"""ProceduralStore — store and retrieve multi-step workflows.

Two-tier retrieval:
1. TrieIndex prefix match (fast reflexes, O(L) lookup)
2. Semantic vector search (cosine similarity)

Ported from mcp_server_v3.py procedure_store/procedure_search.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase
from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import cosine_similarity
from soul_framework.procedures.types import Procedure, ProcedureSearchResult
from soul_framework.trees import TrieIndex


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_embedding(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_procedure(row: dict[str, Any]) -> Procedure:
    return Procedure(
        id=row.get("id", 0),
        agent=row.get("agent", ""),
        task_type=row.get("task_type", "general"),
        task_description=row.get("task_description", ""),
        workflow=row.get("workflow", ""),
        facts=row.get("facts", ""),
        hit_count=row.get("hit_count", 0),
        success_count=row.get("success_count", 0),
        fail_count=row.get("fail_count", 0),
        source_task=row.get("source_task", ""),
        build_policy=row.get("build_policy", "direct"),
        reflection=row.get("reflection", ""),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


class ProceduralStore:
    """Procedural memory with trie prefix matching + semantic search."""

    def __init__(
        self,
        agent: str,
        backend: BackendBase,
        embedding: EmbeddingProvider,
    ) -> None:
        self._agent = agent
        self._db = backend
        self._emb = embedding
        self._trie: TrieIndex | None = None
        self._trie_loaded = False

    async def _ensure_trie(self) -> TrieIndex:
        """Lazy-load trie index from all procedures."""
        if self._trie is None or not self._trie_loaded:
            self._trie = TrieIndex()
            rows = await self._db.fetchall(
                "SELECT id, task_description FROM procedural_memories WHERE agent = $1",
                self._agent,
            )
            for row in rows:
                desc = row.get("task_description", "")
                self._trie.insert(desc.lower(), row["id"])
            self._trie_loaded = True
        return self._trie

    def invalidate_trie(self) -> None:
        """Force trie rebuild on next search."""
        self._trie_loaded = False

    async def store(
        self,
        task_description: str,
        workflow: str,
        *,
        task_type: str = "general",
        facts: str = "",
        source_task: str = "",
        success: bool = True,
    ) -> int:
        """Store a procedural memory. Returns procedure ID."""
        now = _now_iso()

        # Embed description + workflow for semantic search
        embed_text = f"{task_description} {workflow}"
        vec = await self._emb.embed(embed_text)
        emb_bytes = _pack_embedding(vec)

        row = await self._db.fetchone(
            """INSERT INTO procedural_memories
               (agent, task_type, task_description, workflow, facts, embedding,
                hit_count, success_count, fail_count, source_task, build_policy,
                created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               RETURNING id""",
            self._agent, task_type, task_description, workflow, facts,
            emb_bytes, 0, 1 if success else 0, 0 if success else 1,
            source_task, "direct", now, now,
        )

        proc_id = row["id"] if row else 0

        # Update trie
        if self._trie is not None and self._trie_loaded:
            self._trie.insert(task_description.lower(), proc_id)

        return proc_id

    async def search(
        self,
        query: str,
        *,
        task_type: str = "",
        top_k: int = 5,
    ) -> list[ProcedureSearchResult]:
        """Two-tier search: trie prefix match first, then semantic."""
        results: list[ProcedureSearchResult] = []
        seen_ids: set[int] = set()

        # Tier 1: Trie prefix match (fast reflexes)
        trie = await self._ensure_trie()
        prefix_matches = trie.search_prefix(query.lower())
        prefix_ids = [val for _, val in prefix_matches]
        if prefix_ids:
            for pid in prefix_ids[:top_k]:
                if pid in seen_ids:
                    continue
                row = await self._db.fetchone(
                    "SELECT * FROM procedural_memories WHERE id = $1 AND agent = $2",
                    pid, self._agent,
                )
                if row:
                    proc = _row_to_procedure(row)
                    if task_type and proc.task_type != task_type:
                        continue
                    results.append(ProcedureSearchResult(
                        procedure=proc,
                        score=1.0,  # exact prefix match
                        similarity=1.0,
                        match_type="prefix",
                    ))
                    seen_ids.add(pid)

        # Tier 2: Semantic vector search
        query_vec = await self._emb.embed(query)

        conditions = ["agent = $1"]
        params: list[Any] = [self._agent]
        idx = 2
        if task_type:
            conditions.append(f"task_type = ${idx}")
            params.append(task_type)
            idx += 1

        where = " AND ".join(conditions)
        rows = await self._db.fetchall(
            f"SELECT * FROM procedural_memories WHERE {where}",
            *params,
        )

        for row in rows:
            if row["id"] in seen_ids:
                continue
            emb_data = row.get("embedding")
            if not emb_data:
                continue
            mem_vec = _unpack_embedding(emb_data)
            sim = cosine_similarity(query_vec, mem_vec)

            proc = _row_to_procedure(row)
            results.append(ProcedureSearchResult(
                procedure=proc,
                score=sim,
                similarity=sim,
                match_type="semantic",
            ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)

        # Bump hit_count for returned results
        for r in results[:top_k]:
            await self._db.execute(
                "UPDATE procedural_memories SET hit_count = hit_count + 1 WHERE id = $1",
                r.procedure.id,
            )

        return results[:top_k]

    async def get(self, procedure_id: int) -> Procedure | None:
        """Get a single procedure by ID."""
        row = await self._db.fetchone(
            "SELECT * FROM procedural_memories WHERE id = $1 AND agent = $2",
            procedure_id, self._agent,
        )
        if not row:
            return None
        return _row_to_procedure(row)

    async def record_outcome(self, procedure_id: int, success: bool) -> bool:
        """Record success/failure for a procedure. Returns True if found."""
        existing = await self.get(procedure_id)
        if not existing:
            return False
        if success:
            await self._db.execute(
                "UPDATE procedural_memories SET success_count = success_count + 1, updated_at = $1 WHERE id = $2",
                _now_iso(), procedure_id,
            )
        else:
            await self._db.execute(
                "UPDATE procedural_memories SET fail_count = fail_count + 1, updated_at = $1 WHERE id = $2",
                _now_iso(), procedure_id,
            )
        return True

    async def add_reflection(self, procedure_id: int, reflection: str) -> bool:
        """Add a reflection/lesson to a procedure."""
        existing = await self.get(procedure_id)
        if not existing:
            return False
        await self._db.execute(
            "UPDATE procedural_memories SET reflection = $1, updated_at = $2 WHERE id = $3",
            reflection, _now_iso(), procedure_id,
        )
        return True

    async def count(self, *, task_type: str = "") -> int:
        """Count procedures for this agent."""
        if task_type:
            val = await self._db.fetchval(
                "SELECT COUNT(*) FROM procedural_memories WHERE agent = $1 AND task_type = $2",
                self._agent, task_type,
            )
        else:
            val = await self._db.fetchval(
                "SELECT COUNT(*) FROM procedural_memories WHERE agent = $1",
                self._agent,
            )
        return val or 0
