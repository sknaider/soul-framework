from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import stat
import struct

import pytest

from soul_framework.backend.schema import SCHEMA_SQL
from soul_framework.embedding.simple import SimpleEmbedding
from soul_framework.embedding_migration import (
    main,
    migrate_sqlite_embeddings,
    rollback_sqlite_migration,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path, memories=5, procedures=2):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    old = struct.pack("<128f", *([0.25] * 128))
    for idx in range(memories):
        conn.execute(
            "INSERT INTO memories(agent,category,content,embedding,valid_from,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("Maya", "fact", f"memory {idx}", old, "2026-01-01", "2026-01-01"),
        )
    for idx in range(procedures):
        conn.execute(
            "INSERT INTO procedural_memories(agent,task_description,workflow,embedding,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            ("Maya", f"task {idx}", f"step {idx}", old, "2026-01-01", "2026-01-01"),
        )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    _source(source)
    before = _sha(source)
    result = await migrate_sqlite_embeddings(
        source,
        SimpleEmbedding(1024),
        candidate=candidate,
        checkpoint=checkpoint,
        dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["plan"]["rows"] == {"memories": 5, "procedural_memories": 2}
    assert not candidate.exists()
    assert not checkpoint.exists()
    assert _sha(source) == before


@pytest.mark.asyncio
async def test_batch_checkpoint_resume_and_source_immutable(tmp_path):
    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    _source(source, memories=5, procedures=2)
    before = _sha(source)
    paused = await migrate_sqlite_embeddings(
        source,
        SimpleEmbedding(1024),
        candidate=candidate,
        checkpoint=checkpoint,
        batch_size=2,
        max_batches=1,
        provider_name="test-1024",
    )
    assert paused["status"] == "paused"
    assert paused["tables"]["memories"]["completed_rows"] == 2
    assert stat.S_IMODE(candidate.stat().st_mode) == 0o600
    completed = await migrate_sqlite_embeddings(
        source,
        SimpleEmbedding(1024),
        candidate=candidate,
        checkpoint=checkpoint,
        batch_size=2,
        resume=True,
        provider_name="test-1024",
    )
    assert completed["status"] == "completed"
    assert _sha(source) == before
    with sqlite3.connect(candidate) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE length(embedding)=4096"
            ).fetchone()[0]
            == 5
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM procedural_memories WHERE length(embedding)=4096"
            ).fetchone()[0]
            == 2
        )
        assert conn.execute(
            "SELECT group_concat(content,'|') FROM memories"
        ).fetchone()[0] == ("memory 0|memory 1|memory 2|memory 3|memory 4")


@pytest.mark.asyncio
async def test_bad_provider_shape_keeps_resumable_checkpoint(tmp_path):
    class BadProvider:
        dimensions = 1024

        async def embed_batch(self, texts):
            return [[0.0] * 7 for _ in texts]

    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    _source(source)
    before = _sha(source)
    with pytest.raises(ValueError, match="invalid batch shape"):
        await migrate_sqlite_embeddings(
            source,
            BadProvider(),
            candidate=candidate,
            checkpoint=checkpoint,
            provider_name="bad",
        )
    assert _sha(source) == before
    assert candidate.exists()
    assert json.loads(checkpoint.read_text())["status"] == "running"


@pytest.mark.asyncio
async def test_non_finite_provider_fails_before_packing(tmp_path):
    class NonFiniteProvider:
        dimensions = 1024

        async def embed_batch(self, texts):
            return [[math.inf] + [0.0] * 1023 for _ in texts]

    source = tmp_path / "soul.db"
    _source(source)
    with pytest.raises(ValueError, match="non-finite"):
        await migrate_sqlite_embeddings(
            source,
            NonFiniteProvider(),
            candidate=tmp_path / "candidate.db",
            checkpoint=tmp_path / "checkpoint.json",
            provider_name="non-finite",
        )


@pytest.mark.asyncio
async def test_resume_rejects_changed_source(tmp_path):
    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    _source(source)
    await migrate_sqlite_embeddings(
        source,
        SimpleEmbedding(1024),
        candidate=candidate,
        checkpoint=checkpoint,
        max_batches=1,
        provider_name="test",
    )
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE memories SET content='changed' WHERE id=1")
        conn.commit()
    with pytest.raises(ValueError, match="source database changed"):
        await migrate_sqlite_embeddings(
            source,
            SimpleEmbedding(1024),
            candidate=candidate,
            checkpoint=checkpoint,
            resume=True,
            provider_name="test",
        )


@pytest.mark.asyncio
async def test_rollback_retains_source_candidate_and_evidence(tmp_path):
    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    checkpoint = tmp_path / "checkpoint.json"
    _source(source)
    before = _sha(source)
    await migrate_sqlite_embeddings(
        source,
        SimpleEmbedding(1024),
        candidate=candidate,
        checkpoint=checkpoint,
        provider_name="test",
    )
    state = rollback_sqlite_migration(checkpoint)
    assert state["status"] == "rolled-back"
    assert "retained" in state["rollback"]
    assert source.exists() and candidate.exists() and checkpoint.exists()
    assert _sha(source) == before


def test_cli_dry_run_and_existing_candidate_fail_closed(tmp_path, capsys):
    source = tmp_path / "soul.db"
    candidate = tmp_path / "candidate.db"
    _source(source)
    assert (
        main(
            [
                "run",
                str(source),
                "--candidate",
                str(candidate),
                "--dry-run",
            ]
        )
        == 0
    )
    assert '"status": "dry-run"' in capsys.readouterr().out
    candidate.write_bytes(b"do not overwrite")
    assert (
        main(
            ["run", str(source), "--candidate", str(candidate), "--provider", "simple"]
        )
        == 2
    )
    assert candidate.read_bytes() == b"do not overwrite"
