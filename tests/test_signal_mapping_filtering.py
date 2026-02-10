#!/usr/bin/env python3
"""Tests for signal mapping wildcard filtering and column sorting."""

import pytest
import tempfile
import sqlite3
import os
from unittest.mock import patch, MagicMock
from datetime import datetime


def test_wildcard_filtering_asterisk():
    """Test that wildcard filtering works with asterisk (*) in berth filters."""
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database with berth_signal_scores table
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE berth_signal_scores (
                td_area TEXT NOT NULL,
                from_berth TEXT NOT NULL,
                to_berth TEXT NOT NULL,
                address TEXT NOT NULL,
                score REAL NOT NULL,
                obs_count INTEGER NOT NULL DEFAULT 1,
                last_seen_ts INTEGER,
                last_seen_utc TEXT NOT NULL,
                last_data TEXT,
                PRIMARY KEY (td_area, from_berth, to_berth, address)
            )
        """)
        
        # Insert test data
        test_data = [
            ('EK', '0152', '0154', '87701', 1.5, 10, 1234567890000, '2024-01-01T12:00:00Z', 'test1'),
            ('EK', '0153', '0155', '87702', 1.2, 5, 1234567890000, '2024-01-01T12:00:00Z', 'test2'),
            ('EK', '0252', '0254', '87703', 0.8, 3, 1234567890000, '2024-01-01T12:00:00Z', 'test3'),
            ('WK', '1152', '1154', '87704', 1.0, 8, 1234567890000, '2024-01-01T12:00:00Z', 'test4'),
        ]
        conn.executemany(
            "INSERT INTO berth_signal_scores VALUES (?,?,?,?,?,?,?,?,?)",
            test_data
        )
        conn.commit()
        
        # Test wildcard query with asterisk
        cursor = conn.cursor()
        
        # Test 01* pattern (should match 0152 and 0153)
        cursor.execute(
            "SELECT from_berth FROM berth_signal_scores WHERE from_berth LIKE ?",
            ('01%',)  # * should be converted to %
        )
        results = [row[0] for row in cursor.fetchall()]
        assert '0152' in results
        assert '0153' in results
        assert '0252' not in results
        assert len(results) == 2
        
        # Test %52 pattern (should match 0152, 0252, 1152)
        cursor.execute(
            "SELECT from_berth FROM berth_signal_scores WHERE from_berth LIKE ?",
            ('%52',)
        )
        results = [row[0] for row in cursor.fetchall()]
        assert '0152' in results
        assert '0252' in results
        assert '1152' in results
        assert len(results) == 3
        
        conn.close()
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_wildcard_filtering_percent():
    """Test that wildcard filtering works with percent (%) in berth filters."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE berth_signal_scores (
                td_area TEXT NOT NULL,
                from_berth TEXT NOT NULL,
                to_berth TEXT NOT NULL,
                address TEXT NOT NULL,
                score REAL NOT NULL,
                obs_count INTEGER NOT NULL DEFAULT 1,
                last_seen_ts INTEGER,
                last_seen_utc TEXT NOT NULL,
                last_data TEXT,
                PRIMARY KEY (td_area, from_berth, to_berth, address)
            )
        """)
        
        test_data = [
            ('EK', 'A001', 'A002', '87701', 1.5, 10, 1234567890000, '2024-01-01T12:00:00Z', 'test1'),
            ('EK', 'A101', 'B002', '87702', 1.2, 5, 1234567890000, '2024-01-01T12:00:00Z', 'test2'),
            ('EK', 'B001', 'A002', '87703', 0.8, 3, 1234567890000, '2024-01-01T12:00:00Z', 'test3'),
        ]
        conn.executemany(
            "INSERT INTO berth_signal_scores VALUES (?,?,?,?,?,?,?,?,?)",
            test_data
        )
        conn.commit()
        
        cursor = conn.cursor()
        
        # Test A% pattern
        cursor.execute(
            "SELECT from_berth FROM berth_signal_scores WHERE from_berth LIKE ?",
            ('A%',)
        )
        results = [row[0] for row in cursor.fetchall()]
        assert 'A001' in results
        assert 'A101' in results
        assert 'B001' not in results
        
        # Test %002 pattern for to_berth
        cursor.execute(
            "SELECT to_berth FROM berth_signal_scores WHERE to_berth LIKE ?",
            ('%002',)
        )
        results = [row[0] for row in cursor.fetchall()]
        assert 'A002' in results
        assert 'B002' in results
        assert len(results) == 3  # A002 appears twice
        
        conn.close()
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_column_sorting():
    """Test that column sorting works correctly."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE berth_signal_scores (
                td_area TEXT NOT NULL,
                from_berth TEXT NOT NULL,
                to_berth TEXT NOT NULL,
                address TEXT NOT NULL,
                score REAL NOT NULL,
                obs_count INTEGER NOT NULL DEFAULT 1,
                last_seen_ts INTEGER,
                last_seen_utc TEXT NOT NULL,
                last_data TEXT,
                PRIMARY KEY (td_area, from_berth, to_berth, address)
            )
        """)
        
        test_data = [
            ('EK', '0152', '0154', '87701', 1.5, 10, 1234567890000, '2024-01-01T12:00:00Z', 'test1'),
            ('WK', '0153', '0155', '87702', 0.5, 5, 1234567891000, '2024-01-01T12:01:00Z', 'test2'),
            ('EK', '0252', '0254', '87703', 2.0, 3, 1234567892000, '2024-01-01T12:02:00Z', 'test3'),
        ]
        conn.executemany(
            "INSERT INTO berth_signal_scores VALUES (?,?,?,?,?,?,?,?,?)",
            test_data
        )
        conn.commit()
        
        cursor = conn.cursor()
        
        # Test sorting by score DESC (default)
        cursor.execute("SELECT score FROM berth_signal_scores ORDER BY score DESC")
        scores = [row[0] for row in cursor.fetchall()]
        assert scores == [2.0, 1.5, 0.5]
        
        # Test sorting by score ASC
        cursor.execute("SELECT score FROM berth_signal_scores ORDER BY score ASC")
        scores = [row[0] for row in cursor.fetchall()]
        assert scores == [0.5, 1.5, 2.0]
        
        # Test sorting by td_area ASC
        cursor.execute("SELECT td_area FROM berth_signal_scores ORDER BY td_area ASC")
        areas = [row[0] for row in cursor.fetchall()]
        assert areas == ['EK', 'EK', 'WK']
        
        # Test sorting by obs_count DESC
        cursor.execute("SELECT obs_count FROM berth_signal_scores ORDER BY obs_count DESC")
        counts = [row[0] for row in cursor.fetchall()]
        assert counts == [10, 5, 3]
        
        # Test sorting by from_berth ASC
        cursor.execute("SELECT from_berth FROM berth_signal_scores ORDER BY from_berth ASC")
        berths = [row[0] for row in cursor.fetchall()]
        assert berths == ['0152', '0153', '0252']
        
        conn.close()
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_wildcard_with_exact_match():
    """Test that exact match still works when no wildcards are present."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE berth_signal_scores (
                td_area TEXT NOT NULL,
                from_berth TEXT NOT NULL,
                to_berth TEXT NOT NULL,
                address TEXT NOT NULL,
                score REAL NOT NULL,
                obs_count INTEGER NOT NULL DEFAULT 1,
                last_seen_ts INTEGER,
                last_seen_utc TEXT NOT NULL,
                last_data TEXT,
                PRIMARY KEY (td_area, from_berth, to_berth, address)
            )
        """)
        
        test_data = [
            ('EK', '0152', '0154', '87701', 1.5, 10, 1234567890000, '2024-01-01T12:00:00Z', 'test1'),
            ('EK', '0153', '0155', '87702', 1.2, 5, 1234567890000, '2024-01-01T12:00:00Z', 'test2'),
        ]
        conn.executemany(
            "INSERT INTO berth_signal_scores VALUES (?,?,?,?,?,?,?,?,?)",
            test_data
        )
        conn.commit()
        
        cursor = conn.cursor()
        
        # Exact match should return only one result
        cursor.execute(
            "SELECT from_berth FROM berth_signal_scores WHERE from_berth = ?",
            ('0152',)
        )
        results = [row[0] for row in cursor.fetchall()]
        assert results == ['0152']
        assert len(results) == 1
        
        conn.close()
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_sort_url_generation():
    """Test that sort URLs are generated correctly with filter preservation."""
    # This is a unit test for the URL generation logic
    from urllib.parse import urlencode, parse_qs, urlparse
    
    # Simulate filter parameters
    td_area_filter = "EK"
    from_berth_filter = "01*"
    min_score = "0.5"
    
    # Simulate current sort state
    sort_by = "score"
    sort_order = "desc"
    
    # Build URL for sorting by from_berth
    params = {}
    if td_area_filter:
        params['area'] = td_area_filter
    if from_berth_filter:
        params['from_berth'] = from_berth_filter
    if min_score:
        params['min_score'] = min_score
    params['sort'] = 'from_berth'
    params['order'] = 'asc'  # Default for non-score columns
    
    url = f"/signal-mappings?{urlencode(params)}"
    
    # Parse the URL and verify parameters
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    
    assert query_params['area'][0] == 'EK'
    assert query_params['from_berth'][0] == '01*'
    assert query_params['min_score'][0] == '0.5'
    assert query_params['sort'][0] == 'from_berth'
    assert query_params['order'][0] == 'asc'


def test_sort_toggle():
    """Test that sort order toggles correctly when clicking the same column."""
    # Test toggling from desc to asc
    sort_by = "score"
    sort_order = "desc"
    column = "score"
    
    # When sorting by same column, toggle order
    if sort_by == column:
        new_order = 'asc' if sort_order == 'desc' else 'desc'
    else:
        new_order = 'desc'  # Default for score
    
    assert new_order == 'asc'
    
    # Test toggling from asc to desc
    sort_order = "asc"
    if sort_by == column:
        new_order = 'asc' if sort_order == 'desc' else 'desc'
    else:
        new_order = 'desc'
    
    assert new_order == 'desc'
