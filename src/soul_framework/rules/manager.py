"""RuleManager — behavioral rules and guardrails."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuleManager:
    """Manages behavioral rules for an agent."""

    def __init__(self, agent: str, backend: BackendBase) -> None:
        self._agent = agent
        self._db = backend

    async def set(
        self,
        rule_key: str,
        content: str,
        *,
        priority: str = "normal",
        set_by: str = "system",
    ) -> int:
        """Create or update a rule. Returns rule ID."""
        now = _now_iso()
        existing = await self._db.fetchone(
            "SELECT id FROM rules WHERE agent = $1 AND rule_key = $2",
            self._agent, rule_key,
        )
        if existing:
            await self._db.execute(
                """UPDATE rules SET content = $1, priority = $2, set_by = $3, active = 1
                   WHERE agent = $4 AND rule_key = $5""",
                content, priority, set_by, self._agent, rule_key,
            )
            return existing["id"]
        else:
            row = await self._db.fetchone(
                """INSERT INTO rules (agent, rule_key, content, set_by, priority, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                self._agent, rule_key, content, set_by, priority, now,
            )
            return row["id"] if row else 0

    async def list(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """List all rules for this agent."""
        if include_inactive:
            return await self._db.fetchall(
                "SELECT * FROM rules WHERE agent = $1 ORDER BY priority DESC, created_at",
                self._agent,
            )
        return await self._db.fetchall(
            "SELECT * FROM rules WHERE agent = $1 AND active = 1 ORDER BY priority DESC, created_at",
            self._agent,
        )

    async def get(self, rule_key: str) -> dict[str, Any] | None:
        """Get a specific rule by key."""
        return await self._db.fetchone(
            "SELECT * FROM rules WHERE agent = $1 AND rule_key = $2",
            self._agent, rule_key,
        )

    async def get_critical(self, *, limit: int = 5) -> list[dict[str, Any]]:
        """Get critical-priority active rules."""
        return await self._db.fetchall(
            "SELECT * FROM rules WHERE agent = $1 AND priority = $2 AND active = 1 ORDER BY created_at LIMIT $3",
            self._agent, "critical", limit,
        )

    async def deactivate(self, rule_key: str) -> bool:
        """Deactivate a rule (soft delete). Returns True if found."""
        existing = await self.get(rule_key)
        if not existing:
            return False
        await self._db.execute(
            "UPDATE rules SET active = 0 WHERE agent = $1 AND rule_key = $2",
            self._agent, rule_key,
        )
        return True
