-- Migration script: Drop td_batches and rename timestamp columns
-- This script migrates an existing database to the new schema:
--   1. Removes td_batches table and batch_id from td_events
--   2. Renames step_ts -> step_timestamp, signal_ts -> signal_timestamp  
--   3. Removes step_ts_utc and signal_ts_utc columns from berth_signal_observations
--   4. Updates indexes to use new column names
--
-- ⚠️ IMPORTANT: BACKUP YOUR DATABASE BEFORE RUNNING THIS SCRIPT!
--
-- Usage: sqlite3 your_database.db < scripts/migrate_drop_td_batches_and_rename_ts.sql

BEGIN TRANSACTION;

-- ======================================================================
-- STEP 1: Migrate td_events (remove batch_id)
-- ======================================================================

-- Create new td_events table without batch_id
CREATE TABLE td_events_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_timestamp   INTEGER NOT NULL,
    received_at_utc TEXT NOT NULL,
    msg_wrapper     TEXT,
    msg_type        TEXT,
    td_area         TEXT,
    descr           TEXT,
    from_berth      TEXT,
    to_berth        TEXT,
    address         TEXT,
    data            TEXT,
    msg_json        TEXT
);

-- Copy data from old table (drop batch_id column)
INSERT INTO td_events_new (id, msg_timestamp, received_at_utc, msg_wrapper, msg_type, 
                           td_area, descr, from_berth, to_berth, address, data, msg_json)
SELECT id, msg_timestamp, received_at_utc, msg_wrapper, msg_type, 
       td_area, descr, from_berth, to_berth, address, data, msg_json
FROM td_events;

-- Drop old table and rename new table
DROP TABLE td_events;
ALTER TABLE td_events_new RENAME TO td_events;

-- Recreate indexes for td_events
CREATE INDEX idx_td_events_area ON td_events(td_area);
CREATE INDEX idx_td_events_type ON td_events(msg_type);
CREATE INDEX idx_td_events_timestamp ON td_events(msg_timestamp);
CREATE INDEX idx_td_events_received ON td_events(received_at_utc);

-- ======================================================================
-- STEP 2: Drop td_batches table
-- ======================================================================

DROP TABLE IF EXISTS td_batches;

-- ======================================================================
-- STEP 3: Migrate berth_signal_observations (rename columns)
-- ======================================================================

-- Create new berth_signal_observations table with new column names
CREATE TABLE berth_signal_observations_new (
    id INTEGER PRIMARY KEY,
    td_area TEXT NOT NULL,
    step_event_id INTEGER,
    step_timestamp INTEGER,
    from_berth TEXT,
    to_berth TEXT,
    descr TEXT,
    signal_event_id INTEGER,
    signal_timestamp INTEGER,
    address TEXT NOT NULL,
    data TEXT,
    dt_ms INTEGER NOT NULL,
    weight REAL NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
);

-- Copy data with column name mapping
-- Old: step_ts, signal_ts, step_ts_utc, signal_ts_utc
-- New: step_timestamp, signal_timestamp (drop _utc variants)
INSERT INTO berth_signal_observations_new 
    (id, td_area, step_event_id, step_timestamp, from_berth, to_berth, descr,
     signal_event_id, signal_timestamp, address, data, dt_ms, weight, created_at_utc, created_at_ts)
SELECT 
    id, td_area, step_event_id, 
    COALESCE(step_ts, 0) AS step_timestamp,  -- fallback for NULL
    from_berth, to_berth, descr,
    signal_event_id, 
    COALESCE(signal_ts, 0) AS signal_timestamp,  -- fallback for NULL
    address, data, dt_ms, weight, created_at_utc, created_at_ts
FROM berth_signal_observations;

-- Drop old table and rename new table
DROP TABLE berth_signal_observations;
ALTER TABLE berth_signal_observations_new RENAME TO berth_signal_observations;

-- Recreate indexes with new column names
CREATE INDEX idx_bso_edge 
    ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);
CREATE INDEX idx_bso_addr 
    ON berth_signal_observations(td_area, address, signal_timestamp);

-- ======================================================================
-- STEP 4: Migrate berth_signal_scores (ensure both last_seen_ts and last_seen_utc)
-- ======================================================================

-- Check if we need to migrate berth_signal_scores
-- The schema should already have last_seen_ts and last_seen_utc
-- If not, we create a new table
CREATE TABLE berth_signal_scores_new (
    td_area TEXT NOT NULL,
    from_berth TEXT NOT NULL,
    to_berth TEXT NOT NULL,
    address TEXT NOT NULL,
    score REAL NOT NULL,
    obs_count INTEGER NOT NULL,
    last_seen_ts INTEGER,
    last_seen_utc TEXT NOT NULL,
    last_data TEXT,
    PRIMARY KEY (td_area, from_berth, to_berth, address)
);

-- Copy data (handle case where old table might not have last_seen_ts)
INSERT INTO berth_signal_scores_new 
    (td_area, from_berth, to_berth, address, score, obs_count, last_seen_ts, last_seen_utc, last_data)
SELECT 
    td_area, from_berth, to_berth, address, score, obs_count,
    COALESCE(last_seen_ts, 0) AS last_seen_ts,  -- fallback if column missing
    last_seen_utc,
    last_data
FROM berth_signal_scores;

-- Drop old table and rename new table
DROP TABLE berth_signal_scores;
ALTER TABLE berth_signal_scores_new RENAME TO berth_signal_scores;

-- Recreate index
CREATE INDEX idx_bss_edge 
    ON berth_signal_scores(td_area, from_berth, to_berth, score DESC);

-- ======================================================================
-- COMMIT
-- ======================================================================

COMMIT;

-- Vacuum to reclaim space
VACUUM;

-- Print success message
SELECT 'Migration completed successfully!' AS result;
SELECT 'Old columns removed: batch_id, step_ts_utc, signal_ts_utc' AS changes;
SELECT 'New columns: step_timestamp, signal_timestamp' AS changes;
SELECT 'Dropped table: td_batches' AS changes;
