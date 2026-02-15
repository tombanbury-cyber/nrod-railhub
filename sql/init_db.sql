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

-- Insert sample layout
INSERT INTO layout (id, name, description, data) VALUES 
    ('demo', 'Demo Station', 'Simple demonstration layout with one platform', '{"version": "1.0", "type": "station"}');

-- Insert sample berths for the demo layout
-- Layout: Platform with 8 berths arranged horizontally
INSERT INTO berth (id, layout_id, name, x, y, width, height, berth_type) VALUES
    ('BRTH_1', 'demo', 'A1', 50, 100, 60, 30, 'platform'),
    ('BRTH_2', 'demo', 'A2', 120, 100, 60, 30, 'platform'),
    ('BRTH_3', 'demo', 'A3', 190, 100, 60, 30, 'platform'),
    ('BRTH_4', 'demo', 'A4', 260, 100, 60, 30, 'platform'),
    ('BRTH_5', 'demo', 'A5', 330, 100, 60, 30, 'platform'),
    ('BRTH_6', 'demo', 'A6', 400, 100, 60, 30, 'platform'),
    ('BRTH_7', 'demo', 'A7', 470, 100, 60, 30, 'platform'),
    ('BRTH_8', 'demo', 'A8', 540, 100, 60, 30, 'platform');

-- Insert sample signals
INSERT INTO signal (id, layout_id, name, x, y, signal_type) VALUES
    ('SIG_1', 'demo', 'S1', 30, 100, 'auto'),
    ('SIG_2', 'demo', 'S2', 560, 100, 'auto');

-- Insert sample train
INSERT INTO train (id, headcode, description, toc) VALUES
    ('T1', '2C90', 'Demo Train Service', 'GW');

-- Insert sample events showing a train journey
-- Uncomment these to pre-populate with a sample journey:
-- INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
--     ('2026-02-14T10:00:00Z', 'td', 'T1', 'berth_enter', 'BRTH_1', '{}'),
--     ('2026-02-14T10:01:00Z', 'td', 'T1', 'berth_exit', 'BRTH_1', '{}'),
--     ('2026-02-14T10:01:01Z', 'td', 'T1', 'berth_enter', 'BRTH_2', '{}');
