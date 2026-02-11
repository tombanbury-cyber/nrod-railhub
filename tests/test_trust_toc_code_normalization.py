#!/usr/bin/env python3
"""Test TRUST messages TOC normalization on insert and backfill."""

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nrod_railhub.database import RailDB
from nrod_railhub.resolvers import TOCResolver


def test_trust_message_insert_with_business_code():
    """Test that insert_trust_message resolves business code to canonical toc_code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a TRUST message with business code '84' (Southeastern)
        trust_body = {
            'train_id': '123456',
            'toc_id': '84',  # Business code for Southeastern
            'actual_timestamp': '1640000000000',
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        }
        
        db.insert_trust_message(trust_body)
        
        # Query the database to verify normalization
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT toc_id, toc_code 
            FROM trust_messages 
            WHERE train_id='123456'
        """)
        row = cursor.fetchone()
        
        assert row is not None, "Message should be inserted"
        assert row['toc_id'] == '84', "Raw toc_id should be preserved as '84'"
        assert row['toc_code'] == 'SE', "Canonical toc_code should be 'SE'"
        
        conn.close()


def test_trust_message_insert_with_atoc_code():
    """Test that insert_trust_message resolves ATOC code to canonical toc_code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a TRUST message with ATOC code 'SWR' (South Western Railway)
        trust_body = {
            'train_id': '789012',
            'toc_id': 'SWR',  # ATOC code for South Western Railway
            'actual_timestamp': '1640000001000',
            'event_type': 'DEPARTURE',
            'reporting_stanox': '87701'
        }
        
        db.insert_trust_message(trust_body)
        
        # Query the database to verify normalization
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT toc_id, toc_code 
            FROM trust_messages 
            WHERE train_id='789012'
        """)
        row = cursor.fetchone()
        
        assert row is not None, "Message should be inserted"
        assert row['toc_id'] == 'SWR', "Raw toc_id should be preserved as 'SWR'"
        assert row['toc_code'] == 'SW', "Canonical toc_code should be 'SW'"
        
        conn.close()


def test_trust_message_insert_with_canonical_code():
    """Test that insert_trust_message handles canonical codes correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a TRUST message with canonical code 'GW'
        trust_body = {
            'train_id': '345678',
            'toc_id': 'GW',  # Already canonical
            'actual_timestamp': '1640000002000',
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        }
        
        db.insert_trust_message(trust_body)
        
        # Query the database to verify normalization
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT toc_id, toc_code 
            FROM trust_messages 
            WHERE train_id='345678'
        """)
        row = cursor.fetchone()
        
        assert row is not None, "Message should be inserted"
        assert row['toc_id'] == 'GW', "Raw toc_id should be preserved as 'GW'"
        assert row['toc_code'] == 'GW', "Canonical toc_code should be 'GW'"
        
        conn.close()


def test_trust_message_insert_with_unknown_code():
    """Test that insert_trust_message handles unknown codes gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a TRUST message with unknown code
        trust_body = {
            'train_id': '999888',
            'toc_id': 'ZZZ',  # Unknown code
            'actual_timestamp': '1640000003000',
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        }
        
        db.insert_trust_message(trust_body)
        
        # Query the database to verify handling
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT toc_id, toc_code 
            FROM trust_messages 
            WHERE train_id='999888'
        """)
        row = cursor.fetchone()
        
        assert row is not None, "Message should be inserted"
        assert row['toc_id'] == 'ZZZ', "Raw toc_id should be preserved as 'ZZZ'"
        assert row['toc_code'] is None, "Canonical toc_code should be NULL for unknown codes"
        
        conn.close()


