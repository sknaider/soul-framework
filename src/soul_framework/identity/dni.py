"""Fail-closed verification for SOUL-issued sovereign DNI credentials.

The issuer is deliberately absent from this package.  SOUL Core consumes a
credential and a pinned public trust store; it never owns the SIA private key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_CREDENTIAL_SCHEMA = "soul.dni.credential.v1"
_TRUST_SCHEMA = "soul.dni.trust.v1"
_SIGNING_DOMAIN = b"SOUL-DNI-CREDENTIAL-V1\0"
_TRUST_SIGNING_DOMAIN = b"SOUL-DNI-TRUST-V1\0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCUMENT_BYTES = 64 * 1024
# Root of trust owned by the SOUL central signer.  Rotation requires a Core
# release (or a future cross-signed root set); installer-provided files cannot
# replace this pin.
_PINNED_SIA_KEY_SHA256 = (
    ("soul-sia-root-1", "fb1f9a652e63ce6b680e8199a7ad2dff26cfa5a74fa67741fdb4f7fff3a07614"),
)


class SoulDNIVerificationError(ValueError):
    """A SOUL runtime cannot establish a valid sovereign identity."""


@dataclass(frozen=True)
class VerifiedSoulDNI:
    soul_dni: str
    soul_id: str
    machine_soul_id: str
    issuer: str
    issuer_key_id: str
    sequence: int
    trust_sequence: int
    expires_at: datetime
    credential_bytes: bytes = field(default=b"", repr=False, compare=False)
    trust_store_bytes: bytes = field(default=b"", repr=False, compare=False)


def generate_soul_id() -> str:
    """Generate a canonical UUIDv7 for a new sovereign SOUL identity."""

    millis = time.time_ns() // 1_000_000
    randomness = secrets.randbits(74)
    value = (
        (millis << 80)
        | (0x7 << 76)
        | ((randomness >> 62) << 64)
        | (0b10 << 62)
        | (randomness & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))


def _strict_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise ValueError("document exceeds 64 KiB")
        def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate JSON key: {key}")
                value[key] = item
            return value

        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {token}")
            ),
        )
    except Exception as exc:
        raise SoulDNIVerificationError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SoulDNIVerificationError(f"{label} must be a JSON object")
    return value


def _protected_document_bytes(
    value: str | os.PathLike[str], label: str
) -> tuple[Path, bytes]:
    """Open a protected document once and return the exact verified bytes.

    Digesting and parsing separate path reads creates a swap race.  The file
    descriptor is therefore opened once (without following the final symlink
    where the OS supports it), checked with ``fstat`` and read exactly once.
    """

    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SoulDNIVerificationError(f"{label} path must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise SoulDNIVerificationError(f"{label} path must not contain symlinks")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SoulDNIVerificationError(f"{label} must be a regular file") from exc
    try:
        import stat

        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SoulDNIVerificationError(f"{label} must be a regular file")
        if info.st_size > _MAX_DOCUMENT_BYTES:
            raise SoulDNIVerificationError(f"{label} exceeds 64 KiB")
        if os.name != "nt" and (
            info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022
        ):
            raise SoulDNIVerificationError(
                f"{label} must be owner/root controlled and not group/world writable"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, _MAX_DOCUMENT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_DOCUMENT_BYTES:
                raise SoulDNIVerificationError(f"{label} exceeds 64 KiB")
        data = b"".join(chunks)
    finally:
        os.close(fd)
    return path, data


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_credential_bytes(credential: dict[str, Any]) -> bytes:
    """Return the exact domain-separated bytes signed by the SOUL SIA."""

    unsigned = dict(credential)
    unsigned.pop("signature", None)
    return _SIGNING_DOMAIN + _canonical(unsigned)


def canonical_trust_store_bytes(trust_store: dict[str, Any]) -> bytes:
    """Return the exact domain-separated bytes signed by the SOUL SIA root."""

    unsigned = dict(trust_store)
    unsigned.pop("signature", None)
    return _TRUST_SIGNING_DOMAIN + _canonical(unsigned)


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SoulDNIVerificationError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SoulDNIVerificationError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise SoulDNIVerificationError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _decode_raw_public_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise SoulDNIVerificationError("issuer public key must be base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise SoulDNIVerificationError("issuer public key is not valid base64") from exc
    if len(raw) != 32:
        raise SoulDNIVerificationError("issuer public key must be Ed25519 raw bytes")
    return raw


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str):
        raise SoulDNIVerificationError("DNI signature must be base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise SoulDNIVerificationError("DNI signature is not valid base64") from exc
    if len(raw) != 64:
        raise SoulDNIVerificationError("DNI signature must be an Ed25519 signature")
    return raw


def _windows_machine_material() -> str:
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            machine_guid, _kind = winreg.QueryValueEx(key, "MachineGuid")
        completed = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        sid_match = re.search(r"S-1-(?:\d+-)+\d+", completed.stdout)
        if completed.returncode != 0 or sid_match is None:
            raise RuntimeError("Windows SID unavailable")
        return f"windows\0{machine_guid}\0{sid_match.group(0)}"
    except Exception as exc:
        raise SoulDNIVerificationError("cannot derive protected Windows machine binding") from exc


def _unix_machine_material() -> str:
    candidates = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
    machine_id = ""
    for path in candidates:
        try:
            machine_id = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if machine_id:
            break
    if not machine_id:
        # macOS does not expose /etc/machine-id.  Its platform UUID is an OS
        # supplied device identifier, not a caller-controlled environment var.
        if platform.system() == "Darwin":
            completed = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', completed.stdout)
            if completed.returncode == 0 and match:
                machine_id = match.group(1)
    if not machine_id:
        raise SoulDNIVerificationError("cannot derive protected machine identifier")
    return f"{platform.system().casefold()}\0{machine_id}\0uid:{os.getuid()}"


def current_machine_binding_sha256() -> str:
    """Bind a DNI to the OS machine and local owner, without exposing either."""

    material = _windows_machine_material() if os.name == "nt" else _unix_machine_material()
    return hashlib.sha256(("SOUL-MACHINE-BINDING-V1\0" + material).encode()).hexdigest()


def verify_soul_dni(
    credential_path: str | os.PathLike[str],
    trust_store_path: str | os.PathLike[str],
    *,
    expected_audience: str,
    expected_machine_soul_id: str | None = None,
    expected_machine_binding_sha256: str | None = None,
    expected_trust_store_sha256: str | None = None,
    trusted_issuer_key_sha256: dict[str, str] | None = None,
    now: datetime | None = None,
    allow_expired: bool = False,
) -> VerifiedSoulDNI:
    """Verify issuer, signature, state, freshness, audience and device binding."""

    _credential_file, credential_bytes = _protected_document_bytes(
        credential_path, "DNI credential"
    )
    _trust_file, trust_bytes = _protected_document_bytes(
        trust_store_path, "DNI trust store"
    )
    if not expected_audience or any(ch in expected_audience for ch in "\x00\r\n"):
        raise SoulDNIVerificationError("expected audience is invalid")
    if expected_trust_store_sha256 is not None:
        if not _SHA256_RE.fullmatch(expected_trust_store_sha256):
            raise SoulDNIVerificationError("pinned trust-store digest is invalid")
        actual = hashlib.sha256(trust_bytes).hexdigest()
        if not hmac.compare_digest(actual, expected_trust_store_sha256):
            raise SoulDNIVerificationError("DNI trust store does not match its pinned digest")

    trust = _strict_json(trust_bytes, "DNI trust store")
    credential = _strict_json(credential_bytes, "DNI credential")
    if trust.get("schema") != _TRUST_SCHEMA:
        raise SoulDNIVerificationError("unsupported DNI trust-store schema")
    if credential.get("schema") != _CREDENTIAL_SCHEMA:
        raise SoulDNIVerificationError("unsupported DNI credential schema")
    if credential.get("issuer") != trust.get("issuer") or not credential.get("issuer"):
        raise SoulDNIVerificationError("DNI issuer is not trusted")

    key_id = credential.get("issuer_key_id")
    keys = trust.get("keys")
    if not isinstance(key_id, str) or not isinstance(keys, dict) or key_id not in keys:
        raise SoulDNIVerificationError("DNI issuer key is not trusted")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise SoulDNIVerificationError(
            "SOUL DNI verification requires the 'cryptography' package"
        ) from exc
    public_raw = _decode_raw_public_key(keys[key_id])
    pinned_keys = dict(
        _PINNED_SIA_KEY_SHA256
        if trusted_issuer_key_sha256 is None
        else trusted_issuer_key_sha256.items()
    )
    expected_key_digest = pinned_keys.get(key_id)
    if (
        expected_key_digest is None
        or not _SHA256_RE.fullmatch(expected_key_digest)
        or not hmac.compare_digest(hashlib.sha256(public_raw).hexdigest(), expected_key_digest)
    ):
        raise SoulDNIVerificationError("DNI issuer key is not pinned by SOUL Core")
    public_key = Ed25519PublicKey.from_public_bytes(public_raw)
    if trust.get("signing_key_id") != key_id:
        raise SoulDNIVerificationError("DNI trust snapshot signer does not match issuer key")
    try:
        public_key.verify(
            _decode_signature(trust.get("signature")),
            canonical_trust_store_bytes(trust),
        )
    except InvalidSignature as exc:
        raise SoulDNIVerificationError("DNI trust snapshot signature is invalid") from exc
    trust_sequence = trust.get("sequence")
    if (
        not isinstance(trust_sequence, int)
        or isinstance(trust_sequence, bool)
        or trust_sequence < 1
    ):
        raise SoulDNIVerificationError("DNI trust snapshot sequence is invalid")
    trust_issued_at = _parse_utc(trust.get("issued_at"), "trust.issued_at")
    trust_expires_at = _parse_utc(trust.get("expires_at"), "trust.expires_at")
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        trust_issued_at > instant
        or (trust_expires_at <= instant and not allow_expired)
        or trust_expires_at <= trust_issued_at
        or trust_expires_at - trust_issued_at > timedelta(days=30)
    ):
        raise SoulDNIVerificationError("DNI trust snapshot is not currently valid")
    revoked_keys = trust.get("revoked_key_ids", [])
    if not isinstance(revoked_keys, list) or key_id in revoked_keys:
        raise SoulDNIVerificationError("DNI issuer key is revoked")
    try:
        public_key.verify(
            _decode_signature(credential.get("signature")),
            canonical_credential_bytes(credential),
        )
    except InvalidSignature as exc:
        raise SoulDNIVerificationError("DNI signature is invalid") from exc

    soul_id = str(credential.get("soul_id") or "")
    soul_dni = str(credential.get("soul_dni") or "")
    try:
        parsed_soul_id = uuid.UUID(soul_id)
        normalized_soul_id = str(parsed_soul_id)
    except ValueError as exc:
        raise SoulDNIVerificationError("DNI soul_id must be a UUID") from exc
    if (
        parsed_soul_id.version != 7
        or parsed_soul_id.variant != uuid.RFC_4122
        or soul_id != normalized_soul_id
        or soul_dni != f"urn:soul:agent:{soul_id}"
    ):
        raise SoulDNIVerificationError("DNI and soul_id do not match canonically")
    if credential.get("lifecycle_state") != "active":
        raise SoulDNIVerificationError("DNI lifecycle is not active")
    revoked_dnis = trust.get("revoked_soul_dnis", [])
    if not isinstance(revoked_dnis, list) or soul_dni in revoked_dnis:
        raise SoulDNIVerificationError("DNI is revoked")

    audience = credential.get("audience")
    if (
        not isinstance(audience, list)
        or not all(isinstance(item, str) for item in audience)
        or expected_audience not in audience
    ):
        raise SoulDNIVerificationError("DNI audience does not authorize this runtime")
    sequence = credential.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise SoulDNIVerificationError("DNI sequence must be a positive integer")
    credential_trust_sequence = credential.get("trust_sequence")
    if (
        not isinstance(credential_trust_sequence, int)
        or isinstance(credential_trust_sequence, bool)
        or credential_trust_sequence < 1
        or trust_sequence < credential_trust_sequence
    ):
        raise SoulDNIVerificationError(
            "DNI credential is not bound to the active trust generation"
        )
    issued_at = _parse_utc(credential.get("issued_at"), "issued_at")
    expires_at = _parse_utc(credential.get("expires_at"), "expires_at")
    if (
        issued_at > instant
        or (expires_at <= instant and not allow_expired)
        or expires_at <= issued_at
    ):
        raise SoulDNIVerificationError("DNI credential is not currently valid")
    if expires_at - issued_at > timedelta(days=30):
        raise SoulDNIVerificationError("DNI credential lifetime exceeds 30 days")

    machine_soul_id = str(credential.get("machine_soul_id") or "")
    try:
        normalized_machine_id = str(uuid.UUID(machine_soul_id))
    except ValueError as exc:
        raise SoulDNIVerificationError("DNI machine_soul_id must be a UUID") from exc
    if machine_soul_id != normalized_machine_id:
        raise SoulDNIVerificationError("DNI machine_soul_id is not canonical")
    if expected_machine_soul_id is not None and machine_soul_id != expected_machine_soul_id:
        raise SoulDNIVerificationError("DNI is bound to another machine soul")
    machine_binding = str(credential.get("machine_binding_sha256") or "")
    expected_binding = expected_machine_binding_sha256 or current_machine_binding_sha256()
    if not _SHA256_RE.fullmatch(expected_binding) or machine_binding != expected_binding:
        raise SoulDNIVerificationError("DNI is bound to another machine/owner")

    return VerifiedSoulDNI(
        soul_dni=soul_dni,
        soul_id=soul_id,
        machine_soul_id=machine_soul_id,
        issuer=str(credential["issuer"]),
        issuer_key_id=key_id,
        sequence=sequence,
        trust_sequence=trust_sequence,
        expires_at=expires_at,
        credential_bytes=credential_bytes,
        trust_store_bytes=trust_bytes,
    )
