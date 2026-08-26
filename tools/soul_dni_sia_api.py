#!/usr/bin/env python3
"""Online SOUL Identity Authority (SIA) enrollment and renewal service.

The runtime wheel deliberately excludes this module.  It belongs on a
separately administered authority host with an external Ed25519 root key.
Enrollment is authorized by a one-use bearer token stored only as SHA-256;
renewal requires proof of possession of the device Ed25519 key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soul_dni_sia import issue  # noqa: E402


_PROOF_DOMAIN = b"SOUL-DNI-DEVICE-PROOF-V1\0"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_BODY = 32 * 1024
_CLOCK_SKEW_SECONDS = 300
_READ_TIMEOUT_SECONDS = 5.0
_MAX_CONCURRENT_REQUESTS = 32
_RENEW_BEFORE_DAYS = 7
_SOUL_SIA_TAILNET_IP = ipaddress.ip_address("100.75.201.110")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.microsecond:
        raise ValueError("timestamp must not contain fractional seconds")
    return parsed


def _is_tailnet_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address == _SOUL_SIA_TAILNET_IP


def canonical_device_proof(action: str, body: dict[str, Any]) -> bytes:
    """Canonical bytes signed by a device for enrollment or renewal."""

    if action not in {"enroll", "renew"}:
        raise ValueError("unsupported proof action")
    fields = (
        (
            "machine_soul_id",
            "machine_binding_sha256",
            "device_public_key",
            "enrollment_token_sha256",
            "nonce",
            "timestamp",
        )
        if action == "enroll"
        else (
            "soul_dni",
            "machine_soul_id",
            "machine_binding_sha256",
            "expected_sequence",
            "nonce",
            "timestamp",
        )
    )
    payload = {name: body.get(name) for name in fields}
    return _PROOF_DOMAIN + action.encode("ascii") + b"\0" + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def signable_enrollment_body(
    *,
    enrollment_token: str,
    machine_soul_id: str,
    machine_binding_sha256: str,
    device_public_key: str,
    nonce: str,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "machine_soul_id": machine_soul_id,
        "machine_binding_sha256": machine_binding_sha256,
        "device_public_key": device_public_key,
        "enrollment_token_sha256": hashlib.sha256(
            enrollment_token.encode("utf-8")
        ).hexdigest(),
        "nonce": nonce,
        "timestamp": timestamp,
    }


def _strict_object(data: bytes) -> dict[str, Any]:
    if len(data) > _MAX_BODY:
        raise ValueError("request exceeds 32 KiB")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant: {token}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def _decode_public(value: object) -> tuple[Ed25519PublicKey, bytes]:
    if not isinstance(value, str):
        raise ValueError("device public key is required")
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise ValueError("device public key must be Ed25519")
    return Ed25519PublicKey.from_public_bytes(raw), raw


def _verify_signature(public: Ed25519PublicKey, value: object, message: bytes) -> None:
    if not isinstance(value, str):
        raise PermissionError("device proof signature is required")
    try:
        signature = base64.b64decode(value, validate=True)
        public.verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("device proof signature is invalid") from exc


class SIAOnlineAuthority:
    """Transactional identity registry around the external offline signer."""

    def __init__(
        self,
        *,
        database: Path,
        private_key_file: Path,
        state_dir: Path,
        lifetime_days: int = 30,
        issuer: str = "SOUL Identity Authority",
        issuer_key_id: str = "soul-sia-root-1",
    ) -> None:
        if not 1 <= lifetime_days <= 30:
            raise ValueError("online SIA lifetime must be 1..30 days")
        self.database = database.expanduser()
        self.private_key_file = private_key_file.expanduser()
        self.state_dir = state_dir.expanduser()
        for label, path in (
            ("registry", self.database),
            ("private key", self.private_key_file),
            ("state directory", self.state_dir),
        ):
            if not path.is_absolute():
                raise ValueError(f"SIA {label} path must be absolute")
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.database.parent, 0o700)
            os.chmod(self.state_dir, 0o700)
        self.lifetime_days = lifetime_days
        self.issuer = issuer
        self.issuer_key_id = issuer_key_id
        self._lock = threading.RLock()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS enrollment_tokens (
                    token_sha256 TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS identities (
                    soul_dni TEXT PRIMARY KEY,
                    soul_id TEXT NOT NULL UNIQUE,
                    machine_soul_id TEXT NOT NULL UNIQUE,
                    machine_binding_sha256 TEXT NOT NULL,
                    device_public_key BLOB NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    credential_sequence INTEGER NOT NULL,
                    trust_sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    renewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_nonces (
                    soul_dni TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (soul_dni, nonce),
                    FOREIGN KEY (soul_dni) REFERENCES identities(soul_dni)
                );
                """
            )
        if os.name != "nt":
            os.chmod(self.database, 0o600)

    def create_enrollment_token(
        self, *, label: str, ttl_minutes: int = 30
    ) -> str:
        if not label.strip() or not 1 <= ttl_minutes <= 24 * 60:
            raise ValueError("token label and ttl 1..1440 minutes are required")
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = _iso(_utc_now() + timedelta(minutes=ttl_minutes))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO enrollment_tokens(token_sha256,label,expires_at) VALUES(?,?,?)",
                (digest, label.strip(), expires),
            )
        return token

    @staticmethod
    def _validate_machine(body: dict[str, Any]) -> tuple[str, str]:
        machine_soul_id = str(uuid.UUID(str(body.get("machine_soul_id", ""))))
        binding = body.get("machine_binding_sha256")
        if not isinstance(binding, str) or not _SHA256_RE.fullmatch(binding):
            raise ValueError("machine binding must be lowercase SHA-256")
        return machine_soul_id, binding

    @staticmethod
    def _validate_fresh_request(body: dict[str, Any]) -> tuple[str, datetime]:
        nonce = body.get("nonce")
        if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
            raise ValueError("request nonce is invalid")
        timestamp = _parse_time(body.get("timestamp"))
        if abs((_utc_now() - timestamp).total_seconds()) > _CLOCK_SKEW_SECONDS:
            raise PermissionError("request timestamp is stale")
        return nonce, timestamp

    def _issue_bundle(
        self,
        *,
        machine_soul_id: str,
        machine_binding_sha256: str,
        soul_id: str | None,
    ) -> dict[str, Any]:
        head = self.state_dir / "trust-head.json"
        previous = head if head.exists() else None
        sequence = 1
        if previous is not None:
            previous_value = _strict_object(previous.read_bytes())
            sequence = int(previous_value["sequence"]) + 1
        with tempfile.TemporaryDirectory(prefix="soul-sia-online-") as directory:
            result = issue(
                private_key_file=self.private_key_file,
                out_dir=Path(directory) / "issued",
                machine_soul_id=machine_soul_id,
                machine_binding_sha256=machine_binding_sha256,
                soul_id=soul_id,
                lifetime_days=self.lifetime_days,
                sequence=sequence,
                previous_trust_store=previous,
                state_dir=self.state_dir,
                issuer=self.issuer,
                issuer_key_id=self.issuer_key_id,
            )
            credential_bytes = Path(result["credential"]).read_bytes()
            trust_bytes = Path(result["trust_store"]).read_bytes()
        credential = _strict_object(credential_bytes)
        trust = _strict_object(trust_bytes)
        return {
            "schema": "soul.dni.delivery.v1",
            "soul_dni": credential["soul_dni"],
            "machine_soul_id": credential["machine_soul_id"],
            "credential": credential,
            "trust_store": trust,
            "trust_store_sha256": hashlib.sha256(trust_bytes).hexdigest(),
            "expires_at": credential["expires_at"],
        }

    def enroll(self, request: dict[str, Any]) -> dict[str, Any]:
        token = request.get("enrollment_token")
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise PermissionError("enrollment token is invalid")
        machine_soul_id, binding = self._validate_machine(request)
        self._validate_fresh_request(request)
        public, public_bytes = _decode_public(request.get("device_public_key"))
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        proof = signable_enrollment_body(
            enrollment_token=token,
            machine_soul_id=machine_soul_id,
            machine_binding_sha256=binding,
            device_public_key=str(request["device_public_key"]),
            nonce=str(request["nonce"]),
            timestamp=str(request["timestamp"]),
        )
        if not hmac.compare_digest(
            str(request.get("enrollment_token_sha256", "")), digest
        ):
            raise PermissionError("enrollment token digest mismatch")
        _verify_signature(public, request.get("signature"), canonical_device_proof("enroll", proof))

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT expires_at,used_at FROM enrollment_tokens WHERE token_sha256=?",
                (digest,),
            ).fetchone()
            if row is None or row["used_at"] is not None:
                raise PermissionError("enrollment token is unknown or already used")
            if _parse_time(row["expires_at"]) <= _utc_now():
                raise PermissionError("enrollment token expired")
            if conn.execute(
                "SELECT 1 FROM identities WHERE machine_soul_id=?", (machine_soul_id,)
            ).fetchone():
                raise PermissionError("machine already owns a SOUL DNI")
            bundle = self._issue_bundle(
                machine_soul_id=machine_soul_id,
                machine_binding_sha256=binding,
                soul_id=None,
            )
            credential = bundle["credential"]
            now = _iso(_utc_now())
            conn.execute(
                """INSERT INTO identities(
                       soul_dni,soul_id,machine_soul_id,machine_binding_sha256,
                       device_public_key,status,credential_sequence,trust_sequence,
                       created_at,renewed_at) VALUES(?,?,?,?,?,'active',?,?,?,?)""",
                (
                    credential["soul_dni"],
                    credential["soul_id"],
                    machine_soul_id,
                    binding,
                    public_bytes,
                    int(credential["sequence"]),
                    int(credential["trust_sequence"]),
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE enrollment_tokens SET used_at=? WHERE token_sha256=? AND used_at IS NULL",
                (now, digest),
            )
            conn.commit()
            return bundle

    def renew(self, request: dict[str, Any]) -> dict[str, Any]:
        soul_dni = request.get("soul_dni")
        if not isinstance(soul_dni, str) or not soul_dni.startswith("urn:soul:agent:"):
            raise ValueError("SOUL DNI is invalid")
        machine_soul_id, binding = self._validate_machine(request)
        nonce, _timestamp = self._validate_fresh_request(request)
        expected = request.get("expected_sequence")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ValueError("expected credential sequence is invalid")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM identities WHERE soul_dni=?", (soul_dni,)
            ).fetchone()
            if row is None or row["status"] != "active":
                raise PermissionError("SOUL DNI is unknown or revoked")
            if row["machine_soul_id"] != machine_soul_id or not hmac.compare_digest(
                row["machine_binding_sha256"], binding
            ):
                raise PermissionError("renewal machine identity mismatch")
            if int(row["credential_sequence"]) != expected:
                raise PermissionError("renewal sequence is stale")
            public = Ed25519PublicKey.from_public_bytes(row["device_public_key"])
            _verify_signature(
                public,
                request.get("signature"),
                canonical_device_proof("renew", request),
            )
            if conn.execute(
                "SELECT 1 FROM request_nonces WHERE soul_dni=? AND nonce=?",
                (soul_dni, nonce),
            ).fetchone():
                raise PermissionError("renewal nonce replayed")
            renewed_at = _parse_time(row["renewed_at"])
            earliest = renewed_at + timedelta(
                days=max(1, self.lifetime_days - _RENEW_BEFORE_DAYS)
            )
            if _utc_now() < earliest:
                raise PermissionError("renewal is not due yet")
            bundle = self._issue_bundle(
                machine_soul_id=machine_soul_id,
                machine_binding_sha256=binding,
                soul_id=row["soul_id"],
            )
            credential = bundle["credential"]
            if credential["soul_dni"] != soul_dni:
                raise RuntimeError("SIA attempted to replace a sovereign identity")
            now = _iso(_utc_now())
            conn.execute(
                "INSERT INTO request_nonces(soul_dni,nonce,seen_at) VALUES(?,?,?)",
                (soul_dni, nonce, now),
            )
            updated = conn.execute(
                """UPDATE identities SET credential_sequence=?, trust_sequence=?,
                       renewed_at=? WHERE soul_dni=? AND credential_sequence=?""",
                (
                    int(credential["sequence"]),
                    int(credential["trust_sequence"]),
                    now,
                    soul_dni,
                    expected,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("renewal compare-and-swap failed")
            conn.commit()
            return bundle


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading server with a hard cap against partial-request exhaustion."""

    daemon_threads = True

    def __init__(self, *args, max_workers: int = _MAX_CONCURRENT_REQUESTS, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        if not self._worker_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class _Handler(BaseHTTPRequestHandler):
    authority: SIAOnlineAuthority
    server_version = "SOUL-SIA/1"

    def setup(self) -> None:
        # BaseHTTPRequestHandler parses the request line and all headers before
        # do_POST.  Apply the deadline here so slow headers cannot occupy the
        # bounded worker pool indefinitely.
        super().setup()
        self.connection.settimeout(_READ_TIMEOUT_SECONDS)

    def _json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            return

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._json(404, {"ok": False, "error": "not_found"})
            return
        self._json(200, {"ok": True, "service": "soul-dni-sia", "max_days": 30})

    def do_POST(self) -> None:  # noqa: N802
        routes = {
            "/v1/dni/enroll": self.authority.enroll,
            "/v1/dni/renew": self.authority.renew,
        }
        action = routes.get(self.path)
        if action is None:
            self._json(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= _MAX_BODY:
                raise ValueError("invalid content length")
            request = _strict_object(self.rfile.read(length))
            self._json(200, {"ok": True, "delivery": action(request)})
        except (socket.timeout, TimeoutError):
            try:
                self._json(408, {"ok": False, "error": "request_timeout"})
            except OSError:
                pass
        except PermissionError as exc:
            self._json(403, {"ok": False, "error": str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception:
            self._json(500, {"ok": False, "error": "internal_error"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(prog="soul-dni-sia-api")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    token = sub.add_parser("create-token")
    token.add_argument("--label", required=True)
    token.add_argument("--ttl-minutes", type=int, default=30)
    serve = sub.add_parser("serve")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8781)
    serve.add_argument("--tls-cert", type=Path)
    serve.add_argument("--tls-key", type=Path)
    serve.add_argument("--allow-tailnet-http", action="store_true")
    args = parser.parse_args()
    authority = SIAOnlineAuthority(
        database=args.database,
        private_key_file=args.private_key,
        state_dir=args.state_dir,
    )
    if args.action == "create-token":
        print(authority.create_enrollment_token(label=args.label, ttl_minutes=args.ttl_minutes))
        return 0
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SystemExit("TLS certificate and key are inseparable")
    if args.bind not in {"127.0.0.1", "::1"} and not args.tls_cert:
        if not args.allow_tailnet_http or not _is_tailnet_ip(args.bind):
            raise SystemExit("non-loopback SIA binding requires TLS or explicit tailnet HTTP")
    _Handler.authority = authority
    server = _BoundedThreadingHTTPServer((args.bind, args.port), _Handler)
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(args.tls_cert), str(args.tls_key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
