from __future__ import annotations

from pathlib import Path

import pytest

from soul_framework import Soul
from soul_framework.config import SoulConfig


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
