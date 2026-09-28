CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_type TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    ratio REAL,
    miles INTEGER,
    cpp REAL,
    confidence TEXT,
    source TEXT,
    scraped_at TEXT,
    flags TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    last_scraped_at TEXT,
    row_count INTEGER DEFAULT 0,
    status TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT,
    origin TEXT,
    destination TEXT,
    cabin TEXT,
    cash_usd REAL,
    fees_usd REAL,
    started_at TEXT,
    finished_at TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_name TEXT,
    title TEXT,
    url TEXT,
    programs TEXT,
    detected_at TEXT,
    acknowledged INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wayback_cache (
    target_url TEXT PRIMARY KEY,
    snapshot_url TEXT,
    snapshot_ts TEXT,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_alerts_programs ON alerts(programs);
