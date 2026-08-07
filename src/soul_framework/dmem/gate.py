"""DMemGate — Dopamine-gated memory routing.

Ported from mcp_server_v3.py dmem_gate (arxiv 2603.14597).

Computes surprise (1 - max_cosine_sim to recent memories).
Low surprise + low utility → fast_path (skip enrichment, save tokens).
High surprise or high utility → full_processing (A-MEM enrichment pipeline).
"""

from __future__ import annotations

import struct
from typing import Any

from soul_framework.backend.base import BackendBase
from soul_framework.dmem.types import DMemResult, DMemRoute
from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import cosine_similarity


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


class DMemGate:
    """Surprise-based routing gate for incoming memories.

    Usage:
        gate = DMemGate(agent, backend, embedding)
        result = await gate.evaluate("some new content")
        if result.route == DMemRoute.FAST_PATH:
            # skip LLM enrichment, store directly
        else:
            # full A-MEM pipeline
    """

    def __init__(
        self,
        agent: str,
        backend: BackendBase,
        embedding: EmbeddingProvider,
        *,
        threshold_surprise: float = 0.3,
        threshold_utility: float = 0.6,
        lookback: int = 50,
    ) -> None:
        self._agent = agent
        self._db = backend
        self._emb = embedding
        self._threshold_surprise = threshold_surprise
        self._threshold_utility = threshold_utility
        self._lookback = lookback

    async def evaluate(
        self,
        content: str,
        *,
        utility: float = 0.5,
        threshold_surprise: float | None = None,
        threshold_utility: float | None = None,
    ) -> DMemResult:
        """Evaluate content for surprise against recent memories.

        Returns routing decision with surprise score.
        """
        t_surprise = threshold_surprise or self._threshold_surprise
        t_utility = threshold_utility or self._threshold_utility

        # Embed the candidate
        query_vec = await self._emb.embed(content)

        # Get recent memories with embeddings
        rows = await self._db.fetchall(
            """SELECT id, embedding FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND embedding IS NOT NULL
               ORDER BY created_at DESC
               LIMIT $2""",
            self._agent, self._lookback,
        )

        if not rows:
            # No existing memories — everything is novel
            return DMemResult(
                route=DMemRoute.FULL_PROCESSING,
                surprise=1.0,
                max_similarity=0.0,
                utility=utility,
                rpe=1.0,
                reason="no existing memories — fully novel",
            )

        # Compute max cosine similarity
        max_sim = 0.0
        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data:
                continue
            mem_vec = _unpack_embedding(emb_data)
            sim = cosine_similarity(query_vec, mem_vec)
            if sim > max_sim:
                max_sim = sim

        surprise = 1.0 - max_sim
        # RPE: reward prediction error (surprise weighted by utility)
        rpe = surprise * utility

        # Route decision
        if surprise < t_surprise and utility < t_utility:
            route = DMemRoute.FAST_PATH
            reason = f"low surprise ({surprise:.3f}<{t_surprise}) and low utility ({utility:.3f}<{t_utility})"
        else:
            route = DMemRoute.FULL_PROCESSING
            parts = []
            if surprise >= t_surprise:
                parts.append(f"high surprise ({surprise:.3f}>={t_surprise})")
            if utility >= t_utility:
                parts.append(f"high utility ({utility:.3f}>={t_utility})")
            reason = " and ".join(parts) if parts else "full processing"

        return DMemResult(
            route=route,
            surprise=surprise,
            max_similarity=max_sim,
            utility=utility,
            rpe=rpe,
            reason=reason,
        )
