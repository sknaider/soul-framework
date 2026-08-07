"""ReflectionManager — inner monologue and self-reflection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReflectionManager:
    """Manages inner monologue / self-reflection for an agent."""

    def __init__(self, agent: str, backend: BackendBase) -> None:
        self._agent = agent
        self._db = backend

    async def add_thought(
        self,
        thought: str,
        emotional_state: str = "",
        *,
        session_id: str = "",
        turn_number: int = 0,
    ) -> int:
        """Record an inner thought. Returns thought ID."""
        now = _now_iso()
        row = await self._db.fetchone(
            """INSERT INTO inner_monologue
               (agent, session_id, turn_number, thought, emotional_state, created_at)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            self._agent, session_id, turn_number, thought, emotional_state, now,
        )
        return row["id"] if row else 0

    async def get_last_thought(self) -> dict[str, Any] | None:
        """Get the most recent inner thought."""
        return await self._db.fetchone(
            """SELECT * FROM inner_monologue
               WHERE agent = $1 ORDER BY created_at DESC LIMIT 1""",
            self._agent,
        )

    async def list_thoughts(
        self, *, limit: int = 10, session_id: str = ""
    ) -> list[dict[str, Any]]:
        """List recent thoughts, newest first."""
        if session_id:
            return await self._db.fetchall(
                """SELECT * FROM inner_monologue
                   WHERE agent = $1 AND session_id = $2
                   ORDER BY created_at DESC LIMIT $3""",
                self._agent, session_id, limit,
            )
        return await self._db.fetchall(
            """SELECT * FROM inner_monologue
               WHERE agent = $1 ORDER BY created_at DESC LIMIT $2""",
            self._agent, limit,
        )

    async def add_diary_entry(
        self,
        content: str,
        *,
        mood: str = "",
        session_date: str = "",
    ) -> int:
        """Record a diary entry (end-of-session summary). Returns entry ID."""
        now = _now_iso()
        date = session_date or now[:10]  # YYYY-MM-DD
        row = await self._db.fetchone(
            """INSERT INTO diary (agent, mood, content, session_date, created_at)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            self._agent, mood, content, date, now,
        )
        return row["id"] if row else 0

    async def get_last_diary(self) -> dict[str, Any] | None:
        """Get the most recent diary entry."""
        return await self._db.fetchone(
            "SELECT * FROM diary WHERE agent = $1 ORDER BY created_at DESC LIMIT 1",
            self._agent,
        )
