"""InstinctManager — learned behaviors that form and decay like habits.

Instincts are SOUL's original IP — no existing framework or paper has this concept.
An instinct is a pattern-action pair with confidence that grows through activation
and decays through disuse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InstinctManager:
    """Manages instincts (learned behavioral patterns) for an agent."""

    def __init__(self, agent: str, backend: BackendBase) -> None:
        self._agent = agent
        self._db = backend

    async def create(
        self,
        trigger_pattern: str,
        action: str,
        *,
        confidence: float = 0.5,
    ) -> int:
        """Create a new instinct. Returns instinct ID."""
        now = _now_iso()
        row = await self._db.fetchone(
            """INSERT INTO instincts (agent, trigger_pattern, action, confidence, created_at)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            self._agent, trigger_pattern, action, confidence, now,
        )
        return row["id"] if row else 0

    async def list(self, *, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """List instincts, optionally filtering by minimum confidence."""
        if min_confidence > 0:
            return await self._db.fetchall(
                "SELECT * FROM instincts WHERE agent = $1 AND confidence >= $2 ORDER BY confidence DESC",
                self._agent, min_confidence,
            )
        return await self._db.fetchall(
            "SELECT * FROM instincts WHERE agent = $1 ORDER BY confidence DESC",
            self._agent,
        )

    async def activate(self, instinct_id: int) -> bool:
        """Record an activation: increment count, boost confidence, update timestamp.

        Confidence grows logarithmically: +0.1 * (1 - current_confidence)
        This means early activations have more impact, approaching 1.0 asymptotically.
        """
        row = await self._db.fetchone(
            "SELECT * FROM instincts WHERE id = $1 AND agent = $2",
            instinct_id, self._agent,
        )
        if not row:
            return False

        current_conf = row.get("confidence", 0.5)
        count = row.get("activation_count", 0)
        new_conf = min(1.0, current_conf + 0.1 * (1.0 - current_conf))
        now = _now_iso()

        await self._db.execute(
            """UPDATE instincts
               SET confidence = $1, activation_count = $2, last_activated = $3
               WHERE id = $4 AND agent = $5""",
            round(new_conf, 4), count + 1, now, instinct_id, self._agent,
        )
        return True

    async def get(self, instinct_id: int) -> dict[str, Any] | None:
        """Get a specific instinct by ID."""
        return await self._db.fetchone(
            "SELECT * FROM instincts WHERE id = $1 AND agent = $2",
            instinct_id, self._agent,
        )

    async def delete(self, instinct_id: int) -> bool:
        """Permanently delete an instinct."""
        existing = await self.get(instinct_id)
        if not existing:
            return False
        await self._db.execute(
            "DELETE FROM instincts WHERE id = $1 AND agent = $2",
            instinct_id, self._agent,
        )
        return True
