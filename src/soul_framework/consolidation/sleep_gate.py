"""SleepGate — 4-phase nocturnal memory consolidation.

Ported from mcp_server_v3.py sleep_gate (arxiv 2603.14517).

Phases:
1. REPLAY — boost relevance for recently activated memories
2. FORGET — decay stale, low-importance memories (with emotional resistance)
3. PRUNE — soft-invalidate memories below relevance threshold
4. CONSOLIDATE — merge near-duplicate memories (cosine similarity > threshold)
"""

from __future__ import annotations

import math
import struct
from datetime import datetime, timedelta, timezone
from typing import Any

from soul_framework.backend.base import BackendBase
from soul_framework.consolidation.types import ConsolidationReport, PhaseResult


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack binary embedding to float list."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SleepGate:
    """Automated memory consolidation engine.

    Usage:
        gate = SleepGate(agent, backend)
        report = await gate.run(dry_run=True)  # preview
        report = await gate.run(dry_run=False)  # execute
    """

    def __init__(self, agent: str, backend: BackendBase) -> None:
        self._agent = agent
        self._db = backend

    async def run(
        self,
        *,
        dry_run: bool = True,
        replay_boost: float = 0.10,
        forget_decay: float = 0.85,
        stale_days: int = 14,
        prune_threshold: float = 0.05,
        consolidation_similarity: float = 0.92,
        max_prune: int = 50,
    ) -> ConsolidationReport:
        """Execute all 4 consolidation phases. Returns report."""
        report = ConsolidationReport(agent=self._agent, dry_run=dry_run)

        p1 = await self._phase_replay(dry_run, replay_boost)
        report.phases.append(p1)

        p2 = await self._phase_forget(dry_run, forget_decay, stale_days)
        report.phases.append(p2)

        p3 = await self._phase_prune(dry_run, prune_threshold, max_prune)
        report.phases.append(p3)

        p4 = await self._phase_consolidate(dry_run, consolidation_similarity)
        report.phases.append(p4)

        report.total_affected = sum(p.affected for p in report.phases)
        return report

    async def _phase_replay(self, dry_run: bool, boost: float) -> PhaseResult:
        """Phase 1 REPLAY: boost relevance for memories activated in last 24h."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = await self._db.fetchall(
            """SELECT id, relevance_score FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND last_activation IS NOT NULL
               AND last_activation > $2""",
            self._agent, cutoff,
        )

        if not dry_run:
            for row in rows:
                current = row.get("relevance_score", 1.0) or 1.0
                new_score = min(1.0, current + boost)
                await self._db.execute(
                    "UPDATE memories SET relevance_score = $1 WHERE id = $2",
                    new_score, row["id"],
                )

        return PhaseResult(
            phase="REPLAY",
            affected=len(rows),
            details=f"boosted +{boost:.0%} for {len(rows)} recently activated",
        )

    async def _phase_forget(
        self, dry_run: bool, decay: float, stale_days: int
    ) -> PhaseResult:
        """Phase 2 FORGET: decay stale, low-importance memories.

        Emotional resistance: high valence/arousal memories resist forgetting.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
        rows = await self._db.fetchall(
            """SELECT id, relevance_score, valence, arousal, utility_score
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND importance <= 7
               AND (last_activation IS NULL OR last_activation < $2)""",
            self._agent, cutoff,
        )

        affected = 0
        if not dry_run:
            for row in rows:
                val = abs(row.get("valence", 0.0) or 0.0)
                aro = row.get("arousal", 0.0) or 0.0
                # Emotional resistance: emotions slow forgetting
                emotional_resist = 1.0 + val * 0.5 + aro * 0.3
                effective_decay = 1.0 - ((1.0 - decay) / emotional_resist)

                current = row.get("relevance_score", 1.0) or 1.0
                new_score = current * effective_decay

                # Also nudge utility down
                util = row.get("utility_score", 0.5) or 0.5
                new_util = max(0.0, util - 0.03)

                await self._db.execute(
                    "UPDATE memories SET relevance_score = $1, utility_score = $2 WHERE id = $3",
                    new_score, new_util, row["id"],
                )
                affected += 1

        return PhaseResult(
            phase="FORGET",
            affected=affected if not dry_run else len(rows),
            details=f"decay={decay:.0%}, stale>{stale_days}d, {len(rows)} candidates",
        )

    async def _phase_prune(
        self, dry_run: bool, threshold: float, max_prune: int
    ) -> PhaseResult:
        """Phase 3 PRUNE: soft-invalidate low-relevance, low-importance memories.

        Safety: identity_defining memories are protected.
        """
        rows = await self._db.fetchall(
            """SELECT id FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND relevance_score < $2
               AND importance <= 5
               AND identity_defining = 0
               ORDER BY relevance_score ASC
               LIMIT $3""",
            self._agent, threshold, max_prune,
        )

        if not dry_run:
            now = _now_iso()
            for row in rows:
                await self._db.execute(
                    "UPDATE memories SET invalid_at = $1 WHERE id = $2",
                    now, row["id"],
                )

        return PhaseResult(
            phase="PRUNE",
            affected=len(rows),
            details=f"threshold<{threshold}, max={max_prune}, protected: identity_defining",
        )

    async def _phase_consolidate(
        self, dry_run: bool, similarity_threshold: float
    ) -> PhaseResult:
        """Phase 4 CONSOLIDATE: merge near-duplicate memories.

        For each pair above similarity threshold, keep the higher-importance one,
        boost its relevance, and invalidate the duplicate.
        """
        rows = await self._db.fetchall(
            """SELECT id, importance, relevance_score, embedding
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               AND embedding IS NOT NULL""",
            self._agent,
        )

        # Decode all embeddings
        entries: list[dict[str, Any]] = []
        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data or len(emb_data) < 8:
                continue
            entries.append({
                "id": row["id"],
                "importance": row.get("importance", 5),
                "relevance": row.get("relevance_score", 1.0) or 1.0,
                "vec": _unpack_embedding(emb_data),
            })

        merged = 0
        invalidated: set[int] = set()
        now = _now_iso()

        # O(n^2) but bounded by active memory count — fine for <10K
        for i in range(len(entries)):
            if entries[i]["id"] in invalidated:
                continue
            for j in range(i + 1, len(entries)):
                if entries[j]["id"] in invalidated:
                    continue
                sim = _cosine_sim(entries[i]["vec"], entries[j]["vec"])
                if sim >= similarity_threshold:
                    # Keep higher importance; tiebreak by higher relevance
                    a, b = entries[i], entries[j]
                    if (b["importance"], b["relevance"]) > (a["importance"], a["relevance"]):
                        survivor, victim = b, a
                    else:
                        survivor, victim = a, b

                    if not dry_run:
                        # Boost survivor
                        new_rel = min(1.0, survivor["relevance"] + 0.05)
                        await self._db.execute(
                            "UPDATE memories SET relevance_score = $1 WHERE id = $2",
                            new_rel, survivor["id"],
                        )
                        # Invalidate victim
                        await self._db.execute(
                            "UPDATE memories SET invalid_at = $1 WHERE id = $2",
                            now, victim["id"],
                        )

                    invalidated.add(victim["id"])
                    merged += 1

        return PhaseResult(
            phase="CONSOLIDATE",
            affected=merged,
            details=f"similarity>{similarity_threshold:.0%}, {merged} duplicates merged",
        )
