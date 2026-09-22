-- Schematic Visualization PoC Database Schema
-- Creates minimal tables for layout visualization and event tracking

PRAGMA foreign_keys = ON;

-- Layout table: stores configuration for different schematic layouts
CREATE TABLE IF NOT EXISTS layout (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    data TEXT,  -- JSON for additional layout metadata
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Berth table: defines berth positions and properties on a layout
CREATE TABLE IF NOT EXISTS berth (
    id TEXT PRIMARY KEY,
    layout_id TEXT NOT NULL,
    name TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    width INTEGER DEFAULT 60,
    height INTEGER DEFAULT 30,
    berth_type TEXT DEFAULT 'normal',  -- normal, platform, siding
    FOREIGN KEY (layout_id) REFERENCES layout(id)
);

-- Signal table: defines signal positions and properties
CREATE TABLE IF NOT EXISTS signal (
    id TEXT PRIMARY KEY,
    layout_id TEXT NOT NULL,
    name TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    signal_type TEXT DEFAULT 'auto',  -- auto, controlled, shunt
    FOREIGN KEY (layout_id) REFERENCES layout(id)
);

-- Train table: stores train information
CREATE TABLE IF NOT EXISTS train (
    id TEXT PRIMARY KEY,
    headcode TEXT,
    description TEXT,
    toc TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Event table: stores all berth and signal events
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,  -- ISO8601 timestamp
    source TEXT NOT NULL,  -- td, trust, manual, etc.
    train_id TEXT,
    event_type TEXT NOT NULL,  -- berth_enter, berth_exit, signal_on, signal_off
    object_id TEXT NOT NULL,  -- berth id or signal id
    payload TEXT,  -- JSON for additional event data
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (train_id) REFERENCES train(id)
);

CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts);
CREATE INDEX IF NOT EXISTS idx_event_train ON event(train_id);
CREATE INDEX IF NOT EXISTS idx_event_object ON event(object_id);
CREATE INDEX IF NOT EXISTS idx_event_type ON event(event_type);

-- Current S-Class state by TD area/address
CREATE TABLE IF NOT EXISTS td_sclass_state (
    td_area TEXT NOT NULL,
    address TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    last_seen_ts INTEGER NOT NULL,
    last_seen_iso TEXT NOT NULL,
    raw_data TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    PRIMARY KEY (td_area, address)
);

CREATE INDEX IF NOT EXISTS idx_td_sclass_state_area_ts
    ON td_sclass_state(td_area, last_seen_ts);

-- Derived S-Class bit transitions
CREATE TABLE IF NOT EXISTS td_sclass_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    ts_iso TEXT NOT NULL,
    td_area TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    address TEXT NOT NULL,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    bit INTEGER NOT NULL,
    old_state INTEGER NOT NULL,
    new_state INTEGER NOT NULL,
    raw_old TEXT NOT NULL,
    raw_new TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_td_sclass_changes_ts
    ON td_sclass_changes(ts_ms);
CREATE INDEX IF NOT EXISTS idx_td_sclass_changes_area_addr_ts
    ON td_sclass_changes(td_area, address, ts_ms);

-- Historical berth transition evidence derived from observed train chains
CREATE TABLE IF NOT EXISTS berth_transition_counts (
    headcode TEXT NOT NULL,
    from_berth TEXT NOT NULL,
    to_berth TEXT NOT NULL,
    transition_count INTEGER NOT NULL DEFAULT 0,
    first_seen_ts TEXT NOT NULL,
    last_seen_ts TEXT NOT NULL,
    PRIMARY KEY (headcode, from_berth, to_berth)
);

CREATE INDEX IF NOT EXISTS idx_berth_transition_headcode_from
    ON berth_transition_counts(headcode, from_berth);
CREATE INDEX IF NOT EXISTS idx_berth_transition_headcode_last_seen
    ON berth_transition_counts(headcode, last_seen_ts);

-- Historical headcode route patterns for gap-filling and auditability
CREATE TABLE IF NOT EXISTS headcode_route_patterns (
    headcode TEXT NOT NULL,
    route_key TEXT NOT NULL,
    berth_sequence_json TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (headcode, route_key)
);

CREATE INDEX IF NOT EXISTS idx_headcode_route_patterns_headcode
    ON headcode_route_patterns(headcode, observations DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS route_inference_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    algorithm_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

-- Insert sample layout
INSERT OR IGNORE INTO layout (id, name, description, data) VALUES 
    ('demo', 'Demo Station', 'Simple demonstration layout with one platform', '{"version": "1.0", "type": "station"}');

-- Insert sample berths for the demo layout
-- Layout: Platform with 8 berths arranged horizontally
INSERT OR IGNORE INTO berth (id, layout_id, name, x, y, width, height, berth_type) VALUES
    ('BRTH_1', 'demo', 'A1', 50, 100, 60, 30, 'platform'),
    ('BRTH_2', 'demo', 'A2', 120, 100, 60, 30, 'platform'),
    ('BRTH_3', 'demo', 'A3', 190, 100, 60, 30, 'platform'),
    ('BRTH_4', 'demo', 'A4', 260, 100, 60, 30, 'platform'),
    ('BRTH_5', 'demo', 'A5', 330, 100, 60, 30, 'platform'),
    ('BRTH_6', 'demo', 'A6', 400, 100, 60, 30, 'platform'),
    ('BRTH_7', 'demo', 'A7', 470, 100, 60, 30, 'platform'),
    ('BRTH_8', 'demo', 'A8', 540, 100, 60, 30, 'platform');

-- Insert sample signals
INSERT OR IGNORE INTO signal (id, layout_id, name, x, y, signal_type) VALUES
    ('SIG_1', 'demo', 'S1', 30, 100, 'auto'),
    ('SIG_2', 'demo', 'S2', 560, 100, 'auto');

-- Insert sample train
INSERT OR IGNORE INTO train (id, headcode, description, toc) VALUES
    ('T1', '2C90', 'Demo Train Service', 'GW');

-- Insert sample events showing a train journey
-- Uncomment these to pre-populate with a sample journey:
-- INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
--     ('2026-02-14T10:00:00Z', 'td', 'T1', 'berth_enter', 'BRTH_1', '{}'),
--     ('2026-02-14T10:01:00Z', 'td', 'T1', 'berth_exit', 'BRTH_1', '{}'),
--     ('2026-02-14T10:01:01Z', 'td', 'T1', 'berth_enter', 'BRTH_2', '{}');
