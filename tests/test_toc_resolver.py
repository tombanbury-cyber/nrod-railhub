#!/usr/bin/env python3
"""Test TOC (Train Operating Company) resolver and database functionality."""

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nrod_railhub.resolvers import TOCResolver
from nrod_railhub.database import RailDB


def test_toc_resolver_initialization():
    """Test that TOC resolver initializes with static data."""
    resolver = TOCResolver()
    
    # Should have loaded TOC data
    assert len(resolver.toc_map) > 0, "TOC map should not be empty"
    assert len(resolver.TOC_DATA) > 0, "TOC_DATA should not be empty"
    
    # Check some common TOC codes
    assert 'SW' in resolver.toc_map, "Should have South Western Railway"
    assert 'GW' in resolver.toc_map, "Should have Great Western Railway"
    assert 'XC' in resolver.toc_map, "Should have CrossCountry"


def test_toc_resolver_get_name():
    """Test getting TOC names by code."""
    resolver = TOCResolver()
    
    # Test valid codes
    sw_name = resolver.get_toc_name('SW')
    assert sw_name is not None, "SW should have a name"
    assert 'South Western' in sw_name, f"Expected 'South Western' in name, got {sw_name}"
    
    gw_name = resolver.get_toc_name('GW')
    assert gw_name is not None, "GW should have a name"
    assert 'Great Western' in gw_name, f"Expected 'Great Western' in name, got {gw_name}"
    
    # Test case insensitivity
    assert resolver.get_toc_name('sw') == sw_name, "Should be case insensitive"
    assert resolver.get_toc_name('Sw') == sw_name, "Should be case insensitive"
    
    # Test invalid code
    assert resolver.get_toc_name('ZZZ') is None, "Invalid code should return None"
    assert resolver.get_toc_name('') is None, "Empty code should return None"
    assert resolver.get_toc_name(None) is None, "None should return None"


def test_toc_resolver_get_all():
    """Test getting all TOC data."""
    resolver = TOCResolver()
    
    all_tocs = resolver.get_all_tocs()
    
    assert isinstance(all_tocs, list), "Should return a list"
    assert len(all_tocs) > 0, "Should have TOC entries"
    
    # Check first entry structure
    first = all_tocs[0]
    assert 'toc_code' in first, "Should have toc_code"
    assert 'toc_name' in first, "Should have toc_name"
    assert 'sector' in first, "Should have sector"
    
    # Verify entries are sorted by code
    codes = [t['toc_code'] for t in all_tocs]
    assert codes == sorted(codes), "TOCs should be sorted by code"


def test_database_toc_schema():
    """Test that TOC reference table is created in database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Check that toc_reference table exists
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='toc_reference'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None, "toc_reference table should exist"


def test_database_upsert_toc():
    """Test inserting and updating TOC data in database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert a TOC
        db.upsert_toc('SW', 'South Western Railway', sector='Passenger')
        
        # Retrieve it
        name = db.get_toc_name('SW')
        assert name == 'South Western Railway', f"Expected 'South Western Railway', got {name}"
        
        # Update it
        db.upsert_toc('SW', 'South Western Railway Updated', sector='Passenger')
        
        # Verify update
        name = db.get_toc_name('SW')
        assert name == 'South Western Railway Updated', f"Expected updated name, got {name}"
        
        # Test non-existent TOC
        assert db.get_toc_name('ZZZ') is None, "Non-existent TOC should return None"


def test_database_get_all_tocs():
    """Test retrieving all TOC data from database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert multiple TOCs
        db.upsert_toc('SW', 'South Western Railway', sector='Passenger')
        db.upsert_toc('GW', 'Great Western Railway', sector='Passenger')
        db.upsert_toc('XC', 'CrossCountry', sector='Passenger')
        
        # Retrieve all
        all_tocs = db.get_all_tocs()
        
        assert len(all_tocs) == 3, f"Expected 3 TOCs, got {len(all_tocs)}"
        
        # Check structure
        for toc in all_tocs:
            assert 'toc_code' in toc
            assert 'toc_name' in toc
            assert 'sector' in toc
        
        # Verify codes are sorted
        codes = [t['toc_code'] for t in all_tocs]
        assert codes == sorted(codes), "TOCs should be sorted by code"


def test_populate_database():
    """Test populating database with TOC reference data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        resolver = TOCResolver()
        
        # Populate database
        count = resolver.populate_database(db, quiet=True)
        
        assert count > 0, "Should have inserted TOC entries"
        assert count == len(resolver.TOC_DATA), f"Should have inserted all TOC entries: {count} vs {len(resolver.TOC_DATA)}"
        
        # Verify data in database
        all_tocs = db.get_all_tocs()
        assert len(all_tocs) == count, "Database should have all TOC entries"
        
        # Verify specific TOC
        sw_name = db.get_toc_name('SW')
        assert sw_name is not None, "SW should be in database"
        assert 'South Western' in sw_name, f"Expected 'South Western' in name, got {sw_name}"


def test_trust_message_toc_query():
    """Test that TRUST messages can be queried with TOC joins."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a mock TRUST message
        db.insert_trust_message({
            'train_id': '123456',
            'actual_timestamp': '2024-01-01T12:00:00Z',
            'toc_id': 'SW',
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        })
        
        # Query with TOC join
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tm.train_id, tm.toc_id, tr.toc_name
            FROM trust_messages tm
            LEFT JOIN toc_reference tr ON tm.toc_id = tr.toc_code
            WHERE tm.train_id = '123456'
        """)
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None, "Should find TRUST message"
        assert row['toc_id'] == 'SW', "Should have SW TOC"
        assert row['toc_name'] is not None, "Should have TOC name"
        assert 'South Western' in row['toc_name'], f"Expected 'South Western' in name, got {row['toc_name']}"


def test_trust_state_toc_query():
    """Test that TRUST state can be queried with TOC joins."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Insert a mock TRUST state
        db.upsert_trust(
            train_id='123456',
            headcode='2C90',
            uid='C43876',
            toc_id='GW',
            last_event_time='2024-01-01T12:00:00Z',
            last_location='READING',
            last_delay_min=5,
            raw={'test': 'data'}
        )
        
        # Query with TOC join
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ts.train_id, ts.toc_id, tr.toc_name
            FROM trust_state ts
            LEFT JOIN toc_reference tr ON ts.toc_id = tr.toc_code
            WHERE ts.train_id = '123456'
        """)
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None, "Should find TRUST state"
        assert row['toc_id'] == 'GW', "Should have GW TOC"
        assert row['toc_name'] is not None, "Should have TOC name"
        assert 'Great Western' in row['toc_name'], f"Expected 'Great Western' in name, got {row['toc_name']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
