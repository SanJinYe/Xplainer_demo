"""SQLite schema and migration helpers."""

import aiosqlite

EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT,
    agent_step_id TEXT,
    action_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_range_start INTEGER,
    line_range_end INTEGER,
    code_snapshot TEXT NOT NULL,
    intent TEXT NOT NULL,
    reasoning TEXT,
    decision_alternatives TEXT,
    entity_refs TEXT,
    external_refs TEXT
);
"""

EVENTS_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_path);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
"""

ENTITIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line_range_start INTEGER,
    line_range_end INTEGER,
    signature TEXT,
    params TEXT,
    return_type TEXT,
    docstring TEXT,
    created_at TEXT NOT NULL,
    created_by_event TEXT,
    last_modified_event TEXT,
    last_modified_at TEXT,
    modification_count INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0,
    deleted_by_event TEXT,
    event_refs TEXT,
    rename_history TEXT,
    is_external INTEGER DEFAULT 0,
    package TEXT,
    cached_description TEXT,
    description_valid INTEGER DEFAULT 0,
    in_degree INTEGER DEFAULT 0,
    out_degree INTEGER DEFAULT 0,
    tags TEXT
);
"""

ENTITIES_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_entities_qname ON entities(qualified_name);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file_path);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_active ON entities(is_deleted);
"""

ENTITY_SEARCH_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS entity_search USING fts5(
    name,
    qualified_name,
    cached_description
);
"""

RELATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    from_event TEXT,
    context TEXT,
    created_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (source) REFERENCES entities(entity_id),
    FOREIGN KEY (target) REFERENCES entities(entity_id)
);
"""

RELATIONS_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
CREATE INDEX IF NOT EXISTS idx_relations_event ON relations(from_event);
CREATE INDEX IF NOT EXISTS idx_relations_active ON relations(is_active);
"""

EXPLANATION_CACHE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS explanation_cache (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_valid INTEGER DEFAULT 1
);
"""

TASK_STEP_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS task_step_events (
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT,
    intent TEXT NOT NULL,
    reasoning_summary TEXT,
    tool_name TEXT,
    input_summary TEXT,
    output_summary TEXT,
    timestamp TEXT NOT NULL
);
"""

TASK_STEP_EVENTS_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_task_step_events_task ON task_step_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_step_events_timestamp ON task_step_events(timestamp);
"""

SCHEMA_SQL = "\n".join(
    [
        EVENTS_TABLE_SQL,
        EVENTS_INDEXES_SQL,
        ENTITIES_TABLE_SQL,
        ENTITIES_INDEXES_SQL,
        ENTITY_SEARCH_SQL,
        RELATIONS_TABLE_SQL,
        RELATIONS_INDEXES_SQL,
        EXPLANATION_CACHE_TABLE_SQL,
        TASK_STEP_EVENTS_TABLE_SQL,
        TASK_STEP_EVENTS_INDEXES_SQL,
    ]
)


async def run_migrations(connection: aiosqlite.Connection) -> None:
    """Create all required SQLite tables and indexes."""

    await connection.executescript(SCHEMA_SQL)
    await connection.commit()


__all__ = [
    "ENTITY_SEARCH_SQL",
    "ENTITIES_INDEXES_SQL",
    "ENTITIES_TABLE_SQL",
    "EVENTS_INDEXES_SQL",
    "EVENTS_TABLE_SQL",
    "EXPLANATION_CACHE_TABLE_SQL",
    "TASK_STEP_EVENTS_INDEXES_SQL",
    "TASK_STEP_EVENTS_TABLE_SQL",
    "RELATIONS_INDEXES_SQL",
    "RELATIONS_TABLE_SQL",
    "SCHEMA_SQL",
    "run_migrations",
]
