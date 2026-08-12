"""MemoryStore — CRUD + semantic search over agent memories.

Core operations:
- store(): embed + insert memory
- search(): cosine similarity + temporal_decay_score ranking
- list(), get(), update(), invalidate()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from soul_framework.backend.base import BackendBase
from soul_framework.config import SoulConfig
from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import cosine_similarity
from soul_framework.memory.query import contextualize_query
from soul_framework.memory.scoring import temporal_decay_score
from soul_framework.memory.types import Memory, SearchResult
from soul_framework.memory.vector_index import (
    HnswMemoryIndex,
    StaleVectorIndexError,
    USearchMemoryIndex,
    VectorIndex,
    create_vector_index,
)

if TYPE_CHECKING:
    from soul_framework.integrity import SQLiteMemoryIntegrityGuard


def _pack_embedding(vec: list[float]) -> bytes:
    """Pack float list into compact binary (little-endian floats)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack binary back to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
        integrity_guard: SQLiteMemoryIntegrityGuard | None = None,
    ) -> None:
        self._agent = agent
        self._db = backend
        self._emb = embedding
        self._config = config
        self._integrity_guard = integrity_guard
        self._vector_index: VectorIndex | None = None
        self._vector_index_path: Path | None = None
        self._index_dirty = False

    async def verify_integrity(self) -> None:
        if self._integrity_guard is not None:
            await asyncio.to_thread(self._integrity_guard.verify_before_serve)

    async def _seal_integrity(self) -> None:
        if self._integrity_guard is not None:
            await asyncio.to_thread(self._integrity_guard.seal_and_publish)

    async def _fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        if self._integrity_guard is not None:
            return await asyncio.to_thread(
                self._integrity_guard.verified_fetchall, sql, *params
            )
        return await self._db.fetchall(sql, *params)

    async def _fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        if self._integrity_guard is not None:
            return await asyncio.to_thread(
                self._integrity_guard.verified_fetchone, sql, *params
            )
        return await self._db.fetchone(sql, *params)

    async def _fetchval(self, sql: str, *params: Any) -> Any:
        if self._integrity_guard is not None:
            return await asyncio.to_thread(
                self._integrity_guard.verified_fetchval, sql, *params
            )
        return await self._db.fetchval(sql, *params)

    @staticmethod
    def _rows_fingerprint(rows: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for row in rows:
            embedding = row.get("embedding") or b""
            if isinstance(embedding, memoryview):
                embedding = embedding.tobytes()
            digest.update(str(int(row["id"])).encode("ascii"))
            digest.update(b"\0")
            digest.update(embedding)
            digest.update(b"\n")
        return digest.hexdigest()

    async def initialize_vector_index(self) -> None:
        """Build/load the SQLite candidate index; PostgreSQL already uses pgvector."""
        mode = self._config.memory_vector_index.lower()
        if not self._config.memory_vector_cache or mode == "off":
            return
        if mode not in {"auto", "usearch", "hnsw", "exact"}:
            raise ValueError(
                "memory_vector_index must be auto, usearch, hnsw, exact, or off"
            )
        if not hasattr(self._db, "url"):
            return
        rows = await self._fetchall(
            "SELECT id, embedding FROM memories "
            "WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NOT NULL ORDER BY id",
            self._agent,
        )
        ids: list[int] = []
        vectors: list[list[float]] = []
        for row in rows:
            data = row["embedding"]
            if isinstance(data, memoryview):
                data = data.tobytes()
            vector = _unpack_embedding(data)
            if len(vector) != self._emb.dimensions:
                raise RuntimeError(
                    "Stored embedding dimensions do not match the selected provider; "
                    "run soul_framework.embedding_migration before activation"
                )
            ids.append(int(row["id"]))
            vectors.append(vector)

        index = create_vector_index(
            self._emb.dimensions,
            engine=mode,
            m=self._config.memory_hnsw_m,
            ef_construction=self._config.memory_hnsw_ef_construction,
            ef_search=self._config.memory_hnsw_ef_search,
        )

        fingerprint = self._rows_fingerprint(rows)
        db_url = str(getattr(self._db, "url", ":memory:"))
        if (
            isinstance(index, (HnswMemoryIndex, USearchMemoryIndex))
            and db_url != ":memory:"
            and self._integrity_guard is None
        ):
            suffix = hashlib.sha256(self._agent.encode("utf-8")).hexdigest()[:12]
            engine_name = "usearch" if isinstance(index, USearchMemoryIndex) else "hnsw"
            index_type = USearchMemoryIndex if engine_name == "usearch" else HnswMemoryIndex
            self._vector_index_path = Path(f"{db_url}.{suffix}.{engine_name}")
            try:
                index = await asyncio.to_thread(
                    index_type.load,
                    self._vector_index_path,
                    source_fingerprint=fingerprint,
                )
            except StaleVectorIndexError:
                await asyncio.to_thread(index.build, ids, vectors)
                self._index_dirty = True
        else:
            await asyncio.to_thread(index.build, ids, vectors)
        self._vector_index = index

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

        values = (
            self._agent,
            category,
            content,
            emb_bytes,
            importance,
            valence,
            arousal,
            dominance,
            source,
            scope,
            confidence,
            utility,
            event_time or now,
            episode_context,
            meta_json,
            now,
            now,
        )
        insert_sql = """INSERT INTO memories
               (agent, category, content, embedding, importance, valence, arousal, dominance,
                source, scope, confidence_score, utility_score, event_time, episode_context,
                metadata, valid_from, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
               RETURNING id"""
        if self._integrity_guard is not None:
            row = await asyncio.to_thread(
                self._integrity_guard.mutate_and_publish,
                insert_sql,
                *values,
                mode="one",
            )
            memory_id = int(row["id"]) if isinstance(row, dict) else 0
            if memory_id and self._vector_index is not None:
                await asyncio.to_thread(self._vector_index.add, memory_id, vec)
                self._index_dirty = True
            return memory_id

        atomic_insert = getattr(self._db, "insert_memory_with_vector", None)
        if callable(atomic_insert):
            memory_id = await atomic_insert(values, vec)
            await self._seal_integrity()
            return memory_id

        row = await self._db.fetchone(insert_sql, *values)
        memory_id = row["id"] if row else 0
        if memory_id and self._vector_index is not None:
            await asyncio.to_thread(self._vector_index.add, memory_id, vec)
            self._index_dirty = True
        if memory_id:
            await self._seal_integrity()
        return memory_id

    async def search(
        self,
        query: str,
        *,
        limit: int = 0,
        category: str = "",
        min_importance: int = 0,
        scope: str = "",
        context: str | list[str] | None = None,
    ) -> list[SearchResult]:
        """Semantic search with optional bounded conversation context."""
        limit = limit or self._config.memory_search_default_limit
        embedding_query = contextualize_query(
            query, context, max_context_chars=self._config.memory_context_max_chars
        )
        query_vec = await self._emb.embed(embedding_query)

        vector_search = getattr(self._db, "search_memory_vectors", None)
        if callable(vector_search):
            candidate_limit = max(
                limit,
                self._config.memory_search_candidate_limit,
            )
            rows = await vector_search(
                self._agent,
                query_vec,
                category=category,
                min_importance=min_importance,
                scope=scope,
                limit=candidate_limit,
            )
        elif self._vector_index is not None and not (
            category or min_importance or scope
        ):
            candidate_limit = max(limit, self._config.memory_search_candidate_limit)
            hits = await asyncio.to_thread(
                self._vector_index.search, query_vec, candidate_limit
            )
            similarities = {hit.memory_id: hit.similarity for hit in hits}
            if hits:
                placeholders = ",".join(f"${idx}" for idx in range(2, len(hits) + 2))
                rows = await self._fetchall(
                    f"SELECT * FROM memories WHERE agent = $1 AND invalid_at IS NULL "
                    f"AND id IN ({placeholders})",
                    self._agent,
                    *(hit.memory_id for hit in hits),
                )
                for row in rows:
                    row["_vector_similarity"] = similarities[int(row["id"])]
            else:
                rows = (
                    await self._fetchall(
                        "SELECT * FROM memories WHERE agent = $1 AND 0", self._agent
                    )
                    if self._integrity_guard is not None
                    else []
                )
        else:
            # SQLite fallback: exact scan over packed vectors.
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
            rows = await self._fetchall(
                f"SELECT * FROM memories WHERE {' AND '.join(conditions)}",
                *params,
            )

        # Exact text is a correctness fallback, not the main retrieval path.  It
        # prevents ANN approximation or a weak embedding from hiding a literal
        # old memory.  Semantic/filtered cases continue through the normal path.
        if (
            self._config.memory_exact_fallback
            and query.strip()
            and not (category or min_importance or scope)
        ):
            exact_rows = await self._fetchall(
                "SELECT * FROM memories WHERE agent = $1 AND invalid_at IS NULL "
                "AND content LIKE $2 LIMIT $3",
                self._agent,
                f"%{query.strip()}%",
                max(limit, self._config.memory_search_candidate_limit),
            )
            present = {int(row["id"]) for row in rows}
            rows.extend(row for row in exact_rows if int(row["id"]) not in present)

        now = datetime.now(UTC)
        results: list[SearchResult] = []

        for row in rows:
            if "_vector_similarity" in row:
                sim = float(row["_vector_similarity"])
            else:
                emb_data = row.get("embedding")
                if not emb_data:
                    continue
                if isinstance(emb_data, memoryview):
                    emb_data = emb_data.tobytes()
                mem_vec = _unpack_embedding(emb_data)
                sim = cosine_similarity(query_vec, mem_vec)

            # Calculate age in days
            try:
                created = datetime.fromisoformat(row["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
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
            # A highly relevant old memory must not disappear solely because it is old.
            semantic_floor = max(0.0, sim) * self._config.memory_semantic_floor
            score = max(score, semantic_floor) + row.get("importance", 5) * 1e-6

            results.append(
                SearchResult(
                    memory=_row_to_memory(row),
                    score=score,
                    similarity=sim,
                )
            )

        # Sort by score descending, return top N
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    async def get(self, memory_id: int) -> Memory | None:
        """Get a single memory by ID."""
        row = await self._fetchone(
            "SELECT * FROM memories WHERE id = $1 AND agent = $2",
            memory_id,
            self._agent,
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

        rows = await self._fetchall(
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

        changes: dict[str, Any] = {}
        new_vector: list[float] | None = None
        if content is not None and content != existing.content:
            new_vector = await self._emb.embed(content)
            changes["content"] = content
            changes["embedding"] = _pack_embedding(new_vector)
        if importance is not None:
            changes["importance"] = importance
        if category is not None:
            changes["category"] = category
        if utility is not None:
            changes["utility_score"] = utility
        if confidence is not None:
            changes["confidence_score"] = confidence
        if metadata is not None:
            changes["metadata"] = json.dumps(metadata)

        if not changes:
            return True

        atomic_update = getattr(self._db, "update_memory_fields", None)
        if callable(atomic_update):
            updated = await atomic_update(memory_id, self._agent, changes, new_vector)
            if updated:
                await self._seal_integrity()
            return updated

        sets: list[str] = []
        params: list[Any] = []
        for idx, (column, value) in enumerate(changes.items(), start=1):
            sets.append(f"{column} = ${idx}")
            params.append(value)
        next_idx = len(params) + 1
        params.append(memory_id)
        params.append(self._agent)
        update_sql = (
            f"UPDATE memories SET {', '.join(sets)} "
            f"WHERE id = ${next_idx} AND agent = ${next_idx + 1}"
        )
        if self._integrity_guard is not None:
            updated_rows = await asyncio.to_thread(
                self._integrity_guard.mutate_and_publish,
                update_sql,
                *params,
                mode="rowcount",
            )
            if int(updated_rows) != 1:
                return False
        else:
            await self._db.execute(update_sql, *params)
        if new_vector is not None and self._vector_index is not None:
            await asyncio.to_thread(self._vector_index.add, memory_id, new_vector)
            self._index_dirty = True
        if self._integrity_guard is None:
            await self._seal_integrity()
        return True

    async def invalidate(self, memory_id: int) -> bool:
        """Soft-delete a memory by setting invalid_at. Returns True if found."""
        existing = await self.get(memory_id)
        if not existing:
            return False
        invalidate_sql = (
            "UPDATE memories SET invalid_at = $1 WHERE id = $2 AND agent = $3"
        )
        invalidate_params = (_now_iso(), memory_id, self._agent)
        if self._integrity_guard is not None:
            updated_rows = await asyncio.to_thread(
                self._integrity_guard.mutate_and_publish,
                invalidate_sql,
                *invalidate_params,
                mode="rowcount",
            )
            if int(updated_rows) != 1:
                return False
        else:
            await self._db.execute(invalidate_sql, *invalidate_params)
        if self._vector_index is not None:
            await asyncio.to_thread(self._vector_index.remove, memory_id)
            self._index_dirty = True
        if self._integrity_guard is None:
            await self._seal_integrity()
        return True

    async def close(self) -> None:
        """Persist a byte-bound native ANN sidecar after successful mutations."""
        if (
            self._index_dirty
            and isinstance(self._vector_index, (HnswMemoryIndex, USearchMemoryIndex))
            and self._vector_index_path is not None
            and self._integrity_guard is None
        ):
            rows = await self._fetchall(
                "SELECT id, embedding FROM memories WHERE agent = $1 "
                "AND invalid_at IS NULL AND embedding IS NOT NULL ORDER BY id",
                self._agent,
            )
            await asyncio.to_thread(
                self._vector_index.save,
                self._vector_index_path,
                source_fingerprint=self._rows_fingerprint(rows),
            )
            self._index_dirty = False

    async def count(self, *, category: str = "") -> int:
        """Count valid memories for this agent."""
        if category:
            val = await self._fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent = $1 AND category = $2 AND invalid_at IS NULL",
                self._agent,
                category,
            )
        else:
            val = await self._fetchval(
                "SELECT COUNT(*) FROM memories WHERE agent = $1 AND invalid_at IS NULL",
                self._agent,
            )
        return val or 0
