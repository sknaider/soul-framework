"""SQLite backend — zero-config default storage for SOUL Framework."""

from __future__ import annotations

import re
from typing import Any

import aiosqlite

from soul_framework.backend.schema import SCHEMA_SQL

# Translate $1, $2, ... (asyncpg style) to ? (sqlite style)
_PG_PARAM_RE = re.compile(r"\$(\d+)")


def _translate_params(sql: str, params: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
    """Convert $N parameter placeholders to ? and reorder params accordingly."""
    if "$" not in sql:
        return sql, params

    # Find all $N references and their order
    refs = [(m.start(), int(m.group(1))) for m in _PG_PARAM_RE.finditer(sql)]
    if not refs:
        return sql, params

    # Build reordered params based on $N order of appearance
    reordered = tuple(params[idx - 1] for _, idx in refs)
    translated_sql = _PG_PARAM_RE.sub("?", sql)
    return translated_sql, reordered


class SqliteBackend:
    """SQLite backend using aiosqlite. Supports in-memory and file-based DBs."""

    def __init__(self, url: str = ":memory:") -> None:
        self._url = url
        self._db: aiosqlite.Connection | None = None

    @property
    def url(self) -> str:
        return self._url

    async def initialize(self) -> None:
        """Open connection and create tables."""
        self._db = await aiosqlite.connect(self._url)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._db

    async def execute(self, sql: str, *params: Any) -> None:
        """Execute a write query."""
        translated_sql, translated_params = _translate_params(sql, params)
        await self._conn().execute(translated_sql, translated_params)
        await self._conn().commit()

    async def fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        """Fetch a single row as dict.

        Also commits: writes that return a value use ``INSERT ... RETURNING`` through
        this method (e.g. MemoryStore.store), so without the commit those rows would be
        rolled back on close and never persist to a file. After a plain SELECT the commit
        is a no-op (no open transaction).
        """
        translated_sql, translated_params = _translate_params(sql, params)
        cursor = await self._conn().execute(translated_sql, translated_params)
        row = await cursor.fetchone()
        await self._conn().commit()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        translated_sql, translated_params = _translate_params(sql, params)
        cursor = await self._conn().execute(translated_sql, translated_params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def fetchval(self, sql: str, *params: Any) -> Any:
        """Fetch a single scalar value. Commits for the same reason as fetchone()."""
        translated_sql, translated_params = _translate_params(sql, params)
        cursor = await self._conn().execute(translated_sql, translated_params)
        row = await cursor.fetchone()
        await self._conn().commit()
        if row is None:
            return None
        return row[0]

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
