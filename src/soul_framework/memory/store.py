"""MemoryStore — CRUD + semantic search over agent memories.

Core operations:
- store(): embed + insert memory
- search(): cosine similarity + temporal_decay_score ranking
- list(), get(), update(), invalidate()
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase
from soul_framework.config import SoulConfig
from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import cosine_similarity
from soul_framework.memory.scoring import temporal_decay_score
from soul_framework.memory.types import Memory, SearchResult


def _pack_embedding(vec: list[float]) -> bytes:
    """Pack float list into compact binary (little-endian floats)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack binary back to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_memory(row: dict[str, Any]) -> Memory:
    """Convert a DB row dict to a Memory dataclass."""
    meta = row.get("metadata", "{}")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return Memory(
        id=row.get("id", 0),
        agent=row.get("agent", ""),
        category=row.get("category", "fact"),
        content=row.get("content", ""),
        importance=row.get("importance", 5),
        valence=row.get("valence", 0.0),
        arousal=row.get("arousal", 0.0),
        dominance=row.get("dominance", 0.0),
        source=row.get("source", "conversation"),
        scope=row.get("scope", "private"),
        confidence_score=row.get("confidence_score", 1.0),
        utility_score=row.get("utility_score", 0.5),
        relevance_score=row.get("relevance_score", 1.0),
        last_activation=row.get("last_activation", ""),
        identity_defining=bool(row.get("identity_defining", 0)),
        event_time=row.get("event_time", ""),
        episode_context=row.get("episode_context", ""),
        metadata=meta,
        valid_from=row.get("valid_from", ""),
        invalid_at=row.get("invalid_at", ""),
        created_at=row.get("created_at", ""),
    )


