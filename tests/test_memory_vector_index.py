from __future__ import annotations

from pathlib import Path

import pytest

from soul_framework import Soul
from soul_framework.config import SoulConfig
from soul_framework.memory.store import MemoryStore, _unpack_embedding
from soul_framework.memory.vector_index import ExactVectorIndex, USearchMemoryIndex


class KeywordEmbedding:
    dimensions = 3

    async def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if "python" in lowered:
            return [1.0, 0.0, 0.0]
        if "montaña" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


def _config(db: Path) -> SoulConfig:
    return SoulConfig(
        backend="sqlite",
        backend_url=str(db),
        memory_vector_index="usearch",
        memory_search_candidate_limit=10,
    )


@pytest.mark.asyncio
async def test_auto_empty_soul_uses_portable_exact_index(tmp_path: Path):
    db = tmp_path / "empty.db"
    config = SoulConfig(
        backend="sqlite",
        backend_url=str(db),
        memory_vector_index="auto",
    )
    soul = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    try:
        assert isinstance(soul.memory._vector_index, ExactVectorIndex)
        assert soul.memory._vector_index.count == 0
    finally:
        await soul.close()


@pytest.mark.asyncio
async def test_usearch_is_wired_to_store_search_update_and_reopen(tmp_path: Path):
    pytest.importorskip("usearch")
    db = tmp_path / "soul.db"
    config = _config(db)
    soul = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    python_id = await soul.memory.store("Python es mi lenguaje favorito")
    mountain_id = await soul.memory.store("Caminata en la montaña")
    assert (await soul.memory.search("python", limit=1))[0].memory.id == python_id

    await soul.memory.update(mountain_id, content="Python también vive aquí")
    await soul.memory.invalidate(python_id)
    hits = await soul.memory.search("python", limit=5)
    assert [hit.memory.id for hit in hits] == [mountain_id]
    await soul.close()

    sidecars = list(tmp_path.glob("soul.db.*.usearch"))
    assert len(sidecars) == 1
    assert Path(str(sidecars[0]) + ".json").is_file()

    reopened = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    try:
        hits = await reopened.memory.search("python", limit=5)
        assert [hit.memory.id for hit in hits] == [mountain_id]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_corrupt_sidecar_rebuilds_from_canonical_sqlite(tmp_path: Path):
    pytest.importorskip("usearch")
    db = tmp_path / "soul.db"
    config = _config(db)
    soul = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    memory_id = await soul.memory.store("Python permanece en SQLite")
    await soul.memory.search("python")
    await soul.close()
    sidecar = next(tmp_path.glob("soul.db.*.usearch"))
    sidecar.write_bytes(sidecar.read_bytes() + b"tampered")

    reopened = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    try:
        assert (await reopened.memory.search("python", limit=1))[0].memory.id == memory_id
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_two_open_instances_cannot_publish_partial_sidecar(tmp_path: Path):
    """The last closer must index rows committed by every SQLite connection."""
    pytest.importorskip("usearch")
    db = tmp_path / "soul.db"
    config = _config(db)
    first = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    second = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)

    python_id = await first.memory.store("Python es mi lenguaje favorito")
    mountain_id = await second.memory.store("Caminata en la montaña")
    await first.close()
    await second.close()

    reopened = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    try:
        assert await reopened.memory.count() == 2
        python_hits = await reopened.memory.search("python", limit=2)
        mountain_hits = await reopened.memory.search("montaña", limit=2)
        assert python_id in [hit.memory.id for hit in python_hits]
        assert mountain_id in [hit.memory.id for hit in mountain_hits]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_complete_fingerprint_with_partial_labels_rebuilds(tmp_path: Path):
    """A byte-valid sidecar cannot claim a full DB fingerprint with missing IDs."""
    pytest.importorskip("usearch")
    db = tmp_path / "soul.db"
    config = _config(db)
    soul = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    first_id = await soul.memory.store("Python es mi lenguaje favorito")
    second_id = await soul.memory.store("Caminata en la montaña")
    await soul.close()

    # Forge the historical failure shape: index bytes and metadata agree with one
    # label, while source_fingerprint honestly describes the two-row SQLite source.
    from soul_framework.backend.sqlite import SqliteBackend

    backend = SqliteBackend(str(db))
    await backend.initialize()
    try:
        canonical = await backend.fetchall(
            "SELECT id, embedding FROM memories "
            "WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NOT NULL ORDER BY id",
            "ADA",
        )
    finally:
        await backend.close()
    sidecar = next(tmp_path.glob("soul.db.*.usearch"))
    partial = USearchMemoryIndex(3)
    partial.build(
        [second_id],
        [_unpack_embedding(canonical[1]["embedding"])],
    )
    partial.save(sidecar, source_fingerprint=MemoryStore._rows_fingerprint(canonical))

    reopened = await Soul.create("ADA", embedding=KeywordEmbedding(), config=config)
    try:
        hits = await reopened.memory.search("python", limit=2)
        assert first_id in [hit.memory.id for hit in hits]
        assert reopened.memory._vector_index.count == 2
    finally:
        await reopened.close()
