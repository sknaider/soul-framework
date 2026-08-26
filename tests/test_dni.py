from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from soul_framework import Soul
from soul_framework.backend.sqlite import SqliteBackend
from soul_framework.config import SoulConfig
from soul_framework.identity.dni import (
    SoulDNIVerificationError,
    canonical_credential_bytes,
    canonical_trust_store_bytes,
    current_machine_binding_sha256,
    generate_soul_id,
    verify_soul_dni,
    VerifiedSoulDNI,
)
from soul_framework.soul import (
    _DNIGatedBackend,
    _DNIGatedIntegrityGuard,
    _DNIGatedPostgresBackend,
)


def _issued(tmp_path: Path, *, binding: str | None = None):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    soul_id = generate_soul_id()
    machine_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    trust = {
        "schema": "soul.dni.trust.v1",
        "issuer": "SOUL Identity Authority",
        "keys": {"sia-test-1": base64.b64encode(public).decode("ascii")},
        "signing_key_id": "sia-test-1",
        "sequence": 1,
        "issued_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "revoked_key_ids": [],
        "revoked_soul_dnis": [],
    }
    trust["signature"] = base64.b64encode(
        private.sign(canonical_trust_store_bytes(trust))
    ).decode("ascii")
    credential = {
        "schema": "soul.dni.credential.v1",
        "issuer": "SOUL Identity Authority",
        "issuer_key_id": "sia-test-1",
        "soul_dni": f"urn:soul:agent:{soul_id}",
        "soul_id": soul_id,
        "machine_soul_id": machine_id,
        "machine_binding_sha256": binding or current_machine_binding_sha256(),
        "lifecycle_state": "active",
        "sequence": 1,
        "trust_sequence": 1,
        "issued_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "audience": ["soul-core", "soul-platform"],
    }
    credential["signature"] = base64.b64encode(
        private.sign(canonical_credential_bytes(credential))
    ).decode("ascii")
    trust_file = tmp_path / "soul-dni-trust.json"
    credential_file = tmp_path / "soul-dni.json"
    trust_file.write_text(json.dumps(trust, sort_keys=True), encoding="utf-8")
    credential_file.write_text(json.dumps(credential, sort_keys=True), encoding="utf-8")
    trust_file.chmod(0o600)
    credential_file.chmod(0o600)
    digest = hashlib.sha256(trust_file.read_bytes()).hexdigest()
    key_pin = hashlib.sha256(public).hexdigest()
    return credential_file, trust_file, digest, credential, trust, machine_id, key_pin, private


def test_valid_soul_issued_dni_is_accepted(tmp_path):
    credential, trust, digest, raw, _trust, machine_id, key_pin, _private = _issued(tmp_path)
    result = verify_soul_dni(
        credential,
        trust,
        expected_audience="soul-core",
        expected_machine_soul_id=machine_id,
        expected_machine_binding_sha256=current_machine_binding_sha256(),
        expected_trust_store_sha256=digest,
        trusted_issuer_key_sha256={"sia-test-1": key_pin},
    )
    assert result.soul_dni == raw["soul_dni"]
    assert result.machine_soul_id == machine_id


def test_trust_digest_and_json_are_verified_from_one_immutable_byte_read(
    tmp_path, monkeypatch
):
    import soul_framework.identity.dni as dni_module

    credential, trust_path, _digest, _raw, trust, machine_id, key_pin, private = _issued(
        tmp_path
    )
    trusted_bytes = trust_path.read_bytes()
    revoked = dict(trust)
    revoked["sequence"] = 2
    revoked["revoked_soul_dnis"] = [json.loads(credential.read_text())["soul_dni"]]
    revoked["signature"] = base64.b64encode(
        private.sign(canonical_trust_store_bytes(revoked))
    ).decode("ascii")
    revoked_bytes = json.dumps(revoked, sort_keys=True).encode("utf-8")
    trust_path.write_bytes(revoked_bytes)
    digest = hashlib.sha256(revoked_bytes).hexdigest()
    real_read = dni_module._protected_document_bytes

    def swap_path_after_single_read(path, label):
        protected_path, exact_bytes = real_read(path, label)
        if label == "DNI trust store":
            protected_path.write_bytes(trusted_bytes)
        return protected_path, exact_bytes

    monkeypatch.setattr(dni_module, "_protected_document_bytes", swap_path_after_single_read)
    with pytest.raises(SoulDNIVerificationError, match="revoked"):
        verify_soul_dni(
            credential,
            trust_path,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=current_machine_binding_sha256(),
            expected_trust_store_sha256=digest,
            trusted_issuer_key_sha256={"sia-test-1": key_pin},
        )


