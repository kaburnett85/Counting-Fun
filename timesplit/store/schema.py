"""SQLite schema and migrations.

Design notes worth keeping in view:

* ``sessions`` is the granular record. There is no per-tick samples table by
  default -- one row per focus change is fine-grained enough to reclassify,
  where a row per 3-second tick would be 28,800 rows a day for no benefit.
  A samples table exists for field diagnosis when debug.record_samples is on.
* ``local_day`` is denormalised onto sessions so day and week queries are
  index-only and never have to do timezone arithmetic in SQL.
* ``is_locked`` marks a session the user decided personally. Bulk
  reclassification must never overwrite one.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id           INTEGER PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '#888888',
    description  TEXT NOT NULL DEFAULT '',
    is_billable  INTEGER NOT NULL DEFAULT 1,
    is_system    INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS apps (
    id            INTEGER PRIMARY KEY,
    exe_name      TEXT NOT NULL,
    exe_path      TEXT NOT NULL DEFAULT '',
    friendly_name TEXT NOT NULL DEFAULT '',
    is_browser    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (exe_name, exe_path)
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    app_id      INTEGER REFERENCES apps(id),
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT,
    domain      TEXT,
    start_ts    INTEGER NOT NULL,
    end_ts      INTEGER NOT NULL,
    duration_s  INTEGER NOT NULL,
    local_day   TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    confidence  REAL NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'none',
    rule_id     INTEGER REFERENCES rules(id),
    is_locked   INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0,
    closed      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sessions_day    ON sessions(local_day);
CREATE INDEX IF NOT EXISTS ix_sessions_start  ON sessions(start_ts);
CREATE INDEX IF NOT EXISTS ix_sessions_cat    ON sessions(category_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_sessions_review ON sessions(needs_review) WHERE needs_review = 1;
CREATE INDEX IF NOT EXISTS ix_sessions_open   ON sessions(closed) WHERE closed = 0;

CREATE TABLE IF NOT EXISTS idle_periods (
    id         INTEGER PRIMARY KEY,
    start_ts   INTEGER NOT NULL,
    end_ts     INTEGER NOT NULL,
    duration_s INTEGER NOT NULL,
    local_day  TEXT NOT NULL,
    reason     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_idle_day   ON idle_periods(local_day);
CREATE INDEX IF NOT EXISTS ix_idle_start ON idle_periods(start_ts);

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    pattern     TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    priority    INTEGER NOT NULL DEFAULT 100,
    source      TEXT NOT NULL DEFAULT 'user',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL DEFAULT 0,
    hit_count   INTEGER NOT NULL DEFAULT 0,
    last_hit_ts INTEGER,
    note        TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, pattern)
);
CREATE INDEX IF NOT EXISTS ix_rules_enabled ON rules(enabled, priority);

CREATE TABLE IF NOT EXISTS corrections (
    id               INTEGER PRIMARY KEY,
    session_id       INTEGER,
    old_category_id  INTEGER,
    new_category_id  INTEGER NOT NULL,
    features_json    TEXT NOT NULL DEFAULT '[]',
    scope            TEXT NOT NULL DEFAULT 'session',
    weight           REAL NOT NULL DEFAULT 1.0,
    applied_to_model INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_corrections_created ON corrections(created_at);

CREATE TABLE IF NOT EXISTS nb_tokens (
    category_id INTEGER NOT NULL,
    token       TEXT NOT NULL,
    count       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (category_id, token)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS nb_class (
    category_id INTEGER PRIMARY KEY,
    doc_count   REAL NOT NULL DEFAULT 0,
    token_total REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nb_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS llm_requests (
    id            INTEGER PRIMARY KEY,
    created_at    INTEGER NOT NULL,
    n_items       INTEGER NOT NULL DEFAULT 0,
    model         TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_llm_requests_created ON llm_requests(created_at);

CREATE TABLE IF NOT EXISTS llm_items (
    id             INTEGER PRIMARY KEY,
    request_id     INTEGER REFERENCES llm_requests(id),
    signature      TEXT NOT NULL,
    exe_name       TEXT NOT NULL DEFAULT '',
    domain         TEXT NOT NULL DEFAULT '',
    redacted_title TEXT NOT NULL DEFAULT '',
    category_key   TEXT NOT NULL DEFAULT '',
    confidence     REAL NOT NULL DEFAULT 0,
    accepted       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_llm_items_sig ON llm_items(signature);

CREATE TABLE IF NOT EXISTS samples (
    id       INTEGER PRIMARY KEY,
    ts       INTEGER NOT NULL,
    exe_name TEXT NOT NULL DEFAULT '',
    title    TEXT NOT NULL DEFAULT '',
    url      TEXT,
    idle_ms  INTEGER NOT NULL DEFAULT 0,
    flags    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_samples_ts ON samples(ts);
"""

#: (version, sql). Applied in order inside one transaction at startup.
MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA_V1),
]
