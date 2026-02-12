#!/usr/bin/env python3
"""Tests for TOC web page - filtering, sorting, and column display."""

import pytest
import sqlite3
import tempfile
import os


def test_toc_page_shows_all_columns():
    """Test that TOC page displays all columns from toc_reference table."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database with toc_reference table
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE toc_reference (
                toc_code TEXT PRIMARY KEY,
                toc_name TEXT NOT NULL,
                business_code TEXT,
                sector_code TEXT,
                atoc_code TEXT,
                sector TEXT,
                updated_at_utc TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO toc_reference VALUES 
            ('79', 'Great Western Railway', 'GW', '54', 'GW', 'Passenger', '2024-01-01T12:00:00Z'),
            ('61', 'London North Eastern Railway', 'LE', '55', 'LE', 'Passenger', '2024-01-01T12:00:00Z'),
            ('88', 'Southeastern', 'SE', '74', 'SE', 'Passenger', '2024-01-01T12:00:00Z')
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app and test client
        app_holder = {}
        
        def start_app():
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        # Start in thread but don't actually run the server
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test the TOC route
        response = client.get('/tocs')
        result = response.data.decode('utf-8')
        
        # Verify all column headers are present
        assert "TOC Name" in result, "Should have TOC Name column header"
        assert "TOC Code" in result, "Should have TOC Code column header"
        assert "Business Code" in result, "Should have Business Code column header"
        assert "Sector Code" in result, "Should have Sector Code column header"
        assert "ATOC Code" in result, "Should have ATOC Code column header"
        assert "Sector" in result, "Should have Sector column header"
        assert "Last Updated" in result, "Should have Last Updated column header"
        
        # Verify test data is displayed
        assert "Great Western Railway" in result
        assert "London North Eastern Railway" in result
        assert "Southeastern" in result
        assert "'79'" in result or ">79<" in result
        assert "'61'" in result or ">61<" in result
        assert "'88'" in result or ">88<" in result
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_toc_page_has_sorting():
    """Test that TOC page includes JavaScript for sorting."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE toc_reference (
                toc_code TEXT PRIMARY KEY,
                toc_name TEXT NOT NULL,
                business_code TEXT,
                sector_code TEXT,
                atoc_code TEXT,
                sector TEXT,
                updated_at_utc TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO toc_reference VALUES 
            ('79', 'Great Western Railway', 'GW', '54', 'GW', 'Passenger', '2024-01-01T12:00:00Z')
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app
        app_holder = {}
        
        def start_app():
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        response = client.get('/tocs')
        result = response.data.decode('utf-8')
        
        # Verify JavaScript for sorting is present
        assert "sortTable" in result, "Should contain sortTable function"
        assert "onclick='sortTable(" in result or "onclick=\"sortTable(" in result, "Table headers should be clickable"
        
        # Check for all 7 columns being sortable (0-6)
        for i in range(7):
            assert f"sortTable({i})" in result, f"Column {i} should be sortable"
        
        # Verify table structure for sorting
        assert "tocTable" in result, "Table should have ID 'tocTable'"
        assert "<thead>" in result or "thead" in result, "Table should have thead"
        assert "<tbody>" in result or "tbody" in result, "Table should have tbody"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_toc_page_has_filtering():
    """Test that TOC page includes JavaScript for filtering."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE toc_reference (
                toc_code TEXT PRIMARY KEY,
                toc_name TEXT NOT NULL,
                business_code TEXT,
                sector_code TEXT,
                atoc_code TEXT,
                sector TEXT,
                updated_at_utc TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO toc_reference VALUES 
            ('79', 'Great Western Railway', 'GW', '54', 'GW', 'Passenger', '2024-01-01T12:00:00Z')
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app
        app_holder = {}
        
        def start_app():
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        response = client.get('/tocs')
        result = response.data.decode('utf-8')
        
        # Verify filtering input box is present
        assert "tableFilter" in result, "Should contain filter input box"
        assert "Filter by TOC name, code, sector" in result or "placeholder" in result, "Should have filter placeholder"
        assert "updateFilter" in result, "Should contain updateFilter function"
        
        # Verify filter count span
        assert "filterCount" in result, "Should contain filter count span"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_toc_name_is_first_column():
    """Test that TOC Name is the first column in the table."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE toc_reference (
                toc_code TEXT PRIMARY KEY,
                toc_name TEXT NOT NULL,
                business_code TEXT,
                sector_code TEXT,
                atoc_code TEXT,
                sector TEXT,
                updated_at_utc TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO toc_reference VALUES 
            ('79', 'Great Western Railway', 'GW', '54', 'GW', 'Passenger', '2024-01-01T12:00:00Z')
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app
        app_holder = {}
        
        def start_app():
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        response = client.get('/tocs')
        result = response.data.decode('utf-8')
        
        # Find the table header row
        thead_start = result.find("<thead>")
        assert thead_start != -1, "Should have <thead>"
        
        # Find first <th> after <thead>
        first_th_start = result.find("<th", thead_start)
        first_th_end = result.find("</th>", first_th_start)
        first_th_content = result[first_th_start:first_th_end]
        
        # Verify it's TOC Name
        assert "TOC Name" in first_th_content, "First column should be TOC Name"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
