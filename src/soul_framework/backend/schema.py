"""Database schema — DDL statements for SOUL tables.

Portable across SQLite and PostgreSQL. Uses TEXT for timestamps and JSON,
BLOB for embeddings in SQLite (pgvector in PostgreSQL).
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    embedding BLOB,
    importance INTEGER NOT NULL DEFAULT 5,
    valence REAL DEFAULT 0.0,
    arousal REAL DEFAULT 0.0,
    dominance REAL DEFAULT 0.0,
    source TEXT DEFAULT 'conversation',
    scope TEXT DEFAULT 'private',
    confidence_score REAL DEFAULT 1.0,
    utility_score REAL DEFAULT 0.5,
    relevance_score REAL DEFAULT 1.0,
    last_activation TEXT,
    identity_defining INTEGER DEFAULT 0,
    event_time TEXT,
    episode_context TEXT,
    metadata TEXT DEFAULT '{}',
    valid_from TEXT NOT NULL,
    invalid_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(agent, category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(agent, importance DESC);

CREATE TABLE IF NOT EXISTS identity (
    agent TEXT PRIMARY KEY,
    personality TEXT DEFAULT '',
    boot_context TEXT DEFAULT '',
    philosophy TEXT DEFAULT '',
    ocean_scores TEXT DEFAULT '{}',
    ocean_baseline TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    person TEXT NOT NULL,
    trust_level REAL DEFAULT 0.5,
    style TEXT DEFAULT 'default',
    dynamic TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(agent, person)
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    turn_number INTEGER DEFAULT 0,
    thought TEXT NOT NULL,
    emotional_state TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monologue_agent ON inner_monologue(agent, created_at DESC);

CREATE TABLE IF NOT EXISTS diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    mood TEXT DEFAULT '',
    content TEXT NOT NULL,
    session_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instincts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    activation_count INTEGER DEFAULT 0,
    last_activated TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_instincts_agent ON instincts(agent);

CREATE TABLE IF NOT EXISTS working_state (
    agent TEXT PRIMARY KEY,
    state TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL,
    turn_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS procedural_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'general',
    task_description TEXT NOT NULL,
    workflow TEXT NOT NULL,
    facts TEXT DEFAULT '',
    embedding BLOB,
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
"""
