"""PostgreSQL schema for SOUL Framework.

The public API intentionally keeps timestamps and JSON payloads encoded as TEXT,
matching the SQLite backend.  This makes backend switching lossless while the
native pgvector column provides indexed semantic retrieval.
"""

from __future__ import annotations


REQUIRED_TABLES = frozenset({
    "memories",
    "identity",
    "relationships",
    "rules",
    "inner_monologue",
    "diary",
    "instincts",
    "working_state",
    "procedural_memories",
    "schema_migrations",
})

REQUIRED_COLUMNS = {
    "schema_migrations": {"version", "checksum", "embedding_dimensions", "applied_at"},
    "memories": {
        "id", "agent", "category", "content", "embedding", "embedding_vector",
        "importance", "valence", "arousal", "dominance", "source", "scope",
        "confidence_score", "utility_score", "relevance_score", "last_activation",
        "identity_defining", "event_time", "episode_context", "metadata",
        "valid_from", "invalid_at", "created_at",
    },
    "identity": {
        "agent", "personality", "boot_context", "philosophy", "ocean_scores",
        "ocean_baseline", "updated_at",
    },
    "relationships": {
        "id", "agent", "person", "trust_level", "style", "dynamic", "updated_at",
    },
    "rules": {"id", "agent", "rule_key", "content", "set_by", "priority", "active", "created_at"},
    "inner_monologue": {
        "id", "agent", "session_id", "turn_number", "thought", "emotional_state", "created_at",
    },
    "diary": {"id", "agent", "mood", "content", "session_date", "created_at"},
    "instincts": {
        "id", "agent", "trigger_pattern", "action", "confidence",
        "activation_count", "last_activated", "created_at",
    },
    "working_state": {"agent", "state", "updated_at", "turn_count"},
    "procedural_memories": {
        "id", "agent", "task_type", "task_description", "workflow", "facts",
        "embedding", "embedding_vector", "hit_count", "success_count", "fail_count",
        "source_task", "build_policy", "reflection", "created_at", "updated_at",
    },
}

POSTGRES_COLUMN_TYPES = {
    table: {column: "text" for column in columns}
    for table, columns in REQUIRED_COLUMNS.items()
}
for table, columns in {
    "schema_migrations": {"version", "embedding_dimensions"},
    "memories": {"importance", "identity_defining"},
    "rules": {"active"},
    "inner_monologue": {"turn_number"},
    "instincts": {"activation_count"},
    "working_state": {"turn_count"},
    "procedural_memories": {"hit_count", "success_count", "fail_count"},
}.items():
    for column in columns:
        POSTGRES_COLUMN_TYPES[table][column] = "integer"
for table in (
    "memories", "relationships", "rules", "inner_monologue", "diary",
    "instincts", "procedural_memories",
):
    POSTGRES_COLUMN_TYPES[table]["id"] = "bigint"
for table, columns in {
    "memories": {
        "valence", "arousal", "dominance", "confidence_score", "utility_score",
        "relevance_score",
    },
    "relationships": {"trust_level"},
    "instincts": {"confidence"},
}.items():
    for column in columns:
        POSTGRES_COLUMN_TYPES[table][column] = "double precision"
for table in ("memories", "procedural_memories"):
    POSTGRES_COLUMN_TYPES[table]["embedding"] = "bytea"
    POSTGRES_COLUMN_TYPES[table]["embedding_vector"] = "vector"

POSTGRES_NOT_NULL = {
    "schema_migrations": {"version", "checksum", "embedding_dimensions", "applied_at"},
    "memories": {"id", "agent", "category", "content", "importance", "valid_from", "created_at"},
    "identity": {"agent", "updated_at"},
    "relationships": {"id", "agent", "person", "updated_at"},
    "rules": {"id", "agent", "rule_key", "content", "created_at"},
    "inner_monologue": {"id", "agent", "thought", "created_at"},
    "diary": {"id", "agent", "content", "session_date", "created_at"},
    "instincts": {"id", "agent", "trigger_pattern", "action", "created_at"},
    "working_state": {"agent", "updated_at"},
    "procedural_memories": {
        "id", "agent", "task_type", "task_description", "workflow", "created_at", "updated_at",
    },
}

POSTGRES_IDENTITY_COLUMNS = {
    (table, "id")
    for table in (
        "memories", "relationships", "rules", "inner_monologue", "diary",
        "instincts", "procedural_memories",
    )
}

# Operations use ``ON CONFLICT`` on these keys, so column/type parity alone is
# insufficient: losing any of these constraints turns a certified schema into
# one that fails on its first upsert.
POSTGRES_REQUIRED_CONSTRAINTS = {
    "schema_migrations": {("p", ("version",))},
    "memories": {("p", ("id",))},
    "identity": {("p", ("agent",))},
    "relationships": {
        ("p", ("id",)),
        ("u", ("agent", "person")),
    },
    "rules": {
        ("p", ("id",)),
        ("u", ("agent", "rule_key")),
    },
    "inner_monologue": {("p", ("id",))},
    "diary": {("p", ("id",))},
    "instincts": {("p", ("id",))},
    "working_state": {("p", ("agent",))},
    "procedural_memories": {("p", ("id",))},
}

