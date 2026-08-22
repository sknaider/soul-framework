"""Bind one persistent database to one SIA-verified SOUL identity."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from soul_framework.identity.dni import VerifiedSoulDNI


_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS soul_identity_binding (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    soul_dni TEXT NOT NULL,
    soul_id TEXT NOT NULL,
    machine_soul_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
)
"""
_CONTENT_COUNT_SQL = """
SELECT
    (SELECT COUNT(*) FROM memories) +
    (SELECT COUNT(*) FROM identity) +
    (SELECT COUNT(*) FROM relationships) +
    (SELECT COUNT(*) FROM rules) +
    (SELECT COUNT(*) FROM inner_monologue) +
    (SELECT COUNT(*) FROM diary) +
    (SELECT COUNT(*) FROM instincts) +
    (SELECT COUNT(*) FROM working_state) +
    (SELECT COUNT(*) FROM procedural_memories)
"""


def _matches(row: dict[str, Any], dni: VerifiedSoulDNI) -> bool:
    return (
        str(row.get("soul_dni") or "") == dni.soul_dni
        and str(row.get("soul_id") or "") == dni.soul_id
        and str(row.get("machine_soul_id") or "") == dni.machine_soul_id
    )


async def ensure_backend_identity_binding(
    backend: Any, dni: VerifiedSoulDNI
) -> None:
    """Bind an empty database once; reject unbound legacy or foreign souls."""

    await backend.execute(_BINDING_DDL)
    row = await backend.fetchone(
        "SELECT soul_dni, soul_id, machine_soul_id "
        "FROM soul_identity_binding WHERE singleton = 1"
    )
    if row is None:
        total = int(await backend.fetchval(_CONTENT_COUNT_SQL) or 0)
        if total:
            raise PermissionError(
                "legacy SOUL database requires explicit owner-approved DNI enrollment"
            )
        await backend.execute(
            "INSERT INTO soul_identity_binding "
            "(singleton, soul_dni, soul_id, machine_soul_id, bound_at) "
            "VALUES (1, $1, $2, $3, $4) ON CONFLICT (singleton) DO NOTHING",
            dni.soul_dni,
            dni.soul_id,
            dni.machine_soul_id,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        row = await backend.fetchone(
            "SELECT soul_dni, soul_id, machine_soul_id "
            "FROM soul_identity_binding WHERE singleton = 1"
        )
    if row is None or not _matches(row, dni):
        raise PermissionError("SOUL database belongs to another sovereign DNI")


def enroll_legacy_sqlite_identity_binding(
    database: str | os.PathLike[str], dni: VerifiedSoulDNI
) -> bool:
    """Explicitly bind one existing SQLite soul during the owner migration flow."""

    path = Path(database).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("legacy SOUL database must be an absolute regular file")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("legacy SOUL database path must not contain symlinks")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_BINDING_DDL)
        raw = connection.execute(
            "SELECT soul_dni, soul_id, machine_soul_id "
            "FROM soul_identity_binding WHERE singleton = 1"
        ).fetchone()
        if raw is not None:
            row = dict(raw)
            if not _matches(row, dni):
                raise PermissionError("legacy database is already bound to another DNI")
            connection.commit()
            return False
        connection.execute(
            "INSERT INTO soul_identity_binding "
            "(singleton, soul_dni, soul_id, machine_soul_id, bound_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (
                dni.soul_dni,
                dni.soul_id,
                dni.machine_soul_id,
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
