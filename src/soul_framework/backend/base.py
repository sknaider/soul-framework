"""Backend abstraction — Protocol class for storage backends."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BackendBase(Protocol):
    """Abstract storage backend. Implementations: SQLite (v0.2)."""

    async def initialize(self) -> None:
        """Create tables if not exist, run migrations."""
        ...

    async def execute(self, sql: str, *params: Any) -> None:
        """Execute a write query (INSERT, UPDATE, DELETE)."""
        ...

    async def fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        """Fetch a single row as dict, or None."""
        ...

    async def fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Fetch all matching rows as list of dicts."""
        ...

    async def fetchval(self, sql: str, *params: Any) -> Any:
        """Fetch a single scalar value."""
        ...

    async def close(self) -> None:
        """Close connections and release resources."""
        ...
