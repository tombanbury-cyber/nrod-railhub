#!/usr/bin/env python3
"""Tests for web dashboard UI enhancements (sorting and filtering)."""

import pytest
import sqlite3
import tempfile
import os


def test_train_detail_page_formatted_table():
    """Test that train detail page shows formatted table instead of raw JSON."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE td_state (
                td_area TEXT, headcode TEXT, last_time_ms INTEGER,
                last_time_iso TEXT, from_berth TEXT, to_berth TEXT,
                stanox TEXT, location_name TEXT, platform TEXT,
                sched_dep TEXT, sched_arr TEXT, origin_name TEXT, dest_name TEXT
            )
        """)
        conn.execute("""
            INSERT INTO td_state VALUES 
            ('EK', '2C90', 1234567890, '2024-01-01T12:00:00', 'A123', 'B456',
             '87701', 'Clapham Junction', '2', '12:00', '12:30', 'London', 'Brighton')
        """)
        conn.execute("""
            CREATE TABLE td_berth_events (
                td_area TEXT, headcode TEXT, ts_ms INTEGER, ts_iso TEXT,
                msg_type TEXT, from_berth TEXT, to_berth TEXT, descr TEXT
            )
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app and test client
        import threading
        app_ready = threading.Event()
        app_holder = {}
        
        def start_app():
            # Monkey patch Flask instantiation to capture the app
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
                app_ready.set()
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
        
        # Start in thread but don't actually run the server
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test the train route
        response = client.get('/train?area=EK&hc=2C90')
        result = response.data.decode('utf-8')
        
        # Verify the result contains table, not <pre> tag with raw dict
        assert "<table>" in result, "Should contain table"
        assert "str(dict(" not in result, "Should not contain raw JSON dict"
        assert "Train State" in result, "Should have 'Train State' heading"
        assert "TD Area" in result, "Should display TD Area field"
        assert "Headcode" in result, "Should display Headcode field"
        assert "Last Time" in result, "Should display Last Time field"
        assert "From Berth" in result, "Should display From Berth field"
        assert "To Berth" in result, "Should display To Berth field"
        assert "Location" in result, "Should display Location field"
        assert "Platform" in result, "Should display Platform field"
        assert "Schedule" in result, "Should display schedule info"
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_index_page_has_sorting_and_filtering():
    """Test that index page includes JavaScript for sorting and filtering."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE td_state (
                td_area TEXT, headcode TEXT, last_time_ms INTEGER,
                last_time_iso TEXT, from_berth TEXT, to_berth TEXT,
                stanox TEXT, location_name TEXT, platform TEXT,
                sched_dep TEXT, sched_arr TEXT, origin_name TEXT, dest_name TEXT
            )
        """)
        conn.execute("""
            INSERT INTO td_state VALUES 
            ('EK', '2C90', 1234567890, '2024-01-01T12:00:00', 'A123', 'B456',
             '87701', 'Clapham Junction', '2', '12:00', '12:30', 'London', 'Brighton')
        """)
        conn.execute("""
            CREATE TABLE trust_state (id INTEGER PRIMARY KEY)
        """)
        conn.execute("""
            CREATE TABLE vstp_state (id INTEGER PRIMARY KEY)
        """)
        conn.execute("""
            CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY)
        """)
        conn.execute("""
            CREATE TABLE td_signal_events (id INTEGER PRIMARY KEY)
        """)
        conn.commit()
        conn.close()
        
        # Create Flask app and test client
        app_holder = {}
        
        def start_app():
            # Monkey patch Flask instantiation to capture the app
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
        
        # Test the index route
        response = client.get('/')
        result = response.data.decode('utf-8')
        
        # Verify JavaScript for sorting is present
        assert "sortTable" in result, "Should contain sortTable function"
        assert "onclick='sortTable(" in result or "onclick=\"sortTable(" in result, "Table headers should be clickable"
        
        # Verify filtering input box is present
        assert "tableFilter" in result, "Should contain filter input box"
        assert "Filter by headcode, location, berth" in result or "placeholder" in result, "Should have filter placeholder"
        assert "updateFilter" in result, "Should contain updateFilter function"
        
        # Verify table structure for sorting
        assert "tdStateTable" in result, "Table should have ID"
        assert "<thead>" in result or "thead" in result, "Table should have thead"
        assert "<tbody>" in result or "tbody" in result, "Table should have tbody"
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_table_headers_clickable():
    """Test that table headers have onclick handlers for sorting."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up minimal test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE td_state (
                td_area TEXT, headcode TEXT, last_time_ms INTEGER,
                last_time_iso TEXT, from_berth TEXT, to_berth TEXT,
                stanox TEXT, location_name TEXT, platform TEXT,
                sched_dep TEXT, sched_arr TEXT, origin_name TEXT, dest_name TEXT
            )
        """)
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE vstp_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_signal_events (id INTEGER PRIMARY KEY)")
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
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        response = client.get('/')
        result = response.data.decode('utf-8')
        
        # Check for sortable columns (Area, Headcode, Time, From, To, Location, Plat, Sched)
        for i in range(8):
            assert f"sortTable({i})" in result, f"Column {i} should be sortable"
                
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
