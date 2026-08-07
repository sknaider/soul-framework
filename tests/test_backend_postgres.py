"""PostgreSQL/pgvector integration tests.

Set ``SOUL_TEST_POSTGRES_DSN`` to run these against an isolated PostgreSQL
database with the vector extension installed.  Each test owns and removes only
its randomly named schema.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import uuid
from urllib.parse import urlsplit

import pytest

from soul_framework import Soul
from soul_framework.backend.postgres import PostgresBackend
from soul_framework.config import SoulConfig


POSTGRES_DSN = os.getenv("SOUL_TEST_POSTGRES_DSN", "")
RESTRICTED_DSN = os.getenv("SOUL_TEST_POSTGRES_RESTRICTED_DSN", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="SOUL_TEST_POSTGRES_DSN is not configured",
)


class MeaningEmbedding:
    """Controlled semantic provider: synonyms share vectors, not tokens."""

    @property
    def dimensions(self) -> int:
        return 3

    async def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if any(word in lowered for word in ("canine", "dog", "pet")):
            return [1.0, 0.0, 0.0]
        if any(word in lowered for word in ("automobile", "car", "vehicle")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


@pytest.fixture
async def pg_schema():
    import asyncpg

    schema = f"soul_test_{uuid.uuid4().hex}"
    yield schema
    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        # The name is generated locally from a UUID and validated by the backend.
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await conn.close()


def config_for(schema: str, *, dimensions: int = 3) -> SoulConfig:
    return SoulConfig(
        backend="postgres",
        backend_url=POSTGRES_DSN,
        postgres_schema=schema,
        embedding_dimensions=dimensions,
        memory_search_candidate_limit=20,
    )


async def test_soul_create_roundtrip_reconnect_and_pgvector_search(pg_schema, monkeypatch):
    cfg = config_for(pg_schema)
    embedding = MeaningEmbedding()

    soul = await Soul.create("Maya", config=cfg, embedding=embedding)
    canine_id = await soul.memory.store(
        "A canine companion waits beside the blue gate", importance=8
    )
    await soul.memory.store("The automobile needs a battery", importance=5)
    await soul.close()

    reopened = await Soul.create("Maya", config=cfg, embedding=embedding)
    assert (await reopened.memory.get(canine_id)).content.startswith("A canine")
    assert await reopened._backend.fetchval(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ) is False

    # If PostgreSQL accidentally falls back to unpacking every BYTEA in Python,
    # this canary fails. The indexed pgvector result already carries similarity.
    import soul_framework.memory.store as store_module

    monkeypatch.setattr(
        store_module,
        "_unpack_embedding",
        lambda _data: (_ for _ in ()).throw(AssertionError("Python vector scan used")),
    )
    results = await reopened.memory.search("the beloved household pet", limit=1)
    assert results[0].memory.id == canine_id
    assert results[0].similarity == pytest.approx(1.0)

    index_def = await reopened._backend.fetchval(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = $1 AND indexname = 'idx_memories_embedding_hnsw'",
        pg_schema,
    )
    assert "USING hnsw" in index_def
    assert "vector_cosine_ops" in index_def

    await reopened.memory.update(canine_id, content="The automobile is parked indoors")
    vehicle = await reopened.memory.search("a road vehicle", limit=1)
    assert vehicle[0].memory.id == canine_id

    filtered = await reopened.memory.search(
        "a road vehicle", category="fact", min_importance=8, scope="private"
    )
    assert filtered[0].memory.id == canine_id
    await reopened.memory.invalidate(canine_id)
    assert all(hit.memory.id != canine_id for hit in await reopened.memory.search("vehicle"))
    await reopened.close()


async def test_agent_isolation_and_manager_parity(pg_schema):
    cfg = config_for(pg_schema)
    embedding = MeaningEmbedding()
    ada = await Soul.create("ADA", config=cfg, embedding=embedding)
    alice = await Soul.create("ALICE", config=cfg, embedding=embedding)

    private_id = await ada.memory.store("canine private memory", scope="private")
    assert await alice.memory.get(private_id) is None
    assert await alice.memory.search("pet") == []

    await ada.identity.set_ocean({"O": 0.8, "C": 1.0, "E": 0.7, "A": 0.5, "N": 0.2})
    await ada.rules.set("verify", "Test before claiming victory", priority="critical")
    thought_id = await ada.reflect("PostgreSQL survives the full public manager path")
    proc_id = await ada.procedures.store("deploy service", "test then restart")

    assert (await ada.identity.get_ocean())["C"] == 1.0
    assert (await ada.rules.get("verify"))["content"].startswith("Test before")
    assert thought_id > 0
    assert (await ada.procedures.get(proc_id)).workflow == "test then restart"

    await alice.close()
    await ada.close()


async def test_migration_is_idempotent_and_dimension_is_fail_closed(pg_schema):
    first = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    second = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await asyncio.gather(first.initialize(), second.initialize())
    assert await second.fetchval("SELECT COUNT(*) FROM schema_migrations") == 1
    await first.close()
    await second.close()

    wrong = PostgresBackend(POSTGRES_DSN, dimensions=4, schema=pg_schema)
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        await wrong.initialize()

    import asyncpg

    conn = await asyncpg.connect(POSTGRES_DSN)
    await conn.execute(
        f'ALTER TABLE "{pg_schema}".memories '
        "ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::timestamptz"
    )
    await conn.close()
    drifted = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    with pytest.raises(RuntimeError, match="contract mismatch"):
        await drifted.initialize()


async def test_auto_migrate_false_rejects_missing_schema():
    missing = f"soul_missing_{uuid.uuid4().hex}"
    backend = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=missing,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        await backend.initialize()


@pytest.mark.skipif(
    not RESTRICTED_DSN,
    reason="SOUL_TEST_POSTGRES_RESTRICTED_DSN is not configured",
)
async def test_runtime_role_without_ddl_can_use_pre_migrated_schema(pg_schema):
    restricted_role = urlsplit(RESTRICTED_DSN).username or ""
    assert restricted_role.replace("_", "").isalnum()

    owner = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await owner.initialize()
    await owner.execute(f'GRANT USAGE ON SCHEMA "{pg_schema}" TO "{restricted_role}"')
    await owner.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{pg_schema}" '
        f'TO "{restricted_role}"'
    )
    await owner.execute(
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{pg_schema}" '
        f'TO "{restricted_role}"'
    )
    await owner.close()

    cfg = SoulConfig(
        backend="postgres",
        backend_url=RESTRICTED_DSN,
        postgres_schema=pg_schema,
        postgres_auto_migrate=False,
    )
    runtime = await Soul.create("Runtime", config=cfg, embedding=MeaningEmbedding())
    memory_id = await runtime.memory.store("canine memory through restricted role")
    assert (await runtime.memory.search("household pet", limit=1))[0].memory.id == memory_id
    assert await runtime._backend.fetchval(
        "SELECT has_schema_privilege(current_user, $1, 'CREATE')", pg_schema
    ) is False
    assert await runtime._backend.fetchval(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ) is False
    await runtime.close()


def test_invalid_schema_and_missing_dsn_fail_before_connecting():
    with pytest.raises(ValueError, match="backend_url"):
        PostgresBackend("", dimensions=3)
    with pytest.raises(ValueError, match="valid unquoted identifier"):
        PostgresBackend(POSTGRES_DSN, dimensions=3, schema="bad;drop schema public")


async def test_connection_error_redacts_dsn_password():
    secret = "must-not-appear-in-errors"
    dsn = "postgresql" + "://" + "nobody" + ":" + secret + "@127.0.0.1:1/missing"
    backend = PostgresBackend(
        dsn,
        dimensions=3,
    )
    with pytest.raises(RuntimeError) as caught:
        await backend.initialize()
    assert secret not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


async def test_operation_error_drops_sensitive_exception_context(pg_schema):
    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    secret = "SENSITIVE-CONTENT-MUST-NOT-ESCAPE"
    with pytest.raises(RuntimeError) as caught:
        await backend.execute(
            "INSERT INTO memories(agent, category, content, valid_from, created_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            "Redaction", secret, None, "2026-01-01", "2026-01-01",
        )
    assert secret not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    await backend.close()


async def test_zero_and_non_finite_vectors_fail_safely(pg_schema):
    class ZeroEmbedding(MeaningEmbedding):
        async def embed(self, text: str) -> list[float]:
            return [0.0, 0.0, 0.0]

    zero = await Soul.create("Zero", config=config_for(pg_schema), embedding=ZeroEmbedding())
    memory_id = await zero.memory.store("")
    hits = await zero.memory.search("", limit=1)
    assert hits[0].memory.id == memory_id
    assert math.isfinite(hits[0].similarity)
    assert math.isfinite(hits[0].score)
    await zero.close()

    nonzero = await Soul.create("Zero", config=config_for(pg_schema), embedding=MeaningEmbedding())
    carried = await nonzero.memory.search("road vehicle", limit=1)
    assert carried[0].memory.id == memory_id
    assert carried[0].similarity == 0.0
    await nonzero.close()

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    with pytest.raises(ValueError, match="finite"):
        await backend.set_memory_vector(memory_id, "Zero", [float("nan"), 0.0, 0.0])
    await backend.close()


async def test_content_and_both_vectors_update_atomically_under_concurrency(pg_schema):
    cfg = config_for(pg_schema)
    first = await Soul.create("Concurrent", config=cfg, embedding=MeaningEmbedding())
    second = await Soul.create("Concurrent", config=cfg, embedding=MeaningEmbedding())
    memory_id = await first.memory.store("canine initial")

    await asyncio.gather(
        first.memory.update(memory_id, content="canine final"),
        second.memory.update(memory_id, content="automobile final"),
    )
    row = await first._backend.fetchone(
        "SELECT content, embedding, embedding_vector FROM memories WHERE id = $1",
        memory_id,
    )
    packed = struct.unpack("<3f", row["embedding"])
    indexed = tuple(float(value) for value in row["embedding_vector"].to_list())
    expected = (1.0, 0.0, 0.0) if "canine" in row["content"] else (0.0, 1.0, 0.0)
    assert packed == pytest.approx(expected)
    assert indexed == pytest.approx(expected)

    before = dict(row)
    with pytest.raises(RuntimeError):
        await first.memory.update(
            memory_id,
            content="automobile must roll back",
            importance="not-an-integer",  # type: ignore[arg-type]
        )
    after = await first._backend.fetchone(
        "SELECT content, importance, embedding, embedding_vector FROM memories WHERE id = $1",
        memory_id,
    )
    assert after["content"] == before["content"]
    assert after["importance"] == 5
    assert after["embedding"] == before["embedding"]
    assert after["embedding_vector"].to_list() == pytest.approx(
        before["embedding_vector"].to_list()
    )
    await second.close()
    await first.close()


async def test_partial_schema_is_not_certified(pg_schema):
    import asyncpg

    conn = await asyncpg.connect(POSTGRES_DSN)
    await conn.execute(f'CREATE SCHEMA "{pg_schema}"')
    await conn.execute(f'CREATE TABLE "{pg_schema}".memories (id BIGINT PRIMARY KEY)')
    await conn.close()

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    with pytest.raises(RuntimeError, match="missing columns"):
        await backend.initialize()

    conn = await asyncpg.connect(POSTGRES_DSN)
    receipt = await conn.fetchval(
        "SELECT to_regclass($1)", f"{pg_schema}.schema_migrations"
    )
    await conn.close()
    assert receipt is None


async def test_missing_upsert_constraint_is_not_certified(pg_schema):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(
            f'ALTER TABLE "{pg_schema}".relationships '
            "DROP CONSTRAINT relationships_agent_person_key"
        )
    finally:
        await conn.close()

    drifted = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    with pytest.raises(RuntimeError, match="missing required constraint"):
        await drifted.initialize()


async def test_deferrable_upsert_constraint_is_not_certified(pg_schema):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(
            f'ALTER TABLE "{pg_schema}".relationships '
            "DROP CONSTRAINT relationships_agent_person_key"
        )
        await conn.execute(
            f'ALTER TABLE "{pg_schema}".relationships '
            "ADD CONSTRAINT relationships_agent_person_key UNIQUE(agent, person) "
            "DEFERRABLE INITIALLY IMMEDIATE"
        )
    finally:
        await conn.close()

    drifted = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=pg_schema,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="missing required constraint"):
        await drifted.initialize()


async def test_partitioned_required_table_is_not_certified(pg_schema):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(f'ALTER TABLE "{pg_schema}".identity RENAME TO identity_regular')
        await conn.execute(
            f'CREATE TABLE "{pg_schema}".identity ('
            "agent TEXT NOT NULL, personality TEXT DEFAULT '', "
            "boot_context TEXT DEFAULT '', philosophy TEXT DEFAULT '', "
            "ocean_scores TEXT DEFAULT '{}', ocean_baseline TEXT DEFAULT '{}', "
            "updated_at TEXT NOT NULL, PRIMARY KEY(agent)"
            ") PARTITION BY LIST(agent)"
        )
    finally:
        await conn.close()

    partitioned = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=pg_schema,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="contract mismatch"):
        await partitioned.initialize()


async def test_semantic_default_drift_is_not_certified(pg_schema):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(
            f'ALTER TABLE "{pg_schema}".rules ALTER COLUMN active SET DEFAULT 0'
        )
    finally:
        await conn.close()

    drifted = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=pg_schema,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="contract mismatch"):
        await drifted.initialize()


async def test_runtime_validation_rejects_forged_receipt_and_wrong_hnsw_opclass(
    pg_schema,
):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(
            f'UPDATE "{pg_schema}".schema_migrations '
            "SET checksum = 'forged' WHERE version = 1"
        )
    finally:
        await conn.close()

    forged = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=pg_schema,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        await forged.initialize()


async def test_runtime_validation_rejects_wrong_hnsw_opclass(pg_schema):
    import asyncpg

    backend = PostgresBackend(POSTGRES_DSN, dimensions=3, schema=pg_schema)
    await backend.initialize()
    await backend.close()

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        await conn.execute(f'DROP INDEX "{pg_schema}".idx_memories_embedding_hnsw')
        await conn.execute(
            f'CREATE INDEX idx_memories_embedding_hnsw ON "{pg_schema}".memories '
            "USING hnsw (embedding_vector vector_l2_ops) "
            "WHERE category <> 'vector_cosine_ops'"
        )
    finally:
        await conn.close()

    wrong_index = PostgresBackend(
        POSTGRES_DSN,
        dimensions=3,
        schema=pg_schema,
        auto_migrate=False,
    )
    with pytest.raises(RuntimeError, match="missing required HNSW index"):
        await wrong_index.initialize()


async def test_same_backend_initialize_is_single_flight_and_failure_closes_pool(
    pg_schema, monkeypatch
):
    import asyncpg

    backend = PostgresBackend(
        POSTGRES_DSN, dimensions=3, schema=pg_schema, min_size=1, max_size=1
    )
    await asyncio.gather(backend.initialize(), backend.initialize())
    observer = await asyncpg.connect(POSTGRES_DSN)
    open_pools = await observer.fetchval(
        "SELECT COUNT(*) FROM pg_stat_activity "
        "WHERE usename = current_user AND application_name = 'soul-framework'"
    )
    assert open_pools == 1
    await backend.close()
    assert await observer.fetchval(
        "SELECT COUNT(*) FROM pg_stat_activity "
        "WHERE usename = current_user AND application_name = 'soul-framework'"
    ) == 0

    racing = PostgresBackend(
        POSTGRES_DSN, dimensions=3, schema=pg_schema, min_size=1, max_size=1
    )
    original_initialize_once = racing._initialize_once
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def delayed_initialize_once():
        started.set()
        await proceed.wait()
        await original_initialize_once()

    monkeypatch.setattr(racing, "_initialize_once", delayed_initialize_once)
    initialize_task = asyncio.create_task(racing.initialize())
    await started.wait()
    close_task = asyncio.create_task(racing.close())
    proceed.set()
    await asyncio.gather(initialize_task, close_task)
    assert racing._pool is None

    bad_cfg = config_for(pg_schema)
    with pytest.raises(ValueError, match="OCEAN"):
        await Soul.create(
            "BadOcean",
            config=bad_cfg,
            embedding=MeaningEmbedding(),
            ocean={"O": 2.0},
        )
    assert await observer.fetchval(
        "SELECT COUNT(*) FROM pg_stat_activity "
        "WHERE usename = current_user AND application_name = 'soul-framework'"
    ) == 0
    await observer.close()


async def test_identity_rule_and_relationship_upserts_are_concurrent_safe(pg_schema):
    cfg = config_for(pg_schema)
    left = await Soul.create("Race", config=cfg, embedding=MeaningEmbedding())
    right = await Soul.create("Race", config=cfg, embedding=MeaningEmbedding())
    await asyncio.gather(
        left.identity.set_ocean({"O": 0.1, "C": 0.2, "E": 0.3, "A": 0.4, "N": 0.5}),
        right.identity.set_ocean({"O": 0.5, "C": 0.4, "E": 0.3, "A": 0.2, "N": 0.1}),
        left.rules.set("gate", "left"),
        right.rules.set("gate", "right"),
        left.identity.set_relationship("William", trust_level=1.0),
        right.identity.set_relationship("William", trust_level=0.9),
    )
    assert await left._backend.fetchval(
        "SELECT COUNT(*) FROM identity WHERE agent = $1", "Race"
    ) == 1
    assert await left._backend.fetchval(
        "SELECT COUNT(*) FROM rules WHERE agent = $1 AND rule_key = $2", "Race", "gate"
    ) == 1
    assert await left._backend.fetchval(
        "SELECT COUNT(*) FROM relationships WHERE agent = $1 AND person = $2",
        "Race", "William",
    ) == 1
    await right.close()
    await left.close()
