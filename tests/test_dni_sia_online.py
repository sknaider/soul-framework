from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import secrets
import socket
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from soul_framework.identity.dni import verify_soul_dni


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "soul_dni_sia_api", TOOLS / "soul_dni_sia_api.py"
)
assert SPEC and SPEC.loader
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def _root_key(path: Path) -> str:
    private = Ed25519PrivateKey.generate()
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(public).hexdigest()


def _authority(tmp_path: Path):
    key = tmp_path / "sia.pem"
    pin = _root_key(key)
    authority = api.SIAOnlineAuthority(
        database=tmp_path / "registry/sia.sqlite3",
        private_key_file=key,
        state_dir=tmp_path / "state",
    )
    return authority, pin


def _device():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(raw).decode("ascii")


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _enrollment_request(token: str, private, public: str, machine: str, binding: str):
    proof = api.signable_enrollment_body(
        enrollment_token=token,
        machine_soul_id=machine,
        machine_binding_sha256=binding,
        device_public_key=public,
        nonce=secrets.token_urlsafe(24),
        timestamp=_timestamp(),
    )
    return {
        **proof,
        "enrollment_token": token,
        "signature": base64.b64encode(
            private.sign(api.canonical_device_proof("enroll", proof))
        ).decode("ascii"),
    }


def _renewal_request(bundle: dict, private, binding: str):
    credential = bundle["credential"]
    request = {
        "soul_dni": bundle["soul_dni"],
        "machine_soul_id": bundle["machine_soul_id"],
        "machine_binding_sha256": binding,
        "expected_sequence": credential["sequence"],
        "nonce": secrets.token_urlsafe(24),
        "timestamp": _timestamp(),
    }
    request["signature"] = base64.b64encode(
        private.sign(api.canonical_device_proof("renew", request))
    ).decode("ascii")
    return request


def _make_renewal_due(authority, soul_dni: str) -> None:
    due = (datetime.now(timezone.utc) - timedelta(days=24)).replace(microsecond=0)
    with authority._connect() as conn:
        conn.execute(
            "UPDATE identities SET renewed_at=? WHERE soul_dni=?",
            (due.isoformat().replace("+00:00", "Z"), soul_dni),
        )


