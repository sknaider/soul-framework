from __future__ import annotations

from soul_framework import Soul
from soul_framework.config import SoulConfig


class RecordingEmbedding:
    dimensions = 3

    def __init__(self):
        self.queries = []

    async def embed(self, text):
        self.queries.append(text)
        if "caf" in text.lower():
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    async def embed_batch(self, texts):
        return [await self.embed(text) for text in texts]


async def test_context_is_embedded_and_semantic_match_is_not_buried(tmp_path):
    embedding = RecordingEmbedding()
    cfg = SoulConfig(
        backend="sqlite",
        backend_url=str(tmp_path / "soul.db"),
        memory_vector_index="exact",
        memory_semantic_floor=0.8,
    )
    async with Soul.create("ADA", config=cfg, embedding=embedding) as soul:
        target = await soul.memory.store("William toma café sin azúcar", importance=5)
        await soul.memory.store("La pantalla está encendida", importance=10)
        results = await soul.memory.search(
            "¿cómo tomo mi bebida?", context="Estamos hablando del café.", limit=1
        )
        assert results[0].memory.id == target
        assert embedding.queries[-1].startswith("Estamos hablando del café.")


async def test_exact_fallback_recovers_memory_outside_ann_candidates(tmp_path):
    embedding = RecordingEmbedding()
    cfg = SoulConfig(
        backend="sqlite",
        backend_url=str(tmp_path / "soul.db"),
        memory_vector_index="exact",
        memory_search_candidate_limit=1,
    )
    async with Soul.create("ADA", config=cfg, embedding=embedding) as soul:
        target = await soul.memory.store("anchor literal único", importance=4)
        await soul.memory.store("café reciente", importance=10)
        soul.memory._vector_index.remove(target)
        results = await soul.memory.search("anchor literal único", limit=1)
        assert results[0].memory.id == target