class MemoryStore:
    """Agent memory store with embedding-based semantic search."""

    def __init__(
        self,
        agent: str,
        backend: BackendBase,
        embedding: EmbeddingProvider,
        config: SoulConfig,
    ) -> None:
        self._agent = agent
        self._db = backend
        self._emb = embedding
        self._config = config

    async def store(
        self,
        content: str,
        *,
        category: str = "fact",
        importance: int = 5,
        valence: float = 0.0,
        arousal: float = 0.0,
        dominance: float = 0.0,
        source: str = "conversation",
        scope: str = "private",
        confidence: float = 1.0,
        utility: float = 0.5,
        event_time: str = "",
        episode_context: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Store a memory with embedding. Returns memory ID."""
        now = _now_iso()
        vec = await self._emb.embed(content)
        emb_bytes = _pack_embedding(vec)
        meta_json = json.dumps(metadata or {})

        row = await self._db.fetchone(
            """INSERT INTO memories
               (agent, category, content, embedding, importance, valence, arousal, dominance,
                source, scope, confidence_score, utility_score, event_time, episode_context,
                metadata, valid_from, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
               RETURNING id""",
            self._agent, category, content, emb_bytes, importance,
            valence, arousal, dominance, source, scope,
            confidence, utility, event_time or now, episode_context,
            meta_json, now, now,
        )
        return row["id"] if row else 0

    async def search(
        self,
        query: str,
        *,
        limit: int = 0,
        category: str = "",
        min_importance: int = 0,
        scope: str = "",
    ) -> list[SearchResult]:
        """Semantic search: embed query, compute cosine similarity, rank with decay."""
        limit = limit or self._config.memory_search_default_limit
        query_vec = await self._emb.embed(query)

        # Build filter SQL
        conditions = ["agent = $1", "invalid_at IS NULL"]
        params: list[Any] = [self._agent]
        idx = 2
        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if min_importance > 0:
            conditions.append(f"importance >= ${idx}")
            params.append(min_importance)
            idx += 1
        if scope:
            conditions.append(f"scope = ${idx}")
            params.append(scope)
            idx += 1

        where = " AND ".join(conditions)
        rows = await self._db.fetchall(
            f"SELECT * FROM memories WHERE {where}",
            *params,
        )

        now = datetime.now(timezone.utc)
        results: list[SearchResult] = []

        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data:
                continue
            mem_vec = _unpack_embedding(emb_data)
            sim = cosine_similarity(query_vec, mem_vec)

            # Calculate age in days
            try:
                created = datetime.fromisoformat(row["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                days_old = max(0.0, (now - created).total_seconds() / 86400.0)
            except (ValueError, TypeError):
                days_old = 0.0

            score = temporal_decay_score(
                similarity=sim,
                days_old=days_old,
                importance=row.get("importance", 5),
                valence=row.get("valence", 0.0),
                arousal=row.get("arousal", 0.0),
                category=row.get("category", ""),
                utility=row.get("utility_score", 0.5),
                confidence=row.get("confidence_score", 1.0),
            )

            results.append(SearchResult(
                memory=_row_to_memory(row),
                score=score,
                similarity=sim,
            ))

        # Sort by score descending, return top N
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    async def get(self, memory_id: int) -> Memory | None:
        """Get a single memory by ID."""
        row = await self._db.fetchone(
            "SELECT * FROM memories WHERE id = $1 AND agent = $2",
            memory_id, self._agent,
        )
        if not row:
            return None
        return _row_to_memory(row)

    async def list(
        self,
        *,
        limit: int = 20,
        category: str = "",
        include_invalid: bool = False,
    ) -> list[Memory]:
        """List recent memories, newest first."""
        conditions = ["agent = $1"]
        params: list[Any] = [self._agent]
        idx = 2

        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if not include_invalid:
            conditions.append("invalid_at IS NULL")

        where = " AND ".join(conditions)
        conditions_with_limit = f"${idx}"
        params.append(limit)

        rows = await self._db.fetchall(
            f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT {conditions_with_limit}",
            *params,
        )
        return [_row_to_memory(r) for r in rows]

    async def update(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        importance: int | None = None,
        category: str | None = None,
        utility: float | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update memory fields. Re-embeds if content changes. Returns True if found."""
        existing = await self.get(memory_id)
        if not existing:
            return False

        sets: list[str] = []
        params: list[Any] = []
        idx = 1

        if content is not None and content != existing.content:
            vec = await self._emb.embed(content)
            sets.append(f"content = ${idx}")
            params.append(content)
            idx += 1
            sets.append(f"embedding = ${idx}")
            params.append(_pack_embedding(vec))
            idx += 1
        if importance is not None:
            sets.append(f"importance = ${idx}")
            params.append(importance)
            idx += 1
        if category is not None:
            sets.append(f"category = ${idx}")
            params.append(category)
            idx += 1
        if utility is not None:
            sets.append(f"utility_score = ${idx}")
            params.append(utility)
            idx += 1
        if confidence is not None:
            sets.append(f"confidence_score = ${idx}")
            params.append(confidence)
            idx += 1
        if metadata is not None:
            sets.append(f"metadata = ${idx}")
            params.append(json.dumps(metadata))
            idx += 1

        if not sets:
            return True  # nothing to update

        sets_sql = ", ".join(sets)
        params.append(memory_id)
        params.append(self._agent)
        await self._db.execute(
            f"UPDATE memories SET {sets_sql} WHERE id = ${idx} AND agent = ${idx + 1}",
            *params,
        )
        return True

    async def invalidate(self, memory_id: int) -> bool:
        """Soft-delete a memory by setting invalid_at. Returns True if found."""
        existing = await self.get(memory_id)
        if not existing:
            return False
        await self._db.execute(
            "UPDATE memories SET invalid_at = $1 WHERE id = $2 AND agent = $3",
            _now_iso(), memory_id, self._agent,
        )
        return True

    async def count(self, *, category: str = "") -> int:
        """Count valid memories for this agent."""
        if category:
            val = await self._db.fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent = $1 AND category = $2 AND invalid_at IS NULL",
                self._agent, category,
            )
        else:
            val = await self._db.fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL",
                self._agent,
            )
        return val or 0
