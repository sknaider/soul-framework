from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import MethodType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from soul_framework import Soul
from soul_framework.config import SoulConfig
from soul_framework.integrity import (
    CheckpointFile,
    Ed25519CheckpointSigner,
    Ed25519TrustStore,
    InMemoryMonotonicWitness,
    IntegrityVerificationError,
    SQLiteMemoryIntegrityGuard,
    WitnessConflictError,
    WitnessState,
)


async def _database(path: Path) -> None:
    async with Soul.create("ADA", backend="sqlite", backend_url=str(path)) as soul:
        await soul.memory.store("William prefiere evidencia verificable", importance=10)
        await soul.memory.store(
            "El alma permanece aunque cambie el cerebro", importance=9
        )


def _guard(path: Path, checkpoint: Path, witness: InMemoryMonotonicWitness):
    private_key = Ed25519PrivateKey.generate()
    signer = Ed25519CheckpointSigner("test-root", private_key)
    trust = Ed25519TrustStore({"test-root": private_key.public_key()})
    guard = SQLiteMemoryIntegrityGuard(
        database=path,
        agent="ADA",
        stream_id="soul:ADA",
        checkpoint_file=CheckpointFile(checkpoint),
        witness=witness,
        trust_store=trust,
        signer=signer,
    )
    return guard, signer, trust


@pytest.mark.asyncio
async def test_clean_checkpoint_is_accepted_and_new_write_fails_closed(tmp_path: Path):
    db = tmp_path / "soul.db"
    await _database(db)
    guard, _, _ = _guard(db, tmp_path / "checkpoint.json", InMemoryMonotonicWitness())
    checkpoint = guard.seal_and_publish()
    assert guard.verify_before_serve() == checkpoint.checkpoint

    connection = sqlite3.connect(db)
    connection.execute("UPDATE memories SET importance=1 WHERE agent='ADA' AND id=1")
    connection.commit()
    connection.close()
    with pytest.raises(IntegrityVerificationError, match="does not match"):
        guard.verify_before_serve()


@pytest.mark.asyncio
async def test_direct_edit_and_local_recompute_cannot_forge_signature(tmp_path: Path):
    db = tmp_path / "soul.db"
    checkpoint_path = tmp_path / "checkpoint.json"
    await _database(db)
    guard, _, _ = _guard(db, checkpoint_path, InMemoryMonotonicWitness())
    guard.seal_and_publish()

    connection = sqlite3.connect(db)
    connection.execute("UPDATE memories SET content='FALSIFICADO' WHERE id=1")
    connection.commit()
    connection.close()
    with pytest.raises(IntegrityVerificationError, match="does not match"):
        guard.verify_before_serve()

    # The attacker controls DB + local envelope and can replace the local head,
    # but cannot produce a valid Ed25519 signature for those new bytes.
    raw = json.loads(checkpoint_path.read_text())
    raw["checkpoint"]["memory_head"] = "a" * 64
    checkpoint_path.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    with pytest.raises(IntegrityVerificationError, match="signature"):
        guard.verify_before_serve()


@pytest.mark.asyncio
async def test_old_valid_signature_is_rejected_by_monotonic_witness(tmp_path: Path):
    db = tmp_path / "soul.db"
    checkpoint_path = tmp_path / "checkpoint.json"
    await _database(db)
    witness = InMemoryMonotonicWitness()
    guard, _, _ = _guard(db, checkpoint_path, witness)
    first = guard.seal_and_publish()
    old_db = tmp_path / "old.db"
    old_checkpoint = tmp_path / "old-checkpoint.json"
    shutil.copy2(db, old_db)
    shutil.copy2(checkpoint_path, old_checkpoint)

    connection = sqlite3.connect(db)
    connection.execute("UPDATE memories SET importance=8 WHERE id=1")
    connection.commit()
    connection.close()
    second = guard.seal_and_publish()
    assert second.checkpoint.sequence == first.checkpoint.sequence + 1
    assert guard.verify_before_serve() == second.checkpoint

    # Coherent rollback of DB + old checkpoint has a perfectly valid old signature.
    shutil.copy2(old_db, db)
    shutil.copy2(old_checkpoint, checkpoint_path)
    with pytest.raises(IntegrityVerificationError, match="stale, forked"):
        guard.verify_before_serve()


@pytest.mark.asyncio
async def test_colocal_witness_can_be_rolled_back_with_the_store(tmp_path: Path):
    """Control proving why the in-memory witness is not a strong external anchor."""
    db = tmp_path / "soul.db"
    checkpoint_path = tmp_path / "checkpoint.json"
    await _database(db)
    witness = InMemoryMonotonicWitness()
    guard, _, _ = _guard(db, checkpoint_path, witness)
    guard.seal_and_publish()
    old_db = tmp_path / "old.db"
    old_checkpoint = tmp_path / "old-checkpoint.json"
    shutil.copy2(db, old_db)
    shutil.copy2(checkpoint_path, old_checkpoint)
    old_witness = witness.read("soul:ADA")

    connection = sqlite3.connect(db)
    connection.execute("UPDATE memories SET importance=8 WHERE id=1")
    connection.commit()
    connection.close()
    guard.seal_and_publish()

    shutil.copy2(old_db, db)
    shutil.copy2(old_checkpoint, checkpoint_path)
    # Complete host compromise includes the co-local test witness.  Rolling all three
    # artifacts back produces an internally valid old state.
    witness._states["soul:ADA"] = old_witness
    assert guard.verify_before_serve().sequence == 1


