"""Tests for SQLite backend — schema creation, CRUD, param translation."""

from __future__ import annotations

import pytest

from soul_framework.backend.sqlite import SqliteBackend, _translate_params


class TestParamTranslation:
    """Test $N → ? parameter translation."""

    def test_no_params(self):
        sql, params = _translate_params("SELECT * FROM t", ())
        assert sql == "SELECT * FROM t"
        assert params == ()

    def test_sequential_params(self):
        sql, params = _translate_params(
            "INSERT INTO t (a, b) VALUES ($1, $2)", ("x", "y")
        )
        assert sql == "INSERT INTO t (a, b) VALUES (?, ?)"
        assert params == ("x", "y")

    def test_reordered_params(self):
        sql, params = _translate_params(
            "UPDATE t SET a = $2 WHERE b = $1", ("where_val", "set_val")
        )
        assert sql == "UPDATE t SET a = ? WHERE b = ?"
        assert params == ("set_val", "where_val")

    def test_repeated_params(self):
        sql, params = _translate_params(
            "SELECT * FROM t WHERE a = $1 OR b = $1", ("val",)
        )
        assert sql == "SELECT * FROM t WHERE a = ? OR b = ?"
        assert params == ("val", "val")

    def test_no_dollar_passthrough(self):
        sql, params = _translate_params("SELECT 1", ())
        assert sql == "SELECT 1"


class TestSqliteBackend:
    """Test SqliteBackend CRUD operations."""

    @pytest.fixture
    async def db(self):
        backend = SqliteBackend(":memory:")
        await backend.initialize()
        yield backend
        await backend.close()

    async def test_schema_created(self, db):
        """All expected tables exist after initialize."""
        tables = await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {t["name"] for t in tables}
        expected = {"memories", "identity", "relationships", "rules",
                    "inner_monologue", "diary", "instincts", "working_state"}
        assert expected.issubset(names)

    async def test_execute_and_fetchone(self, db):
        await db.execute(
            "INSERT INTO rules (agent, rule_key, content, created_at) VALUES ($1, $2, $3, $4)",
            "test", "r1", "Do the thing", "2026-01-01",
        )
        row = await db.fetchone(
            "SELECT * FROM rules WHERE agent = $1 AND rule_key = $2",
            "test", "r1",
        )
        assert row is not None
        assert row["content"] == "Do the thing"

    async def test_fetchall(self, db):
        await db.execute(
            "INSERT INTO rules (agent, rule_key, content, created_at) VALUES ($1, $2, $3, $4)",
            "test", "r1", "Rule 1", "2026-01-01",
        )
        await db.execute(
            "INSERT INTO rules (agent, rule_key, content, created_at) VALUES ($1, $2, $3, $4)",
            "test", "r2", "Rule 2", "2026-01-01",
        )
        rows = await db.fetchall("SELECT * FROM rules WHERE agent = $1", "test")
        assert len(rows) == 2

    async def test_fetchval(self, db):
        await db.execute(
            "INSERT INTO rules (agent, rule_key, content, created_at) VALUES ($1, $2, $3, $4)",
            "test", "r1", "Rule 1", "2026-01-01",
        )
        count = await db.fetchval("SELECT COUNT(*) FROM rules WHERE agent = $1", "test")
        assert count == 1

    async def test_fetchone_no_results(self, db):
        row = await db.fetchone("SELECT * FROM rules WHERE agent = $1", "nobody")
        assert row is None

    async def test_not_initialized_raises(self):
        db = SqliteBackend(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            await db.execute("SELECT 1")

    async def test_returning_write_persists_to_file_across_reopen(self, tmp_path):
        """Regression: an INSERT ... RETURNING goes through fetchone(), which must commit.

        The old code only committed in execute(), so RETURNING writes (e.g.
        MemoryStore.store) were rolled back on close and never reached the file. This
        only surfaces with a FILE db across separate connections — never with :memory:.
        """
        path = str(tmp_path / "persist.db")

        write = SqliteBackend(path)
        await write.initialize()
        row = await write.fetchone(
            "INSERT INTO rules (agent, rule_key, content, created_at) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            "test", "r1", "survives restart", "2026-01-01",
        )
        assert row and row["id"] >= 1
        await write.close()

        # Fresh connection to the same file — the row must still be there.
        read = SqliteBackend(path)
        await read.initialize()
        got = await read.fetchone(
            "SELECT content FROM rules WHERE agent = $1 AND rule_key = $2", "test", "r1",
        )
        await read.close()
        assert got is not None, "RETURNING write did not persist to the file"
        assert got["content"] == "survives restart"
