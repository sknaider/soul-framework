"""PostgreSQL + pgvector backend for SOUL Framework.

Requires ``pip install soul-framework[postgres]``.  The DSN is always supplied
by the caller; it is never persisted or included in raised error messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from soul_framework.backend.postgres_schema import (
    POSTGRES_COLUMN_TYPES,
    POSTGRES_COLUMN_DEFAULTS,
    POSTGRES_IDENTITY_COLUMNS,
    POSTGRES_NOT_NULL,
    POSTGRES_REQUIRED_CONSTRAINTS,
    REQUIRED_COLUMNS,
    REQUIRED_TABLES,
    postgres_schema_sql,
)

try:
    import asyncpg
    from pgvector import Vector
    from pgvector.asyncpg import register_vector
except ImportError:  # pragma: no cover - exercised without optional extra
    asyncpg = None  # type: ignore[assignment]
    Vector = None  # type: ignore[assignment,misc]
    register_vector = None  # type: ignore[assignment]


_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class PostgresBackend:
    """Async PostgreSQL backend with indexed cosine search through pgvector."""

    def __init__(
        self,
        url: str,
        *,
        dimensions: int,
        schema: str = "soul_framework",
        auto_migrate: bool = True,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if asyncpg is None or Vector is None or register_vector is None:
            raise ImportError(
                "asyncpg and pgvector are required. "
                "Install with: pip install soul-framework[postgres]"
            )
        if not url:
            raise ValueError("backend_url is required for the postgres backend")
        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("postgres_schema must be a valid unquoted identifier")
        if dimensions <= 0 or dimensions > 2000:
            raise ValueError("PostgreSQL vector dimensions must be between 1 and 2000")
        if min_size < 1 or max_size < min_size:
            raise ValueError("PostgreSQL pool sizes must satisfy 1 <= min_size <= max_size")

        self._url = url
        self._dimensions = dimensions
        self._schema = schema
        self._quoted_schema = f'"{schema}"'
        self._auto_migrate = auto_migrate
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Validate pgvector, migrate idempotently, then open the runtime pool."""
        async with self._initialize_lock:
            if self._pool is not None:
                return
            await self._initialize_once()

    async def _initialize_once(self) -> None:
        conn = None
        initialization_error: RuntimeError | None = None
        try:
            conn = await asyncpg.connect(
                self._url,
                server_settings={"application_name": "soul-framework-migrate"},
            )
            has_vector = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            if not has_vector:
                raise RuntimeError(
                    "PostgreSQL extension 'vector' is missing; an administrator must run "
                    "CREATE EXTENSION vector"
                )
            extension_version = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            version_parts = tuple(
                int(part) for part in re.findall(r"\d+", extension_version or "")[:2]
            )
            if version_parts < (0, 8):
                raise RuntimeError(
                    "pgvector 0.8.0 or newer is required for filtered iterative HNSW scans"
                )
            if self._auto_migrate:
                await self._migrate(conn)
            else:
                await self._validate_schema(conn)
        except RuntimeError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            initialization_error = RuntimeError(
                f"PostgreSQL initialization failed ({name}); DSN credentials were redacted"
            )
        finally:
            if conn is not None:
                await conn.close()
        if initialization_error is not None:
            raise initialization_error

        async def init_connection(pool_conn: Any) -> None:
            await register_vector(pool_conn)

        async def setup_connection(pool_conn: Any) -> None:
            # asyncpg resets session settings when a connection returns to the
            # pool, so search_path belongs in ``setup`` (every acquire), not
            # only in ``init`` (once per physical connection).
            await pool_conn.execute(f"SET search_path TO {self._quoted_schema}, public")

        pool_error: RuntimeError | None = None
        try:
            self._pool = await asyncpg.create_pool(
                self._url,
                min_size=self._min_size,
                max_size=self._max_size,
                init=init_connection,
                setup=setup_connection,
                server_settings={"application_name": "soul-framework"},
            )
        except Exception as exc:
            name = type(exc).__name__
            pool_error = RuntimeError(
                f"PostgreSQL pool creation failed ({name}); DSN credentials were redacted"
            )
        if pool_error is not None:
            raise pool_error

    async def _migrate(self, conn: Any) -> None:
        ddl = postgres_schema_sql(self._dimensions)
        checksum = hashlib.sha256(ddl.encode("utf-8")).hexdigest()
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('soul-framework-schema-v1'))")
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._quoted_schema}")
            await conn.execute(f"SET LOCAL search_path TO {self._quoted_schema}, public")
            # Read the contract before applying mutable DDL. A checksum/dimension
            # mismatch must fail without partially changing an existing schema.
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, "
                "embedding_dimensions INTEGER NOT NULL, applied_at TEXT NOT NULL)"
            )
            row = await conn.fetchrow(
                "SELECT checksum, embedding_dimensions FROM schema_migrations WHERE version = 1"
            )
            if row is not None and row["embedding_dimensions"] != self._dimensions:
                raise RuntimeError(
                    "Embedding dimension mismatch: database uses "
                    f"{row['embedding_dimensions']}, provider uses {self._dimensions}"
                )
            if row is not None and row["checksum"] != checksum:
                raise RuntimeError("PostgreSQL schema checksum mismatch; migration drift detected")

            await self._validate_existing_table_columns(conn)
            await conn.execute(ddl)
            await self._validate_schema_contract(conn)
            if row is None:
                await conn.execute(
                    "INSERT INTO schema_migrations(version, checksum, embedding_dimensions, applied_at) "
                    "VALUES (1, $1, $2, $3)",
                    checksum,
                    self._dimensions,
                    datetime.now(timezone.utc).isoformat(),
                )

    async def _validate_schema(self, conn: Any) -> None:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = $1)",
            self._schema,
        )
        if not exists:
            raise RuntimeError(f"PostgreSQL schema '{self._schema}' does not exist")
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = $1",
            self._schema,
        )
        present = {row["table_name"] for row in rows}
        missing = sorted(REQUIRED_TABLES - present)
        if missing:
            raise RuntimeError(f"PostgreSQL schema is incomplete; missing: {', '.join(missing)}")
        await conn.execute(f"SET search_path TO {self._quoted_schema}, public")
        receipt = await conn.fetchrow(
            "SELECT checksum, embedding_dimensions FROM schema_migrations WHERE version = 1"
        )
        if receipt is None:
            raise RuntimeError("PostgreSQL schema has no version 1 migration receipt")
        dimensions = receipt["embedding_dimensions"]
        if dimensions != self._dimensions:
            raise RuntimeError(
                f"Embedding dimension mismatch: database uses {dimensions}, "
                f"provider uses {self._dimensions}"
            )
        expected_checksum = hashlib.sha256(
            postgres_schema_sql(self._dimensions).encode("utf-8")
        ).hexdigest()
        if receipt["checksum"] != expected_checksum:
            raise RuntimeError("PostgreSQL schema checksum mismatch; migration drift detected")
        await self._validate_schema_contract(conn)

    async def _validate_schema_contract(self, conn: Any) -> None:
        table_rows = await conn.fetch(
            "SELECT c.relname AS table_name, c.relkind AS table_kind "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = ANY($2::text[])",
            self._schema,
            list(REQUIRED_TABLES),
        )
        table_kinds = {}
        for row in table_rows:
            table_kind = row["table_kind"]
            if isinstance(table_kind, bytes):
                table_kind = table_kind.decode("ascii").rstrip("\x00")
            table_kinds[row["table_name"]] = table_kind
        invalid_tables = [
            f"{table}:kind={table_kinds.get(table)!r} expected='r'"
            for table in sorted(REQUIRED_TABLES)
            if table_kinds.get(table) != "r"
        ]
        if invalid_tables:
            raise RuntimeError(
                "PostgreSQL schema contract mismatch: " + ", ".join(invalid_tables)
            )

        rows = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = $1",
            self._schema,
        )
        actual: dict[str, set[str]] = {}
        for row in rows:
            actual.setdefault(row["table_name"], set()).add(row["column_name"])
        missing_columns = []
        for table, required in REQUIRED_COLUMNS.items():
            for column in sorted(required - actual.get(table, set())):
                missing_columns.append(f"{table}.{column}")
        if missing_columns:
            raise RuntimeError(
                "PostgreSQL schema is incomplete; missing columns: "
                + ", ".join(missing_columns)
            )

        catalog_rows = await conn.fetch(
            "SELECT c.relname AS table_name, a.attname AS column_name, "
            "format_type(a.atttypid, a.atttypmod) AS pg_type, "
            "a.attnotnull AS not_null, a.attidentity AS identity_kind "
            ", pg_get_expr(default_value.adbin, default_value.adrelid) AS default_expr "
            "FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_attrdef default_value ON default_value.adrelid = a.attrelid "
            "AND default_value.adnum = a.attnum "
            "WHERE n.nspname = $1 AND c.relkind = 'r' "
            "AND a.attnum > 0 AND NOT a.attisdropped",
            self._schema,
        )
        catalog = {
            (row["table_name"], row["column_name"]): row for row in catalog_rows
        }
        mismatches = []
        for table, columns in POSTGRES_COLUMN_TYPES.items():
            for column, expected_type in columns.items():
                row = catalog.get((table, column))
                if row is None:
                    mismatches.append(f"{table}.{column}:missing catalog row")
                    continue
                if expected_type == "vector":
                    expected_type = f"vector({self._dimensions})"
                if row["pg_type"] != expected_type:
                    mismatches.append(
                        f"{table}.{column}:type={row['pg_type']} expected={expected_type}"
                    )
                expected_not_null = column in POSTGRES_NOT_NULL.get(table, set())
                if row["not_null"] != expected_not_null:
                    mismatches.append(
                        f"{table}.{column}:not_null={row['not_null']} "
                        f"expected={expected_not_null}"
                    )
                expected_identity = "d" if (table, column) in POSTGRES_IDENTITY_COLUMNS else ""
                actual_identity = row["identity_kind"]
                if isinstance(actual_identity, bytes):
                    actual_identity = actual_identity.decode("ascii").rstrip("\x00")
                if actual_identity != expected_identity:
                    mismatches.append(
                        f"{table}.{column}:identity={actual_identity!r} "
                        f"expected={expected_identity!r}"
                    )
                expected_default = POSTGRES_COLUMN_DEFAULTS.get(table, {}).get(column)
                if row["default_expr"] != expected_default:
                    mismatches.append(
                        f"{table}.{column}:default={row['default_expr']!r} "
                        f"expected={expected_default!r}"
                    )
        if mismatches:
            raise RuntimeError(
                "PostgreSQL schema contract mismatch: " + ", ".join(mismatches)
            )

        constraint_rows = await conn.fetch(
            "SELECT c.relname AS table_name, con.contype AS constraint_type, "
            "con.condeferrable AS is_deferrable, "
            "array_agg(a.attname ORDER BY key_column.ordinality) AS columns "
            "FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY "
            "AS key_column(attnum, ordinality) "
            "JOIN pg_attribute a ON a.attrelid = c.oid "
            "AND a.attnum = key_column.attnum "
            "WHERE n.nspname = $1 AND con.contype IN ('p', 'u') "
            "GROUP BY c.relname, con.oid, con.contype",
            self._schema,
        )
        actual_constraints: dict[str, set[tuple[str, tuple[str, ...]]]] = {}
        for row in constraint_rows:
            constraint_type = row["constraint_type"]
            if isinstance(constraint_type, bytes):
                constraint_type = constraint_type.decode("ascii").rstrip("\x00")
            # PostgreSQL refuses a DEFERRABLE unique/primary constraint as an
            # ON CONFLICT arbiter, even when its columns are otherwise exact.
            if not row["is_deferrable"]:
                actual_constraints.setdefault(row["table_name"], set()).add(
                    (constraint_type, tuple(row["columns"]))
                )
        missing_constraints = []
        for table, required in POSTGRES_REQUIRED_CONSTRAINTS.items():
            for constraint_type, columns in sorted(
                required - actual_constraints.get(table, set())
            ):
                kind = "PRIMARY KEY" if constraint_type == "p" else "UNIQUE"
                missing_constraints.append(f"{table}.{kind}({', '.join(columns)})")
        if missing_constraints:
            raise RuntimeError(
                "PostgreSQL schema is missing required constraint: "
                + ", ".join(missing_constraints)
            )

        for table in ("memories", "procedural_memories"):
            vector_type = await conn.fetchval(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a "
                "WHERE a.attrelid = to_regclass($1) AND a.attname = 'embedding_vector'",
                f"{self._schema}.{table}",
            )
            if vector_type != f"vector({self._dimensions})":
                raise RuntimeError(
                    f"PostgreSQL schema has incompatible {table}.embedding_vector type: "
                    f"{vector_type!r}"
                )

        required_indexes = {
            "idx_memories_embedding_hnsw": "memories",
            "idx_procedures_embedding_hnsw": "procedural_memories",
        }
        index_rows = await conn.fetch(
            "SELECT index_class.relname AS index_name, "
            "table_class.relname AS table_name, access_method.amname AS method, "
            "attribute.attname AS column_name, opclass.opcname AS opclass_name, "
            "index_meta.indisvalid AS is_valid, index_meta.indisready AS is_ready, "
            "index_meta.indnatts AS attribute_count, "
            "index_meta.indnkeyatts AS key_count, "
            "pg_get_expr(index_meta.indpred, index_meta.indrelid) AS predicate "
            "FROM pg_index index_meta "
            "JOIN pg_class index_class ON index_class.oid = index_meta.indexrelid "
            "JOIN pg_class table_class ON table_class.oid = index_meta.indrelid "
            "JOIN pg_namespace namespace ON namespace.oid = table_class.relnamespace "
            "JOIN pg_am access_method ON access_method.oid = index_class.relam "
            "JOIN pg_attribute attribute ON attribute.attrelid = table_class.oid "
            "AND attribute.attnum = index_meta.indkey[0] "
            "JOIN pg_opclass opclass ON opclass.oid = index_meta.indclass[0] "
            "WHERE namespace.nspname = $1 "
            "AND index_class.relname = ANY($2::text[])",
            self._schema,
            list(required_indexes),
        )
        indexes = {row["index_name"]: row for row in index_rows}
        for name, table in required_indexes.items():
            index = indexes.get(name)
            valid = (
                index is not None
                and index["table_name"] == table
                and index["method"] == "hnsw"
                and index["column_name"] == "embedding_vector"
                and index["opclass_name"] == "vector_cosine_ops"
                and index["is_valid"]
                and index["is_ready"]
                and index["attribute_count"] == 1
                and index["key_count"] == 1
                and index["predicate"] is None
            )
            if not valid:
                raise RuntimeError(f"PostgreSQL schema is missing required HNSW index: {name}")

    async def _validate_existing_table_columns(self, conn: Any) -> None:
        """Reject partial legacy tables before idempotent DDL can fail opaquely."""
        rows = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = $1",
            self._schema,
        )
        actual: dict[str, set[str]] = {}
        for row in rows:
            actual.setdefault(row["table_name"], set()).add(row["column_name"])
        missing_columns = []
        for table, present in actual.items():
            required = REQUIRED_COLUMNS.get(table)
            if required is None:
                continue
            for column in sorted(required - present):
                missing_columns.append(f"{table}.{column}")
        if missing_columns:
            raise RuntimeError(
                "PostgreSQL schema is incomplete; missing columns: "
                + ", ".join(missing_columns)
            )

    def _get_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._pool

    async def execute(self, sql: str, *params: Any) -> None:
        failure: RuntimeError | None = None
        try:
            async with self._get_pool().acquire() as conn:
                await conn.execute(sql, *params)
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure

    async def fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        failure: RuntimeError | None = None
        result: dict[str, Any] | None = None
        try:
            async with self._get_pool().acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                result = dict(row) if row is not None else None
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return result

    async def fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        failure: RuntimeError | None = None
        result: list[dict[str, Any]] = []
        try:
            async with self._get_pool().acquire() as conn:
                result = [dict(row) for row in await conn.fetch(sql, *params)]
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return result

    async def fetchval(self, sql: str, *params: Any) -> Any:
        failure: RuntimeError | None = None
        result: Any = None
        try:
            async with self._get_pool().acquire() as conn:
                result = await conn.fetchval(sql, *params)
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return result

    async def set_memory_vector(self, memory_id: int, agent: str, vector: list[float]) -> None:
        self._check_vector(vector)
        stored_vector = None if self._is_zero_vector(vector) else Vector(vector)
        failure: RuntimeError | None = None
        status = ""
        try:
            async with self._get_pool().acquire() as conn:
                status = await conn.execute(
                    "UPDATE memories SET embedding_vector = $1 WHERE id = $2 AND agent = $3",
                    stored_vector, memory_id, agent,
                )
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        if status != "UPDATE 1":
            raise RuntimeError("Memory disappeared before its vector was persisted")

    async def set_procedure_vector(self, procedure_id: int, agent: str, vector: list[float]) -> None:
        self._check_vector(vector)
        stored_vector = None if self._is_zero_vector(vector) else Vector(vector)
        failure: RuntimeError | None = None
        status = ""
        try:
            async with self._get_pool().acquire() as conn:
                status = await conn.execute(
                    "UPDATE procedural_memories SET embedding_vector = $1 "
                    "WHERE id = $2 AND agent = $3",
                    stored_vector, procedure_id, agent,
                )
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        if status != "UPDATE 1":
            raise RuntimeError("Procedure disappeared before its vector was persisted")

    async def insert_memory_with_vector(
        self, values: tuple[Any, ...], vector: list[float]
    ) -> int:
        """Insert a memory and both embedding representations atomically."""
        self._check_vector(vector)
        stored_vector = None if self._is_zero_vector(vector) else Vector(vector)
        sql = """INSERT INTO memories
            (agent, category, content, embedding, embedding_vector, importance,
             valence, arousal, dominance, source, scope, confidence_score,
             utility_score, event_time, episode_context, metadata, valid_from, created_at)
            VALUES ($1,$2,$3,$4,$18,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            RETURNING id"""
        failure: RuntimeError | None = None
        memory_id = 0
        try:
            async with self._get_pool().acquire() as conn:
                async with conn.transaction():
                    memory_id = int(await conn.fetchval(sql, *values, stored_vector))
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return memory_id

    async def update_memory_fields(
        self,
        memory_id: int,
        agent: str,
        changes: dict[str, Any],
        vector: list[float] | None,
    ) -> bool:
        """Apply a complete memory patch, including its vector, in one statement."""
        allowed = {
            "content", "embedding", "importance", "category", "utility_score",
            "confidence_score", "metadata",
        }
        if not changes or not set(changes).issubset(allowed):
            raise ValueError("Unsupported or empty PostgreSQL memory update")
        sets: list[str] = []
        params: list[Any] = []
        for idx, (column, value) in enumerate(changes.items(), start=1):
            sets.append(f"{column} = ${idx}")
            params.append(value)
        if vector is not None:
            self._check_vector(vector)
            stored_vector = None if self._is_zero_vector(vector) else Vector(vector)
            sets.append(f"embedding_vector = ${len(params) + 1}")
            params.append(stored_vector)
        id_idx = len(params) + 1
        params.extend((memory_id, agent))
        failure: RuntimeError | None = None
        status = ""
        try:
            async with self._get_pool().acquire() as conn:
                status = await conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} "
                    f"WHERE id = ${id_idx} AND agent = ${id_idx + 1}",
                    *params,
                )
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return status == "UPDATE 1"

    async def insert_procedure_with_vector(
        self, values: tuple[Any, ...], vector: list[float]
    ) -> int:
        """Insert a procedural memory and both vector forms atomically."""
        self._check_vector(vector)
        stored_vector = None if self._is_zero_vector(vector) else Vector(vector)
        sql = """INSERT INTO procedural_memories
            (agent, task_type, task_description, workflow, facts, embedding,
             embedding_vector, hit_count, success_count, fail_count, source_task,
             build_policy, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$14,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id"""
        failure: RuntimeError | None = None
        procedure_id = 0
        try:
            async with self._get_pool().acquire() as conn:
                async with conn.transaction():
                    procedure_id = int(await conn.fetchval(sql, *values, stored_vector))
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return procedure_id

    async def search_memory_vectors(
        self,
        agent: str,
        vector: list[float],
        *,
        category: str = "",
        min_importance: int = 0,
        scope: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._check_vector(vector)
        if self._is_zero_vector(vector):
            return await self._search_memories_without_signal(
                agent,
                category=category,
                min_importance=min_importance,
                scope=scope,
                limit=limit,
            )
        conditions = ["agent = $1", "invalid_at IS NULL", "embedding_vector IS NOT NULL"]
        params: list[Any] = [agent]
        idx = 2
        for expression, value in (
            ("category", category),
            ("importance", min_importance),
            ("scope", scope),
        ):
            if value:
                operator = ">=" if expression == "importance" else "="
                conditions.append(f"{expression} {operator} ${idx}")
                params.append(value)
                idx += 1
        vector_idx = idx
        params.append(Vector(vector))
        idx += 1
        params.append(max(1, limit))
        sql = (
            "SELECT *, 1 - (embedding_vector <=> $" + str(vector_idx) + ") AS _vector_similarity "
            "FROM memories WHERE " + " AND ".join(conditions) +
            " ORDER BY embedding_vector <=> $" + str(vector_idx) + f" LIMIT ${idx}"
        )
        vector_rows = await self._fetch_vector_query(sql, params)
        zero_rows = await self._search_zero_memory_vectors(
            agent,
            category=category,
            min_importance=min_importance,
            scope=scope,
            limit=limit,
        )
        return vector_rows + zero_rows

    async def search_procedure_vectors(
        self,
        agent: str,
        vector: list[float],
        *,
        task_type: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._check_vector(vector)
        if self._is_zero_vector(vector):
            conditions = ["agent = $1", "embedding IS NOT NULL"]
            params: list[Any] = [agent]
            idx = 2
            if task_type:
                conditions.append(f"task_type = ${idx}")
                params.append(task_type)
                idx += 1
            params.append(max(1, limit))
            return await self.fetchall(
                "SELECT *, 0.0::double precision AS _vector_similarity "
                "FROM procedural_memories WHERE " + " AND ".join(conditions) +
                f" ORDER BY created_at DESC LIMIT ${idx}",
                *params,
            )
        conditions = ["agent = $1", "embedding_vector IS NOT NULL"]
        params: list[Any] = [agent]
        idx = 2
        if task_type:
            conditions.append(f"task_type = ${idx}")
            params.append(task_type)
            idx += 1
        vector_idx = idx
        params.append(Vector(vector))
        idx += 1
        params.append(max(1, limit))
        sql = (
            "SELECT *, 1 - (embedding_vector <=> $" + str(vector_idx) + ") AS _vector_similarity "
            "FROM procedural_memories WHERE " + " AND ".join(conditions) +
            " ORDER BY embedding_vector <=> $" + str(vector_idx) + f" LIMIT ${idx}"
        )
        vector_rows = await self._fetch_vector_query(sql, params)
        zero_conditions = ["agent = $1", "embedding_vector IS NULL", "embedding IS NOT NULL"]
        zero_params: list[Any] = [agent]
        zero_idx = 2
        if task_type:
            zero_conditions.append(f"task_type = ${zero_idx}")
            zero_params.append(task_type)
            zero_idx += 1
        zero_params.append(max(1, limit))
        zero_rows = await self.fetchall(
            "SELECT *, 0.0::double precision AS _vector_similarity "
            "FROM procedural_memories WHERE " + " AND ".join(zero_conditions) +
            f" ORDER BY created_at DESC LIMIT ${zero_idx}",
            *zero_params,
        )
        return vector_rows + zero_rows

    async def _search_zero_memory_vectors(
        self,
        agent: str,
        *,
        category: str,
        min_importance: int,
        scope: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = [
            "agent = $1", "invalid_at IS NULL", "embedding_vector IS NULL",
            "embedding IS NOT NULL",
        ]
        params: list[Any] = [agent]
        idx = 2
        for expression, value in (
            ("category", category),
            ("importance", min_importance),
            ("scope", scope),
        ):
            if value:
                operator = ">=" if expression == "importance" else "="
                conditions.append(f"{expression} {operator} ${idx}")
                params.append(value)
                idx += 1
        params.append(max(1, limit))
        return await self.fetchall(
            "SELECT *, 0.0::double precision AS _vector_similarity "
            "FROM memories WHERE " + " AND ".join(conditions) +
            f" ORDER BY created_at DESC LIMIT ${idx}",
            *params,
        )

    async def _search_memories_without_signal(
        self,
        agent: str,
        *,
        category: str,
        min_importance: int,
        scope: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = ["agent = $1", "invalid_at IS NULL", "embedding IS NOT NULL"]
        params: list[Any] = [agent]
        idx = 2
        for expression, value in (
            ("category", category),
            ("importance", min_importance),
            ("scope", scope),
        ):
            if value:
                operator = ">=" if expression == "importance" else "="
                conditions.append(f"{expression} {operator} ${idx}")
                params.append(value)
                idx += 1
        params.append(max(1, limit))
        return await self.fetchall(
            "SELECT *, 0.0::double precision AS _vector_similarity "
            "FROM memories WHERE " + " AND ".join(conditions) +
            f" ORDER BY created_at DESC LIMIT ${idx}",
            *params,
        )

    async def _fetch_vector_query(
        self, sql: str, params: list[Any]
    ) -> list[dict[str, Any]]:
        """Run filtered HNSW with iterative scans so post-filters cannot hide hits."""
        failure: RuntimeError | None = None
        result: list[dict[str, Any]] = []
        try:
            async with self._get_pool().acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                    rows = await conn.fetch(sql, *params)
                    result = [dict(row) for row in rows]
        except Exception as exc:
            failure = self._redacted_operation_error(exc)
        if failure is not None:
            raise failure
        return result

    def _check_vector(self, vector: list[float]) -> None:
        if len(vector) != self._dimensions:
            raise ValueError(
                f"Embedding provider returned {len(vector)} dimensions; "
                f"PostgreSQL schema requires {self._dimensions}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding vectors must contain only finite numbers")

    @staticmethod
    def _is_zero_vector(vector: list[float]) -> bool:
        return not any(value != 0.0 for value in vector)

    @staticmethod
    def _redacted_operation_error(exc: Exception) -> RuntimeError:
        return RuntimeError(
            f"PostgreSQL operation failed ({type(exc).__name__}); "
            "query parameters and DSN credentials were redacted"
        )

    async def close(self) -> None:
        async with self._initialize_lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