def test_witness_rejects_sequence_skip_and_broken_link():
    witness = InMemoryMonotonicWitness()
    genesis = WitnessState("soul:ADA", 1, "0" * 64, "1" * 64)
    assert witness.advance("soul:ADA", expected=None, proposed=genesis) == genesis
    with pytest.raises(WitnessConflictError, match="exactly one"):
        witness.advance(
            "soul:ADA",
            expected=genesis,
            proposed=WitnessState("soul:ADA", 3, "1" * 64, "3" * 64),
        )
    with pytest.raises(WitnessConflictError, match="chain is broken"):
        witness.advance(
            "soul:ADA",
            expected=genesis,
            proposed=WitnessState("soul:ADA", 2, "f" * 64, "2" * 64),
        )


@pytest.mark.asyncio
async def test_missing_or_unavailable_witness_fails_closed(tmp_path: Path):
    db = tmp_path / "soul.db"
    checkpoint_path = tmp_path / "checkpoint.json"
    await _database(db)
    witness = InMemoryMonotonicWitness()
    guard, signer, trust = _guard(db, checkpoint_path, witness)
    guard.seal_and_publish()

    empty_witness = InMemoryMonotonicWitness()
    reader = SQLiteMemoryIntegrityGuard(
        database=db,
        agent="ADA",
        stream_id="soul:ADA",
        checkpoint_file=CheckpointFile(checkpoint_path),
        witness=empty_witness,
        trust_store=trust,
        signer=signer,
    )
    with pytest.raises(IntegrityVerificationError, match="witness is unavailable"):
        reader.verify_before_serve()


@pytest.mark.asyncio
async def test_opt_in_module_does_not_change_normal_core_reads(tmp_path: Path):
    db = tmp_path / "soul.db"
    await _database(db)
    async with Soul.create("ADA", backend="sqlite", backend_url=str(db)) as soul:
        assert (await soul.memory.get(1)).content.startswith("William")


@pytest.mark.asyncio
async def test_soul_enforces_guard_and_reseals_after_write(tmp_path: Path):
    db = tmp_path / "soul.db"
    checkpoint = tmp_path / "checkpoint.json"
    await _database(db)
    witness = InMemoryMonotonicWitness()
    guard, _, _ = _guard(db, checkpoint, witness)
    assert guard.seal_and_publish().checkpoint.sequence == 1

    async with Soul.create(
        "ADA", backend="sqlite", backend_url=str(db), integrity_guard=guard
    ) as soul:
        assert (await soul.memory.get(1)).content.startswith("William")
        await soul.memory.store("Nueva memoria sellada", importance=8)
        assert guard.verify_before_serve().sequence == 2

        connection = sqlite3.connect(db)
        connection.execute("UPDATE memories SET content='forged' WHERE id=1")
        connection.commit()
        connection.close()
        with pytest.raises(IntegrityVerificationError, match="does not match"):
            await soul.memory.search("William")


@pytest.mark.asyncio
async def test_verified_read_pins_the_snapshot_across_concurrent_tamper(
    tmp_path: Path,
):
    """A write after verification cannot change the bytes served by that read."""

    db = tmp_path / "soul.db"
    await _database(db)
    guard, _, _ = _guard(db, tmp_path / "checkpoint.json", InMemoryMonotonicWitness())
    guard.seal_and_publish()
    original = guard._verified_checkpoint

    def verify_then_tamper(self, connection):
        checkpoint = original(connection)
        attacker = sqlite3.connect(db, timeout=5.0)
        attacker.execute("UPDATE memories SET content='FORGED_AFTER_VERIFY' WHERE id=1")
        attacker.commit()
        attacker.close()
        return checkpoint

    guard._verified_checkpoint = MethodType(verify_then_tamper, guard)
    row = guard.verified_fetchone("SELECT content FROM memories WHERE id=$1", 1)
    assert row is not None
    assert row["content"] == "William prefiere evidencia verificable"
    guard._verified_checkpoint = original
    with pytest.raises(IntegrityVerificationError, match="does not match"):
        guard.verified_fetchone("SELECT content FROM memories WHERE id=$1", 1)


@pytest.mark.asyncio
async def test_integrity_mode_never_loads_uncommitted_hnsw_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("hnswlib")
    from soul_framework.memory.vector_index import HnswMemoryIndex

    db = tmp_path / "soul.db"
    await _database(db)
    guard, _, _ = _guard(db, tmp_path / "checkpoint.json", InMemoryMonotonicWitness())
    guard.seal_and_publish()

    def reject_load(cls, *args, **kwargs):
        raise AssertionError("integrity mode must rebuild HNSW from verified DB rows")

    monkeypatch.setattr(HnswMemoryIndex, "load", classmethod(reject_load))
    config = SoulConfig(
        backend="sqlite",
        backend_url=str(db),
        memory_vector_index="hnsw",
    )
    async with Soul.create("ADA", config=config, integrity_guard=guard) as soul:
        assert (await soul.memory.get(1)).content.startswith("William")
        assert soul.memory._vector_index_path is None
