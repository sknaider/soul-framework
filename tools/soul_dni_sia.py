#!/usr/bin/env python3
"""Offline SOUL Identity Authority issuer for device-bound DNI credentials.

This operational tool is not imported by SOUL Core and is not installed in the
runtime wheel.  It requires an existing external Ed25519 private key; it never
generates, copies, or emits that private key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature

from soul_framework.identity.dni import (
    canonical_credential_bytes,
    canonical_trust_store_bytes,
    current_machine_binding_sha256,
    generate_soul_id,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _strict_object_bytes(path: Path, label: str) -> tuple[dict, bytes]:
    """Read one protected authority document once and reject ambiguous JSON."""

    path = path.expanduser()
    if not path.is_absolute():
        raise PermissionError(f"{label} must be an absolute regular file")
    _reject_symlink_components(path, label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PermissionError(f"{label} must be an absolute regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError(f"{label} must be a regular file")
        if os.name != "nt" and (
            info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022
        ):
            raise PermissionError(
                f"{label} must be owner/root controlled and not group/world writable"
            )
        if info.st_size > 64 * 1024:
            raise ValueError(f"{label} exceeds 64 KiB")
        data = b""
        while len(data) <= 64 * 1024:
            chunk = os.read(descriptor, 64 * 1024 + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > 64 * 1024:
            raise ValueError(f"{label} exceeds 64 KiB")
    finally:
        os.close(descriptor)

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
        raise ValueError(f"{label} must be a JSON object")
    return value, data


@contextmanager
def _authority_state_lock(state_dir: Path):
    state_dir = state_dir.expanduser()
    if not state_dir.is_absolute():
        raise PermissionError("SIA state directory must be absolute")
    existing = state_dir
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    _reject_symlink_components(existing, "SIA state directory")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_components(state_dir, "SIA state directory")
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise PermissionError("SIA state directory must be a real directory")
    if os.name != "nt":
        os.chmod(state_dir, 0o700)
    lock_path = state_dir / ".head.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield state_dir
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _trust_generation_digest(trust: dict) -> str:
    return hashlib.sha256(
        json.dumps(trust, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _advance_authority_head(
    *,
    state_dir: Path,
    private: Ed25519PrivateKey,
    issuer: str,
    issuer_key_id: str,
    expected_previous: dict | None,
    next_trust: dict,
) -> None:
    """CAS one signed trust generation onto the authority's canonical head."""

    with _authority_state_lock(state_dir) as protected_dir:
        head_path = protected_dir / "trust-head.json"
        current = (
            _verified_previous_trust(
                head_path, private, issuer=issuer, issuer_key_id=issuer_key_id
            )
            if head_path.exists()
            else None
        )
        if (current is None) != (expected_previous is None):
            raise RuntimeError("SIA trust head changed; stale generation rejected")
        if current is not None and expected_previous is not None:
            if not hmac.compare_digest(
                _trust_generation_digest(current),
                _trust_generation_digest(expected_previous),
            ):
                raise RuntimeError("SIA trust head changed; replay/fork rejected")
        expected_sequence = 1 if current is None else int(current["sequence"]) + 1
        if int(next_trust.get("sequence", 0)) != expected_sequence:
            raise RuntimeError("SIA trust generation is not the canonical successor")
        payload = (
            json.dumps(next_trust, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".trust-head.", dir=protected_dir
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, head_path)
            directory_fd = os.open(protected_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _verified_previous_trust(
    path: Path, private: Ed25519PrivateKey, *, issuer: str, issuer_key_id: str
) -> dict:
    trust, _raw = _strict_object_bytes(path, "previous SIA trust snapshot")
    if (
        trust.get("schema") != "soul.dni.trust.v1"
        or trust.get("issuer") != issuer
        or trust.get("signing_key_id") != issuer_key_id
    ):
        raise ValueError("previous trust snapshot belongs to another authority")
    expected_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    try:
        encoded_public = trust["keys"][issuer_key_id]
        if base64.b64decode(encoded_public, validate=True) != expected_public:
            raise ValueError("previous trust snapshot uses another authority key")
        private.public_key().verify(
            base64.b64decode(trust["signature"], validate=True),
            canonical_trust_store_bytes(trust),
        )
    except (KeyError, TypeError, InvalidSignature, ValueError) as exc:
        raise ValueError("previous trust snapshot signature is invalid") from exc
    if not isinstance(trust.get("sequence"), int) or trust["sequence"] < 1:
        raise ValueError("previous trust snapshot sequence is invalid")
    for field in ("revoked_key_ids", "revoked_soul_dnis"):
        if not isinstance(trust.get(field), list) or not all(
            isinstance(item, str) for item in trust[field]
        ):
            raise ValueError(f"previous trust snapshot {field} is invalid")
    return trust


def _signed_trust_snapshot(
    *,
    private: Ed25519PrivateKey,
    issuer: str,
    issuer_key_id: str,
    sequence: int,
    lifetime_days: int,
    revoked_key_ids: set[str],
    revoked_soul_dnis: set[str],
) -> dict:
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    trust = {
        "schema": "soul.dni.trust.v1",
        "issuer": issuer,
        "keys": {issuer_key_id: base64.b64encode(public).decode("ascii")},
        "signing_key_id": issuer_key_id,
        "sequence": sequence,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=lifetime_days)).isoformat().replace(
            "+00:00", "Z"
        ),
        "revoked_key_ids": sorted(revoked_key_ids),
        "revoked_soul_dnis": sorted(revoked_soul_dnis),
    }
    trust["signature"] = base64.b64encode(
        private.sign(canonical_trust_store_bytes(trust))
    ).decode("ascii")
    return trust


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject path substitution anywhere between the filesystem root and path."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PermissionError(f"{label} path must not contain symlinks")