def test_mathematically_valid_self_issued_dni_is_rejected_by_core_root_pin(tmp_path):
    credential, trust, digest, _raw, _trust, machine_id, _key_pin, _private = _issued(tmp_path)
    with pytest.raises(SoulDNIVerificationError, match="not pinned by SOUL Core"):
        verify_soul_dni(
            credential,
            trust,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=current_machine_binding_sha256(),
            expected_trust_store_sha256=digest,
        )


@pytest.mark.parametrize("mutation", ["signature", "state", "audience", "machine"])
def test_dni_tampering_or_copy_fails_closed(tmp_path, mutation):
    credential, trust, digest, raw, _trust, machine_id, key_pin, _private = _issued(tmp_path)
    expected_binding = current_machine_binding_sha256()
    if mutation == "signature":
        raw["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    elif mutation == "state":
        raw["lifecycle_state"] = "candidate"
    elif mutation == "audience":
        raw["audience"] = ["other-runtime"]
    else:
        expected_binding = "b" * 64
    credential.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(SoulDNIVerificationError):
        verify_soul_dni(
            credential,
            trust,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=expected_binding,
            expected_trust_store_sha256=digest,
            trusted_issuer_key_sha256={"sia-test-1": key_pin},
        )


def test_revoked_dni_fails_even_with_valid_signature(tmp_path):
    credential, trust, _digest, raw, trust_raw, machine_id, key_pin, private = _issued(tmp_path)
    trust_raw["revoked_soul_dnis"] = [raw["soul_dni"]]
    trust_raw["sequence"] = 2
    trust_raw["signature"] = base64.b64encode(
        private.sign(canonical_trust_store_bytes(trust_raw))
    ).decode("ascii")
    trust.write_text(json.dumps(trust_raw, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(trust.read_bytes()).hexdigest()
    with pytest.raises(SoulDNIVerificationError, match="revoked"):
        verify_soul_dni(
            credential,
            trust,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=current_machine_binding_sha256(),
            expected_trust_store_sha256=digest,
            trusted_issuer_key_sha256={"sia-test-1": key_pin},
        )


def test_removing_revocation_without_soul_signature_is_rejected(tmp_path):
    credential, trust, _digest, raw, trust_raw, machine_id, key_pin, private = _issued(tmp_path)
    trust_raw["revoked_soul_dnis"] = [raw["soul_dni"]]
    trust_raw["sequence"] = 2
    trust_raw["signature"] = base64.b64encode(
        private.sign(canonical_trust_store_bytes(trust_raw))
    ).decode("ascii")
    trust_raw["revoked_soul_dnis"] = []
    trust.write_text(json.dumps(trust_raw, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(trust.read_bytes()).hexdigest()
    with pytest.raises(SoulDNIVerificationError, match="trust snapshot signature"):
        verify_soul_dni(
            credential,
            trust,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=current_machine_binding_sha256(),
            expected_trust_store_sha256=digest,
            trusted_issuer_key_sha256={"sia-test-1": key_pin},
        )


async def test_persistent_core_requires_dni_before_opening_database(tmp_path, monkeypatch):
    for key in (
        "SOUL_DNI_CREDENTIAL",
        "SOUL_DNI_TRUST_STORE",
        "SOUL_DNI_TRUST_STORE_SHA256",
        "SOUL_DNI_MACHINE_SOUL_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    database = tmp_path / "soul.db"
    with pytest.raises(PermissionError, match="SOUL-issued DNI"):
        await Soul.create("MachineSoul", backend="sqlite", backend_url=str(database))
    assert not database.exists()


async def test_persistent_core_opens_with_valid_dni(tmp_path, monkeypatch):
    credential, trust, digest, _raw, _trust, machine_id, key_pin, _private = _issued(tmp_path)
    monkeypatch.setattr(
        "soul_framework.identity.dni._PINNED_SIA_KEY_SHA256",
        (("sia-test-1", key_pin),),
    )
    database = tmp_path / "soul.db"
    config = SoulConfig(
        backend="sqlite",
        backend_url=str(database),
        dni_credential_path=str(credential),
        dni_trust_store_path=str(trust),
        dni_trust_store_sha256=digest,
        machine_soul_id=machine_id,
    )
    soul = await Soul.create("MachineSoul", config=config)
    await soul.close()
    assert database.is_file()


async def test_database_bound_to_one_dni_rejects_another_valid_soul(
    tmp_path, monkeypatch
):
    (
        credential,
        trust,
        digest,
        raw,
        _trust,
        machine_id,
        key_pin,
        private,
    ) = _issued(tmp_path)
    monkeypatch.setattr(
        "soul_framework.identity.dni._PINNED_SIA_KEY_SHA256",
        (("sia-test-1", key_pin),),
    )
    database = tmp_path / "soul.db"
    first = SoulConfig(
        backend="sqlite",
        backend_url=str(database),
        dni_credential_path=str(credential),
        dni_trust_store_path=str(trust),
        dni_trust_store_sha256=digest,
        machine_soul_id=machine_id,
    )
    async with Soul.create("MachineSoul", config=first) as soul:
        await soul.memory.store("CANARY PRIVATE MEMORY OF SOUL A")

    second_raw = dict(raw)
    second_id = generate_soul_id()
    second_machine_id = str(uuid.uuid4())
    second_raw.update(
        soul_id=second_id,
        soul_dni=f"urn:soul:agent:{second_id}",
        machine_soul_id=second_machine_id,
        sequence=2,
    )
    second_raw["signature"] = base64.b64encode(
        private.sign(canonical_credential_bytes(second_raw))
    ).decode("ascii")
    second_credential = tmp_path / "second-dni.json"
    second_credential.write_text(json.dumps(second_raw, sort_keys=True))
    second_credential.chmod(0o600)
    second = SoulConfig(
        backend="sqlite",
        backend_url=str(database),
        dni_credential_path=str(second_credential),
        dni_trust_store_path=str(trust),
        dni_trust_store_sha256=digest,
        machine_soul_id=second_machine_id,
    )
    with pytest.raises(PermissionError, match="belongs to another sovereign DNI"):
        await Soul.create("MachineSoul", config=second)


async def test_populated_legacy_database_requires_explicit_dni_enrollment(
    tmp_path, monkeypatch
):
    credential, trust, digest, _raw, _trust, machine_id, key_pin, _private = _issued(
        tmp_path
    )
    monkeypatch.setattr(
        "soul_framework.identity.dni._PINNED_SIA_KEY_SHA256",
        (("sia-test-1", key_pin),),
    )
    database = tmp_path / "legacy.db"
    legacy = SqliteBackend(str(database))
    await legacy.initialize()
    await legacy.execute(
        "INSERT INTO identity (agent, personality, updated_at) VALUES ($1, $2, $3)",
        "MachineSoul",
        "legacy",
        datetime.now(timezone.utc).isoformat(),
    )
    await legacy.close()
    config = SoulConfig(
        backend="sqlite",
        backend_url=str(database),
        dni_credential_path=str(credential),
        dni_trust_store_path=str(trust),
        dni_trust_store_sha256=digest,
        machine_soul_id=machine_id,
    )
    with pytest.raises(PermissionError, match="explicit owner-approved DNI enrollment"):
        await Soul.create("MachineSoul", config=config)


class _BackendCanary:
    def __init__(self):
        self.fetches = 0
        self.closed = False

    async def fetchval(self, sql, *params):
        self.fetches += 1
        return 7

    async def close(self):
        self.closed = True


class _PostgresExtensionCanary(_BackendCanary):
    def __init__(self):
        super().__init__()
        self.extension_calls: list[str] = []

    async def insert_memory_with_vector(self, values, vector):
        self.extension_calls.append("insert_memory_with_vector")
        return 11

    async def update_memory_fields(self, memory_id, agent, changes, vector):
        self.extension_calls.append("update_memory_fields")
        return True

    async def search_memory_vectors(self, agent, vector, **kwargs):
        self.extension_calls.append("search_memory_vectors")
        return [{"id": 11}]

    async def insert_procedure_with_vector(self, values, vector):
        self.extension_calls.append("insert_procedure_with_vector")
        return 12

    async def search_procedure_vectors(self, agent, vector, **kwargs):
        self.extension_calls.append("search_procedure_vectors")
        return [{"id": 12}]


class _IntegrityCanary:
    def __init__(self):
        self.calls: list[str] = []

    def __getattr__(self, name):
        def called(*_args, **_kwargs):
            self.calls.append(name)
            return [] if name == "verified_fetchall" else None

        return called


def _verified_until(instant: datetime) -> VerifiedSoulDNI:
    soul_id = generate_soul_id()
    return VerifiedSoulDNI(
        soul_dni=f"urn:soul:agent:{soul_id}",
        soul_id=soul_id,
        machine_soul_id=str(uuid.uuid4()),
        issuer="SOUL Identity Authority",
        issuer_key_id="soul-sia-root-1",
        sequence=1,
        trust_sequence=1,
        expires_at=instant,
    )


async def test_expired_live_core_disconnects_before_database_access():
    inner = _BackendCanary()
    expired = _verified_until(datetime.now(timezone.utc) - timedelta(seconds=1))
    gated = _DNIGatedBackend(
        inner,
        lambda: (_ for _ in ()).throw(SoulDNIVerificationError("expired")),
        expired,
    )
    with pytest.raises(PermissionError, match="renewal required"):
        await gated.fetchval("SELECT secret")
    assert inner.fetches == 0
    await gated.close()
    assert inner.closed is True


async def test_live_core_accepts_fresh_soul_renewal_without_reopening_database():
    inner = _BackendCanary()
    expired = _verified_until(datetime.now(timezone.utc) - timedelta(seconds=1))
    renewed = VerifiedSoulDNI(
        **{
            **expired.__dict__,
            "sequence": 2,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        }
    )
    gated = _DNIGatedBackend(inner, lambda: renewed, expired)
    assert await gated.fetchval("SELECT 7") == 7
    assert inner.fetches == 1


@pytest.mark.parametrize("attack", ["identity-switch", "sequence-rollback"])
async def test_live_core_rejects_sovereign_identity_or_sequence_rollback(attack):
    inner = _BackendCanary()
    current = _verified_until(datetime.now(timezone.utc) - timedelta(seconds=1))
    current = VerifiedSoulDNI(
        **{**current.__dict__, "sequence": 2}
    )
    if attack == "identity-switch":
        malicious = _verified_until(datetime.now(timezone.utc) + timedelta(days=30))
        malicious = VerifiedSoulDNI(
            **{
                **malicious.__dict__,
                "machine_soul_id": current.machine_soul_id,
                "sequence": 3,
            }
        )
    else:
        malicious = VerifiedSoulDNI(
            **{
                **current.__dict__,
                "sequence": 1,
                "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
            }
        )
    gated = _DNIGatedBackend(inner, lambda: malicious, current)
    with pytest.raises(PermissionError, match="renewal required"):
        await gated.fetchval("SELECT secret")
    assert inner.fetches == 0


def test_dni_lifetime_cannot_exceed_thirty_days(tmp_path):
    credential, trust, digest, raw, _trust, machine_id, key_pin, private = _issued(tmp_path)
    issued = datetime.fromisoformat(raw["issued_at"].replace("Z", "+00:00"))
    raw["expires_at"] = (issued + timedelta(days=30, seconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    raw["signature"] = base64.b64encode(
        private.sign(canonical_credential_bytes(raw))
    ).decode("ascii")
    credential.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(SoulDNIVerificationError, match="lifetime exceeds 30 days"):
        verify_soul_dni(
            credential,
            trust,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_id,
            expected_machine_binding_sha256=current_machine_binding_sha256(),
            expected_trust_store_sha256=digest,
            trusted_issuer_key_sha256={"sia-test-1": key_pin},
        )


async def test_postgres_vector_extensions_remain_live_behind_dni_gate():
    inner = _PostgresExtensionCanary()
    current = _verified_until(datetime.now(timezone.utc) + timedelta(days=1))
    gated = _DNIGatedPostgresBackend(inner, lambda: current, current)
    assert await gated.insert_memory_with_vector(("v",), [1.0]) == 11
    assert await gated.update_memory_fields(11, "ADA", {"content": "x"}, [1.0])
    assert await gated.search_memory_vectors("ADA", [1.0]) == [{"id": 11}]
    assert await gated.insert_procedure_with_vector(("v",), [1.0]) == 12
    assert await gated.search_procedure_vectors("ADA", [1.0]) == [{"id": 12}]
    assert inner.extension_calls == [
        "insert_memory_with_vector",
        "update_memory_fields",
        "search_memory_vectors",
        "insert_procedure_with_vector",
        "search_procedure_vectors",
    ]


@pytest.mark.parametrize(
    "operation",
    [
        "insert_memory_with_vector",
        "update_memory_fields",
        "search_memory_vectors",
        "insert_procedure_with_vector",
        "search_procedure_vectors",
    ],
)
async def test_expired_dni_blocks_every_postgres_vector_extension(operation):
    inner = _PostgresExtensionCanary()
    expired = _verified_until(datetime.now(timezone.utc) - timedelta(seconds=1))
    gated = _DNIGatedPostgresBackend(
        inner,
        lambda: (_ for _ in ()).throw(SoulDNIVerificationError("expired")),
        expired,
    )
    calls = {
        "insert_memory_with_vector": lambda: gated.insert_memory_with_vector(("v",), [1.0]),
        "update_memory_fields": lambda: gated.update_memory_fields(
            1, "ADA", {"content": "x"}, [1.0]
        ),
        "search_memory_vectors": lambda: gated.search_memory_vectors("ADA", [1.0]),
        "insert_procedure_with_vector": lambda: gated.insert_procedure_with_vector(
            ("v",), [1.0]
        ),
        "search_procedure_vectors": lambda: gated.search_procedure_vectors(
            "ADA", [1.0]
        ),
    }
    with pytest.raises(PermissionError, match="renewal required"):
        await calls[operation]()
    assert inner.extension_calls == []


@pytest.mark.parametrize(
    "operation,args,kwargs",
    [
        ("verify_before_serve", (), {}),
        ("seal_and_publish", (), {}),
        ("verified_fetchall", ("SELECT secret",), {}),
        ("verified_fetchone", ("SELECT secret",), {}),
        ("verified_fetchval", ("SELECT secret",), {}),
        ("mutate_and_publish", ("UPDATE secret",), {"mode": "rowcount"}),
    ],
)
def test_expired_dni_blocks_every_direct_integrity_guard_path(
    operation, args, kwargs
):
    inner = _IntegrityCanary()
    gated = _DNIGatedIntegrityGuard(
        inner,
        lambda: (_ for _ in ()).throw(
            PermissionError("SOUL DNI renewal required")
        ),
    )
    with pytest.raises(PermissionError, match="renewal required"):
        getattr(gated, operation)(*args, **kwargs)
    assert inner.calls == []