def test_trust_messages_backfill_migration():
    """Test that backfill migration correctly populates toc_code for existing rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        
        # Step 1: Create database with old schema (no toc_code column)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS toc_reference (
                toc_code TEXT PRIMARY KEY,
                toc_name TEXT NOT NULL,
                business_code TEXT,
                atoc_code TEXT,
                sector TEXT,
                updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trust_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                train_id TEXT,
                toc_id TEXT,
                actual_timestamp_ms INTEGER,
                event_type TEXT,
                reporting_stanox TEXT,
                created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                UNIQUE(train_id, actual_timestamp_ms)
            )
        """)
        
        # Step 2: Insert TOC reference data
        conn.execute("""
            INSERT INTO toc_reference (toc_code, toc_name, business_code, atoc_code, sector)
            VALUES ('SE', 'Southeastern', '84', 'SET', 'Regional')
        """)
        conn.execute("""
            INSERT INTO toc_reference (toc_code, toc_name, business_code, atoc_code, sector)
            VALUES ('SW', 'South Western Railway', '71', 'SWR', 'Regional')
        """)
        
        # Step 3: Insert old-format TRUST messages (no toc_code column)
        conn.execute("""
            INSERT INTO trust_messages (train_id, toc_id, actual_timestamp_ms, event_type, reporting_stanox)
            VALUES ('111111', '84', 1640000000000, 'ARRIVAL', '87701')
        """)
        conn.execute("""
            INSERT INTO trust_messages (train_id, toc_id, actual_timestamp_ms, event_type, reporting_stanox)
            VALUES ('222222', 'SWR', 1640000001000, 'DEPARTURE', '87701')
        """)
        conn.execute("""
            INSERT INTO trust_messages (train_id, toc_id, actual_timestamp_ms, event_type, reporting_stanox)
            VALUES ('333333', 'ZZZ', 1640000002000, 'ARRIVAL', '87701')
        """)
        
        conn.commit()
        
        # Step 4: Run migration script
        migration_path = Path(__file__).parent.parent / "scripts" / "db_migrations" / "002_add_trust_messages_toc_code.sql"
        with open(migration_path, 'r') as f:
            migration_sql = f.read()
        
        # Execute migration (split by semicolon to handle multiple statements)
        for statement in migration_sql.split(';'):
            if statement.strip() and not statement.strip().startswith('--'):
                conn.execute(statement)
        
        conn.commit()
        
        # Step 5: Verify backfill results
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check business code mapping
        cursor.execute("SELECT toc_id, toc_code FROM trust_messages WHERE train_id='111111'")
        row1 = cursor.fetchone()
        assert row1['toc_id'] == '84', "Raw toc_id should be '84'"
        assert row1['toc_code'] == 'SE', "Should be backfilled to 'SE'"
        
        # Check ATOC code mapping
        cursor.execute("SELECT toc_id, toc_code FROM trust_messages WHERE train_id='222222'")
        row2 = cursor.fetchone()
        assert row2['toc_id'] == 'SWR', "Raw toc_id should be 'SWR'"
        assert row2['toc_code'] == 'SW', "Should be backfilled to 'SW'"
        
        # Check unknown code handling
        cursor.execute("SELECT toc_id, toc_code FROM trust_messages WHERE train_id='333333'")
        row3 = cursor.fetchone()
        assert row3['toc_id'] == 'ZZZ', "Raw toc_id should be 'ZZZ'"
        assert row3['toc_code'] is None, "Should remain NULL for unknown codes"
        
        conn.close()


def test_trust_messages_join_and_filter():
    """Test that web UI query joins correctly on canonical toc_code."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert messages with various TOC identifiers
        db.insert_trust_message({
            'train_id': 'A11111',
            'toc_id': '84',  # Business code -> SE
            'actual_timestamp': '1640000000000',
            'event_type': 'ARRIVAL'
        })
        
        db.insert_trust_message({
            'train_id': 'B22222',
            'toc_id': 'SWR',  # ATOC code -> SW
            'actual_timestamp': '1640000001000',
            'event_type': 'DEPARTURE'
        })
        
        db.insert_trust_message({
            'train_id': 'C33333',
            'toc_id': 'GW',  # Canonical -> GW
            'actual_timestamp': '1640000002000',
            'event_type': 'ARRIVAL'
        })
        
        # Simulate web UI query with TOC filter for 'SE'
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # This is the updated query from web.py
        cursor.execute("""
            SELECT tm.id, tm.train_id, tm.toc_id AS msg_toc_id, 
                   tm.toc_code AS canonical_toc_code, tr.toc_name
            FROM trust_messages tm
            LEFT JOIN toc_reference tr ON tm.toc_code = tr.toc_code
            WHERE tm.toc_code = ?
            ORDER BY tm.actual_timestamp_ms DESC
        """, ('SE',))
        
        rows = cursor.fetchall()
        
        # Should find the message inserted with business code '84'
        assert len(rows) == 1, "Should find exactly one SE message"
        assert rows[0]['train_id'] == 'A11111'
        assert rows[0]['msg_toc_id'] == '84'
        assert rows[0]['canonical_toc_code'] == 'SE'
        assert rows[0]['toc_name'] == 'Southeastern'
        
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
