-- Migration: Add toc_td_areas table for TOC ↔ TD area mappings
-- This table stores many-to-many relationships between Train Operating Companies (TOCs)
-- and Train Describer (TD) areas. These mappings help constrain candidate schedules
-- when matching berth events to trains.

-- Create the toc_td_areas table if it doesn't exist
CREATE TABLE IF NOT EXISTS toc_td_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    toc_code TEXT NOT NULL,
    td_area TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    confidence REAL,
    effective_from TEXT,
    effective_to TEXT,
    created_by TEXT,
    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
    notes TEXT,
    UNIQUE(toc_code, td_area)
);

-- Create indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_toc_td_areas_toc_code ON toc_td_areas(toc_code);
CREATE INDEX IF NOT EXISTS idx_toc_td_areas_td_area ON toc_td_areas(td_area);