POSTGRES_COLUMN_DEFAULTS = {
    "memories": {
        "category": "'fact'::text",
        "importance": "5",
        "valence": "0.0",
        "arousal": "0.0",
        "dominance": "0.0",
        "source": "'conversation'::text",
        "scope": "'private'::text",
        "confidence_score": "1.0",
        "utility_score": "0.5",
        "relevance_score": "1.0",
        "identity_defining": "0",
        "metadata": "'{}'::text",
    },
    "identity": {
        "personality": "''::text",
        "boot_context": "''::text",
        "philosophy": "''::text",
        "ocean_scores": "'{}'::text",
        "ocean_baseline": "'{}'::text",
    },
    "relationships": {
        "trust_level": "0.5",
        "style": "'default'::text",
        "dynamic": "''::text",
    },
    "rules": {
        "set_by": "'system'::text",
        "priority": "'normal'::text",
        "active": "1",
    },
    "inner_monologue": {
        "session_id": "''::text",
        "turn_number": "0",
        "emotional_state": "''::text",
    },
    "diary": {"mood": "''::text"},
    "instincts": {"confidence": "0.5", "activation_count": "0"},
    "working_state": {"state": "'{}'::text", "turn_count": "0"},
    "procedural_memories": {
        "task_type": "'general'::text",
        "facts": "''::text",
        "hit_count": "0",
        "success_count": "0",
        "fail_count": "0",
        "source_task": "''::text",
        "build_policy": "'direct'::text",
        "reflection": "''::text",
    },
}


def postgres_schema_sql(dimensions: int) -> str:
    """Return idempotent PostgreSQL DDL for one fixed embedding dimension."""
    if dimensions <= 0 or dimensions > 2000:
        raise ValueError("PostgreSQL vector dimensions must be between 1 and 2000")

    return f"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    embedding BYTEA,
    embedding_vector vector({dimensions}),
    importance INTEGER NOT NULL DEFAULT 5,
    valence DOUBLE PRECISION DEFAULT 0.0,
    arousal DOUBLE PRECISION DEFAULT 0.0,
    dominance DOUBLE PRECISION DEFAULT 0.0,
    source TEXT DEFAULT 'conversation',
    scope TEXT DEFAULT 'private',
    confidence_score DOUBLE PRECISION DEFAULT 1.0,
    utility_score DOUBLE PRECISION DEFAULT 0.5,
    relevance_score DOUBLE PRECISION DEFAULT 1.0,
    last_activation TEXT,
    identity_defining INTEGER DEFAULT 0,
    event_time TEXT,
    episode_context TEXT,
    metadata TEXT DEFAULT '{{}}',
    valid_from TEXT NOT NULL,
    invalid_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(agent, category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(agent, importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
    ON memories USING hnsw (embedding_vector vector_cosine_ops);

CREATE TABLE IF NOT EXISTS identity (
    agent TEXT PRIMARY KEY,
    personality TEXT DEFAULT '',
    boot_context TEXT DEFAULT '',
    philosophy TEXT DEFAULT '',
    ocean_scores TEXT DEFAULT '{{}}',
    ocean_baseline TEXT DEFAULT '{{}}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    person TEXT NOT NULL,
    trust_level DOUBLE PRECISION DEFAULT 0.5,
    style TEXT DEFAULT 'default',
    dynamic TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(agent, person)
);

CREATE TABLE IF NOT EXISTS rules (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    content TEXT NOT NULL,
    set_by TEXT DEFAULT 'system',
    priority TEXT DEFAULT 'normal',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(agent, rule_key)
);

CREATE TABLE IF NOT EXISTS inner_monologue (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    turn_number INTEGER DEFAULT 0,
    thought TEXT NOT NULL,
    emotional_state TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monologue_agent ON inner_monologue(agent, created_at DESC);

CREATE TABLE IF NOT EXISTS diary (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    mood TEXT DEFAULT '',
    content TEXT NOT NULL,
    session_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instincts (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence DOUBLE PRECISION DEFAULT 0.5,
    activation_count INTEGER DEFAULT 0,
    last_activated TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_instincts_agent ON instincts(agent);

CREATE TABLE IF NOT EXISTS working_state (
    agent TEXT PRIMARY KEY,
    state TEXT DEFAULT '{{}}',
    updated_at TEXT NOT NULL,
    turn_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS procedural_memories (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    agent TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    task_description TEXT NOT NULL,
    workflow TEXT NOT NULL,
    facts TEXT DEFAULT '',
    embedding BYTEA,
    embedding_vector vector({dimensions}),
    hit_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    source_task TEXT DEFAULT '',
    build_policy TEXT DEFAULT 'direct',
    reflection TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_procedures_agent ON procedural_memories(agent);
CREATE INDEX IF NOT EXISTS idx_procedures_type ON procedural_memories(agent, task_type);
CREATE INDEX IF NOT EXISTS idx_procedures_embedding_hnsw
    ON procedural_memories USING hnsw (embedding_vector vector_cosine_ops);
"""