def _write_delivery(tmp_path: Path, bundle: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    credential = tmp_path / "credential.json"
    trust = tmp_path / "trust.json"
    credential.write_text(
        json.dumps(bundle["credential"], sort_keys=True, separators=(",", ":")) + "\n"
    )
    trust.write_text(
        json.dumps(bundle["trust_store"], sort_keys=True, separators=(",", ":")) + "\n"
    )
    credential.chmod(0o600)
    trust.chmod(0o600)
    return credential, trust


def test_online_enrollment_and_renewal_are_accepted_by_core(tmp_path):
    authority, pin = _authority(tmp_path)
    token = authority.create_enrollment_token(label="test machine")
    device_private, device_public = _device()
    machine = str(uuid.uuid4())
    binding = "a" * 64
    first = authority.enroll(
        _enrollment_request(token, device_private, device_public, machine, binding)
    )
    first_credential, first_trust = _write_delivery(tmp_path / "first", first)
    verified_first = verify_soul_dni(
        first_credential,
        first_trust,
        expected_audience="soul-core",
        expected_machine_soul_id=machine,
        expected_machine_binding_sha256=binding,
        expected_trust_store_sha256=first["trust_store_sha256"],
        trusted_issuer_key_sha256={"soul-sia-root-1": pin},
    )
    _make_renewal_due(authority, first["soul_dni"])
    second = authority.renew(_renewal_request(first, device_private, binding))
    second_credential, second_trust = _write_delivery(tmp_path / "second", second)
    verified_second = verify_soul_dni(
        second_credential,
        second_trust,
        expected_audience="soul-platform",
        expected_machine_soul_id=machine,
        expected_machine_binding_sha256=binding,
        expected_trust_store_sha256=second["trust_store_sha256"],
        trusted_issuer_key_sha256={"soul-sia-root-1": pin},
    )
    assert verified_first.soul_dni == verified_second.soul_dni
    assert verified_second.sequence > verified_first.sequence
    assert (
        datetime.fromisoformat(second["expires_at"].replace("Z", "+00:00"))
        - datetime.now(timezone.utc)
    ).days <= 30


def test_enrollment_token_is_one_use_and_only_hash_is_stored(tmp_path):
    authority, _pin = _authority(tmp_path)
    token = authority.create_enrollment_token(label="one use")
    private, public = _device()
    request = _enrollment_request(
        token, private, public, str(uuid.uuid4()), "b" * 64
    )
    authority.enroll(request)
    with pytest.raises(PermissionError, match="already used"):
        authority.enroll(request)
    with authority._connect() as conn:
        stored = conn.execute(
            "SELECT token_sha256 FROM enrollment_tokens"
        ).fetchone()[0]
        schema = " ".join(
            row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        )
    assert token not in stored and token not in schema
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_renewal_rejects_forgery_replay_wrong_machine_and_stale_sequence(tmp_path):
    authority, _pin = _authority(tmp_path)
    token = authority.create_enrollment_token(label="adversarial")
    private, public = _device()
    binding = "c" * 64
    first = authority.enroll(
        _enrollment_request(token, private, public, str(uuid.uuid4()), binding)
    )
    forged_private, _ = _device()
    forged = _renewal_request(first, forged_private, binding)
    with pytest.raises(PermissionError, match="signature"):
        authority.renew(forged)
    wrong_machine = _renewal_request(first, private, binding)
    wrong_machine["machine_soul_id"] = str(uuid.uuid4())
    with pytest.raises(PermissionError, match="machine"):
        authority.renew(wrong_machine)
    valid = _renewal_request(first, private, binding)
    with pytest.raises(PermissionError, match="not due"):
        authority.renew(valid)
    _make_renewal_due(authority, first["soul_dni"])
    second = authority.renew(valid)
    with pytest.raises(PermissionError, match="stale"):
        authority.renew(valid)
    replay = _renewal_request(second, private, binding)
    _make_renewal_due(authority, second["soul_dni"])
    renewed = authority.renew(replay)
    assert renewed["credential"]["sequence"] > second["credential"]["sequence"]
    replay["expected_sequence"] = renewed["credential"]["sequence"]
    replay["signature"] = base64.b64encode(
        private.sign(api.canonical_device_proof("renew", replay))
    ).decode("ascii")
    with pytest.raises(PermissionError, match="replayed"):
        authority.renew(replay)


def test_expired_enrollment_token_and_stale_request_fail_closed(tmp_path):
    authority, _pin = _authority(tmp_path)
    token = authority.create_enrollment_token(label="expired", ttl_minutes=1)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with authority._connect() as conn:
        conn.execute(
            "UPDATE enrollment_tokens SET expires_at=? WHERE token_sha256=?",
            ("2000-01-01T00:00:00Z", digest),
        )
    private, public = _device()
    request = _enrollment_request(
        token, private, public, str(uuid.uuid4()), "d" * 64
    )
    with pytest.raises(PermissionError, match="expired"):
        authority.enroll(request)
    fresh = authority.create_enrollment_token(label="stale")
    stale = api.signable_enrollment_body(
        enrollment_token=fresh,
        machine_soul_id=str(uuid.uuid4()),
        machine_binding_sha256="e" * 64,
        device_public_key=public,
        nonce=secrets.token_urlsafe(24),
        timestamp="2000-01-01T00:00:00Z",
    )
    stale.update(
        enrollment_token=fresh,
        signature=base64.b64encode(
            private.sign(api.canonical_device_proof("enroll", stale))
        ).decode("ascii"),
    )
    with pytest.raises(PermissionError, match="stale"):
        authority.enroll(stale)


def test_non_loopback_authority_refuses_plaintext_transport(tmp_path, monkeypatch):
    key = tmp_path / "sia.pem"
    _root_key(key)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "soul-dni-sia-api",
            "--database",
            str(tmp_path / "registry.sqlite3"),
            "--private-key",
            str(key),
            "--state-dir",
            str(tmp_path / "state"),
            "serve",
            "--bind",
            "0.0.0.0",
        ],
    )
    with pytest.raises(SystemExit, match="requires TLS"):
        api.main()


def test_tailnet_http_allowance_is_ip_literal_and_cannot_expand_to_lan_or_dns():
    assert api._is_tailnet_ip("100.75.201.110") is True
    for value in (
        "100.75.201.111",
        "fd7a:115c:a1e0::1",
        "192.168.68.200",
        "10.0.0.1",
        "spark-2cdf.tail018bcc.ts.net",
        "0.0.0.0",
        "::",
    ):
        assert api._is_tailnet_ip(value) is False


def test_slow_headers_time_out_and_release_bounded_worker_pool(monkeypatch):
    monkeypatch.setattr(api, "_READ_TIMEOUT_SECONDS", 0.15)
    server = api._BoundedThreadingHTTPServer(
        ("127.0.0.1", 0), api._Handler, max_workers=2
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    sockets = []
    try:
        for _ in range(2):
            client = socket.create_connection(server.server_address, timeout=1)
            client.sendall(b"POST /v1/dni/enroll HTTP/1.1\r\nX-Slow:")
            sockets.append(client)
        time.sleep(0.3)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/health", timeout=1
        ) as response:
            assert response.status == 200
            assert json.loads(response.read())["ok"] is True
    finally:
        for client in sockets:
            client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)


def test_bounded_server_rejects_request_when_all_worker_slots_are_taken():
    server = object.__new__(api._BoundedThreadingHTTPServer)
    server._worker_slots = api.threading.BoundedSemaphore(1)
    assert server._worker_slots.acquire(blocking=False)

    class Request:
        closed = False

        def close(self):
            self.closed = True

    request = Request()
    server.process_request(request, ("127.0.0.1", 1))
    assert request.closed is True
    server._worker_slots.release()