def _authority_key(path: Path) -> Ed25519PrivateKey:
    path = path.expanduser()
    if not path.is_absolute():
        raise PermissionError("SIA private key must be an absolute regular file")
    _reject_symlink_components(path, "SIA private key")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PermissionError("SIA private key must be an absolute regular file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError("SIA private key must be a regular file")
        if os.name != "nt":
            owner_only = info.st_uid in {0, os.getuid()} and not (
                info.st_mode & 0o077
            )
            # systemd exposes LoadCredential files from a private, immutable
            # mount as root:root 0440.  Accept that exact delivery boundary,
            # while retaining owner-only semantics everywhere else.
            systemd_credential = (
                info.st_uid == 0
                and str(path).startswith("/run/credentials/")
                and not (info.st_mode & 0o337)
                and bool(info.st_mode & stat.S_IRGRP)
            )
            if not (owner_only or systemd_credential):
                raise PermissionError("SIA private key must be owner/root-only")
        if info.st_size > 64 * 1024:
            raise ValueError("SIA private key exceeds 64 KiB")
        payload = b""
        while len(payload) <= 64 * 1024:
            chunk = os.read(descriptor, 64 * 1024 + 1 - len(payload))
            if not chunk:
                break
            payload += chunk
        if len(payload) > 64 * 1024:
            raise ValueError("SIA private key exceeds 64 KiB")
    finally:
        os.close(descriptor)
    key = serialization.load_pem_private_key(payload, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("SIA private key must be Ed25519")
    return key


def _atomic_new(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def issue(
    *,
    private_key_file: Path,
    out_dir: Path,
    machine_soul_id: str,
    machine_binding_sha256: str,
    issuer: str = "SOUL Identity Authority",
    issuer_key_id: str = "soul-sia-root-1",
    soul_id: str | None = None,
    lifetime_days: int = 30,
    sequence: int = 1,
    state_dir: Path,
    previous_trust_store: Path | None = None,
) -> dict[str, str]:
    """Issue one active, renewable credential for Core and Platform."""

    if not 1 <= lifetime_days <= 30:
        raise ValueError("DNI credential lifetime must be 1..30 days")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("DNI sequence must be a positive integer")
    if not _SHA256_RE.fullmatch(machine_binding_sha256):
        raise ValueError("machine binding must be a lowercase SHA-256 digest")
    machine_soul_id = str(uuid.UUID(machine_soul_id))
    soul_id = soul_id or generate_soul_id()
    parsed_soul_id = uuid.UUID(soul_id)
    if parsed_soul_id.version != 7 or str(parsed_soul_id) != soul_id:
        raise ValueError("SOUL identity must be a canonical UUIDv7")
    if not issuer.strip() or not issuer_key_id.strip():
        raise ValueError("issuer and issuer key id are required")

    out_dir = out_dir.expanduser()
    if not out_dir.is_absolute():
        raise ValueError("output directory must be absolute and not a symlink")
    # Check the existing prefix before mkdir and the complete path afterwards.
    existing = out_dir
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    _reject_symlink_components(existing, "SIA output directory")
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_components(out_dir, "SIA output directory")
    if out_dir.is_symlink() or not out_dir.is_dir():
        raise ValueError("output directory must be an absolute real directory")
    if os.name != "nt":
        os.chmod(out_dir, 0o700)
    private = _authority_key(private_key_file)
    previous = None
    if previous_trust_store is not None:
        previous = _verified_previous_trust(
            previous_trust_store,
            private,
            issuer=issuer,
            issuer_key_id=issuer_key_id,
        )
        if sequence != int(previous["sequence"]) + 1:
            raise ValueError("renewal trust sequence must advance by exactly one")
        if f"urn:soul:agent:{soul_id}" in set(previous["revoked_soul_dnis"]):
            raise PermissionError("cannot issue a credential for a revoked SOUL DNI")
        if issuer_key_id in set(previous["revoked_key_ids"]):
            raise PermissionError("cannot issue with a revoked SIA signing key")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    trust = _signed_trust_snapshot(
        private=private,
        issuer=issuer,
        issuer_key_id=issuer_key_id,
        sequence=sequence,
        lifetime_days=lifetime_days,
        revoked_key_ids=set(previous["revoked_key_ids"]) if previous else set(),
        revoked_soul_dnis=set(previous["revoked_soul_dnis"]) if previous else set(),
    )
    credential = {
        "schema": "soul.dni.credential.v1",
        "issuer": issuer,
        "issuer_key_id": issuer_key_id,
        "soul_dni": f"urn:soul:agent:{soul_id}",
        "soul_id": soul_id,
        "machine_soul_id": machine_soul_id,
        "machine_binding_sha256": machine_binding_sha256,
        "lifecycle_state": "active",
        "sequence": sequence,
        "trust_sequence": sequence,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=lifetime_days)).isoformat().replace(
            "+00:00", "Z"
        ),
        "audience": ["soul-core", "soul-platform"],
    }
    credential["signature"] = base64.b64encode(
        private.sign(canonical_credential_bytes(credential))
    ).decode("ascii")
    trust_bytes = (json.dumps(trust, sort_keys=True, separators=(",", ":")) + "\n").encode()
    credential_bytes = (
        json.dumps(credential, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    trust_file = out_dir / "soul-dni-trust.json"
    credential_file = out_dir / "soul-dni.json"
    if trust_file.exists() or credential_file.exists():
        raise FileExistsError("SIA issuance output already exists")
    _advance_authority_head(
        state_dir=state_dir,
        private=private,
        issuer=issuer,
        issuer_key_id=issuer_key_id,
        expected_previous=previous,
        next_trust=trust,
    )
    _atomic_new(trust_file, trust_bytes)
    try:
        _atomic_new(credential_file, credential_bytes)
    except Exception:
        trust_file.unlink(missing_ok=True)
        raise
    return {
        "soul_dni": credential["soul_dni"],
        "machine_soul_id": machine_soul_id,
        "credential": str(credential_file),
        "trust_store": str(trust_file),
        "trust_store_sha256": hashlib.sha256(trust_bytes).hexdigest(),
    }


def revoke(
    *,
    private_key_file: Path,
    previous_trust_store: Path,
    out_file: Path,
    revoke_soul_dnis: tuple[str, ...] = (),
    revoke_key_ids: tuple[str, ...] = (),
    issuer: str = "SOUL Identity Authority",
    issuer_key_id: str = "soul-sia-root-1",
    lifetime_days: int = 30,
    state_dir: Path,
) -> dict[str, str | int]:
    """Append revocations to a signed trust generation; removals are impossible."""

    if not 1 <= lifetime_days <= 30:
        raise ValueError("DNI trust lifetime must be 1..30 days")
    if not revoke_soul_dnis and not revoke_key_ids:
        raise ValueError("at least one DNI or key must be revoked")
    private = _authority_key(private_key_file)
    previous = _verified_previous_trust(
        previous_trust_store,
        private,
        issuer=issuer,
        issuer_key_id=issuer_key_id,
    )
    revoked_dnis = set(previous["revoked_soul_dnis"])
    for soul_dni in revoke_soul_dnis:
        if not re.fullmatch(r"urn:soul:agent:[0-9a-f-]{36}", soul_dni):
            raise ValueError("revoked SOUL DNI is invalid")
        revoked_dnis.add(soul_dni)
    revoked_keys = set(previous["revoked_key_ids"])
    revoked_keys.update(revoke_key_ids)
    trust = _signed_trust_snapshot(
        private=private,
        issuer=issuer,
        issuer_key_id=issuer_key_id,
        sequence=int(previous["sequence"]) + 1,
        lifetime_days=lifetime_days,
        revoked_key_ids=revoked_keys,
        revoked_soul_dnis=revoked_dnis,
    )
    payload = (json.dumps(trust, sort_keys=True, separators=(",", ":")) + "\n").encode()
    out_file = out_file.expanduser()
    if not out_file.is_absolute():
        raise ValueError("revocation output must be an absolute path")
    out_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_components(out_file.parent, "revocation output")
    if out_file.exists() or out_file.is_symlink():
        raise FileExistsError(out_file)
    _advance_authority_head(
        state_dir=state_dir,
        private=private,
        issuer=issuer,
        issuer_key_id=issuer_key_id,
        expected_previous=previous,
        next_trust=trust,
    )
    _atomic_new(out_file, payload)
    return {
        "trust_store": str(out_file),
        "trust_store_sha256": hashlib.sha256(payload).hexdigest(),
        "sequence": int(trust["sequence"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="soul-dni-sia")
    actions = parser.add_subparsers(dest="action", required=True)
    issue_parser = actions.add_parser("issue")
    issue_parser.add_argument("--private-key", type=Path, required=True)
    issue_parser.add_argument("--out-dir", type=Path, required=True)
    issue_parser.add_argument("--machine-soul-id", required=True)
    issue_parser.add_argument("--machine-binding-sha256")
    issue_parser.add_argument("--bind-local-machine", action="store_true")
    issue_parser.add_argument("--soul-id")
    issue_parser.add_argument("--issuer", default="SOUL Identity Authority")
    issue_parser.add_argument("--issuer-key-id", default="soul-sia-root-1")
    issue_parser.add_argument("--lifetime-days", type=int, default=30)
    issue_parser.add_argument("--sequence", type=int, default=1)
    issue_parser.add_argument("--previous-trust-store", type=Path)
    issue_parser.add_argument("--state-dir", type=Path, required=True)
    revoke_parser = actions.add_parser("revoke")
    revoke_parser.add_argument("--private-key", type=Path, required=True)
    revoke_parser.add_argument("--previous-trust-store", type=Path, required=True)
    revoke_parser.add_argument("--out-file", type=Path, required=True)
    revoke_parser.add_argument("--soul-dni", action="append", default=[])
    revoke_parser.add_argument("--issuer-key-id-to-revoke", action="append", default=[])
    revoke_parser.add_argument("--issuer", default="SOUL Identity Authority")
    revoke_parser.add_argument("--issuer-key-id", default="soul-sia-root-1")
    revoke_parser.add_argument("--lifetime-days", type=int, default=30)
    revoke_parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "revoke":
        print(
            json.dumps(
                revoke(
                    private_key_file=args.private_key,
                    previous_trust_store=args.previous_trust_store,
                    out_file=args.out_file,
                    revoke_soul_dnis=tuple(args.soul_dni),
                    revoke_key_ids=tuple(args.issuer_key_id_to_revoke),
                    issuer=args.issuer,
                    issuer_key_id=args.issuer_key_id,
                    lifetime_days=args.lifetime_days,
                    state_dir=args.state_dir,
                ),
                sort_keys=True,
            )
        )
        return 0
    binding = args.machine_binding_sha256
    if args.bind_local_machine:
        if binding:
            raise SystemExit("choose --bind-local-machine or --machine-binding-sha256")
        binding = current_machine_binding_sha256()
    if not binding:
        raise SystemExit("machine binding is required")
    print(
        json.dumps(
            issue(
                private_key_file=args.private_key,
                out_dir=args.out_dir,
                machine_soul_id=args.machine_soul_id,
                machine_binding_sha256=binding,
                issuer=args.issuer,
                issuer_key_id=args.issuer_key_id,
                soul_id=args.soul_id,
                lifetime_days=args.lifetime_days,
                sequence=args.sequence,
                previous_trust_store=args.previous_trust_store,
                state_dir=args.state_dir,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
