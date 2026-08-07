"""IdentityManager — CRUD for agent identity, OCEAN scores, relationships."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.base import BackendBase
from soul_framework.config import SoulConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IdentityManager:
    """Manages agent identity, OCEAN personality scores, and relationships."""

    def __init__(self, agent: str, backend: BackendBase, config: SoulConfig) -> None:
        self._agent = agent
        self._db = backend
        self._config = config

    async def get(self) -> dict[str, Any] | None:
        """Get full identity record."""
        row = await self._db.fetchone(
            "SELECT * FROM identity WHERE agent = $1", self._agent
        )
        if not row:
            return None
        # Parse ocean_scores JSON
        ocean_raw = row.get("ocean_scores", "{}")
        if isinstance(ocean_raw, str):
            try:
                row["ocean_scores"] = json.loads(ocean_raw)
            except (json.JSONDecodeError, TypeError):
                row["ocean_scores"] = {}
        return row

    async def set_personality(self, fields: dict[str, Any]) -> None:
        """Set or update identity fields (personality, philosophy, boot_context)."""
        existing = await self.get()
        now = _now_iso()

        if existing:
            sets: list[str] = []
            params: list[Any] = []
            idx = 1
            for key in ("personality", "philosophy", "boot_context"):
                if key in fields:
                    sets.append(f"{key} = ${idx}")
                    params.append(fields[key])
                    idx += 1
            if sets:
                sets.append(f"updated_at = ${idx}")
                params.append(now)
                idx += 1
                params.append(self._agent)
                sets_sql = ", ".join(sets)
                await self._db.execute(
                    f"UPDATE identity SET {sets_sql} WHERE agent = ${idx}",
                    *params,
                )
        else:
            await self._db.execute(
                """INSERT INTO identity (agent, personality, philosophy, boot_context, ocean_scores, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                self._agent,
                fields.get("personality", ""),
                fields.get("philosophy", ""),
                fields.get("boot_context", ""),
                "{}",
                now,
            )

    async def get_ocean(self) -> dict[str, float] | None:
        """Get OCEAN scores as dict {O, C, E, A, N}."""
        row = await self._db.fetchone(
            "SELECT ocean_scores FROM identity WHERE agent = $1", self._agent
        )
        if not row:
            return None
        raw = row.get("ocean_scores", "{}")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return raw

    async def set_ocean(self, scores: dict[str, float]) -> None:
        """Set OCEAN scores. Validates values are in [0.0, 1.0]."""
        for key in ("O", "C", "E", "A", "N"):
            val = scores.get(key, 0.5)
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"OCEAN score {key}={val} must be in [0.0, 1.0]")

        ocean_json = json.dumps(scores)
        now = _now_iso()

        existing = await self._db.fetchone(
            "SELECT agent FROM identity WHERE agent = $1", self._agent
        )
        if existing:
            await self._db.execute(
                "UPDATE identity SET ocean_scores = $1, updated_at = $2 WHERE agent = $3",
                ocean_json, now, self._agent,
            )
        else:
            await self._db.execute(
                """INSERT INTO identity (agent, ocean_scores, updated_at)
                   VALUES ($1, $2, $3)""",
                self._agent, ocean_json, now,
            )

    async def update_ocean(
        self, deltas: dict[str, float], *, cap: float | None = None
    ) -> dict[str, float]:
        """Apply drift deltas to OCEAN scores. Caps per-dimension change. Returns new scores."""
        drift_cap = cap or self._config.ocean_drift_cap
        current = await self.get_ocean() or {"O": 0.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5}

        new_scores = {}
        for key in ("O", "C", "E", "A", "N"):
            delta = deltas.get(key, 0.0)
            clamped_delta = max(-drift_cap, min(drift_cap, delta))
            new_val = max(0.0, min(1.0, current.get(key, 0.5) + clamped_delta))
            new_scores[key] = round(new_val, 4)

        await self.set_ocean(new_scores)
        return new_scores

    async def get_relationships(self) -> list[dict[str, Any]]:
        """Get all relationships for this agent."""
        return await self._db.fetchall(
            "SELECT * FROM relationships WHERE agent = $1 ORDER BY trust_level DESC",
            self._agent,
        )

    async def set_relationship(
        self,
        person: str,
        *,
        trust_level: float = 0.5,
        style: str = "default",
        dynamic: str = "",
    ) -> None:
        """Create or update a relationship."""
        now = _now_iso()
        existing = await self._db.fetchone(
            "SELECT id FROM relationships WHERE agent = $1 AND person = $2",
            self._agent, person,
        )
        if existing:
            await self._db.execute(
                """UPDATE relationships
                   SET trust_level = $1, style = $2, dynamic = $3, updated_at = $4
                   WHERE agent = $5 AND person = $6""",
                trust_level, style, dynamic, now, self._agent, person,
            )
        else:
            await self._db.execute(
                """INSERT INTO relationships (agent, person, trust_level, style, dynamic, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                self._agent, person, trust_level, style, dynamic, now,
            )
