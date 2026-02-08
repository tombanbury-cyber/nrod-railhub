-- Migration: Add created_at_ts column to vstp_schedules (if not exists)
-- Note: This migration is included for documentation purposes.
-- The created_at_ts column already exists in the schema as of the initial implementation.

-- Check if column exists and add if missing (SQLite 3.35.0+)
-- For older SQLite versions, this would need to be handled in application code

-- Add created_at_ts to vstp_schedules if it doesn't exist
-- (In practice, this is already in the schema with DEFAULT (strftime('%s','now') * 1000))
-- ALTER TABLE vstp_schedules ADD COLUMN created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000);

-- Backfill any existing rows that might have NULL (should not happen with new schema)
-- UPDATE vstp_schedules 
-- SET created_at_ts = (strftime('%s','now') * 1000) 
-- WHERE created_at_ts IS NULL;

-- Add index for efficient purge queries
CREATE INDEX IF NOT EXISTS idx_vstp_schedules_created_at_ts ON vstp_schedules(created_at_ts);

-- Add similar index for trust_messages (already has the column)
CREATE INDEX IF NOT EXISTS idx_trust_messages_created_at_ts ON trust_messages(created_at_ts);
