-- ============================================================================
-- Migration Script: Drop td_batches and Rename Mapper Timestamp Columns
-- ============================================================================
--
-- This migration script updates the database schema to:
-- 1. Remove the td_batches table (events are now stored directly in td_events)
-- 2. Remove the batch_id foreign key from td_events
-- 3. Rename mapper timestamp columns to use unix ms (INTEGER):
--    - berth_signal_observations: step_ts -> step_timestamp, signal_ts -> signal_timestamp
--    - Remove step_ts_utc and signal_ts_utc columns
-- 4. Keep berth_signal_scores with last_seen_ts (INTEGER) and last_seen_utc (TEXT)
--
-- IMPORTANT: BACKUP YOUR DATABASE BEFORE RUNNING THIS SCRIPT!
-- 
-- Usage:
--   sqlite3 your_database.db < migrate_drop_td_batches_and_rename_ts.sql
--
-- Testing:
--   1. Make a backup copy of your database: cp rail.db rail.db.backup
--   2. Run the migration on a test copy: cp rail.db rail_test.db && sqlite3 rail_test.db < migrate_drop_td_batches_and_rename_ts.sql
--   3. Verify counts and spot-check data
--   4. If satisfied, run on production database
--
-- Rollback:
--   Keep your backup! If issues arise, restore from rail.db.backup
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- Step 1: Migrate td_events (remove batch_id)
-- ============================================================================

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

-- Copy data from old table to new table
-- Note: We drop batch_id; received_at_utc is preserved from the old td_events table
INSERT INTO td_events_new (
    id, msg_timestamp, received_at_utc, msg_wrapper, msg_type, td_area, descr,
    from_berth, to_berth, address, data, msg_json
)
SELECT
    id, msg_timestamp, received_at_utc, msg_wrapper, msg_type, td_area, descr,
    from_berth, to_berth, address, data, msg_json
FROM td_events;

-- Drop old table and rename new table
DROP TABLE td_events;
ALTER TABLE td_events_new RENAME TO td_events;

-- Recreate indexes on td_events
CREATE INDEX idx_td_events_area ON td_events(td_area);
CREATE INDEX idx_td_events_type ON td_events(msg_type);
CREATE INDEX idx_td_events_timestamp ON td_events(msg_timestamp);

-- ============================================================================
-- Step 2: Drop td_batches table
-- ============================================================================

DROP TABLE IF EXISTS td_batches;

-- ============================================================================
-- Step 3: Migrate berth_signal_observations (rename timestamp columns)
-- ============================================================================

-- Create new observations table with renamed columns
CREATE TABLE berth_signal_observations_new (
    id INTEGER PRIMARY KEY,
    td_area TEXT NOT NULL,
    step_event_id INTEGER,
    step_timestamp INTEGER NOT NULL,
    from_berth TEXT,
    to_berth TEXT,
    descr TEXT,
    signal_event_id INTEGER,
    signal_timestamp INTEGER NOT NULL,
    address TEXT NOT NULL,
    data TEXT,
    dt_ms INTEGER NOT NULL,
    weight REAL NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
);

-- Copy data with column name mapping
-- Migration policy:
--   - Use existing step_ts as step_timestamp (already unix ms)
--   - Use existing signal_ts as signal_timestamp (already unix ms)
--   - If step_ts is NULL or 0, try parsing step_ts_utc to unix ms, else use 0
--   - If signal_ts is NULL or 0, use 0 (could enhance by matching td_events.msg_timestamp)
INSERT INTO berth_signal_observations_new (
    id, td_area, step_event_id, step_timestamp, from_berth, to_berth, descr,
    signal_event_id, signal_timestamp, address, data, dt_ms, weight, created_at_utc, created_at_ts
)
SELECT
    id,
    td_area,
    step_event_id,
    COALESCE(step_ts, 
             CASE 
                 WHEN step_ts_utc IS NOT NULL 
                 THEN CAST((julianday(step_ts_utc) - 2440587.5) * 86400000 AS INTEGER)
                 ELSE 0
             END
    ) AS step_timestamp,
    from_berth,
    to_berth,
    descr,
    signal_event_id,
    COALESCE(signal_ts, 
             CASE 
                 WHEN signal_ts_utc IS NOT NULL 
                 THEN CAST((julianday(signal_ts_utc) - 2440587.5) * 86400000 AS INTEGER)
                 ELSE 0
             END
    ) AS signal_timestamp,
    address,
    data,
    dt_ms,
    weight,
    created_at_utc,
    created_at_ts
FROM berth_signal_observations;

-- Drop old table and rename new table
DROP TABLE berth_signal_observations;
ALTER TABLE berth_signal_observations_new RENAME TO berth_signal_observations;

-- Recreate indexes on berth_signal_observations
CREATE INDEX idx_bso_edge ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);
CREATE INDEX idx_bso_addr ON berth_signal_observations(td_area, address, signal_timestamp);

-- ============================================================================
-- Step 4: berth_signal_scores (already has correct schema)
-- ============================================================================
-- The berth_signal_scores table already has the correct schema:
-- - last_seen_ts (INTEGER)
-- - last_seen_utc (TEXT)
-- No migration needed for this table.

-- ============================================================================
-- Verification Queries (optional - uncomment to check results)
-- ============================================================================

-- SELECT 'td_events count:' AS check, COUNT(*) AS count FROM td_events;
-- SELECT 'berth_signal_observations count:' AS check, COUNT(*) AS count FROM berth_signal_observations;
-- SELECT 'berth_signal_scores count:' AS check, COUNT(*) AS count FROM berth_signal_scores;

-- Sample data checks:
-- SELECT 'td_events sample:' AS check, id, msg_timestamp, received_at_utc, td_area, msg_type FROM td_events ORDER BY id DESC LIMIT 3;
-- SELECT 'observations sample:' AS check, id, step_timestamp, signal_timestamp, td_area, from_berth, to_berth FROM berth_signal_observations ORDER BY id DESC LIMIT 3;

COMMIT;

-- ============================================================================
-- Migration Complete
-- ============================================================================
-- Your database has been migrated to the new schema.
-- 
-- Changes applied:
-- - td_batches table dropped
-- - td_events: batch_id column removed
-- - berth_signal_observations: step_ts -> step_timestamp, signal_ts -> signal_timestamp
-- - berth_signal_observations: step_ts_utc and signal_ts_utc columns removed
-- - Indexes recreated with new column names
-- 
-- Next steps:
-- 1. Verify data integrity with sample queries
-- 2. Run the updated td_feed_dashboard-v4b.py with the new schema
-- 3. Monitor for any issues and keep your backup until confident
-- ============================================================================
