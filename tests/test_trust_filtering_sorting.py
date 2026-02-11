#!/usr/bin/env python3
"""Tests for TRUST web page filtering and sorting functionality."""

import pytest
import sqlite3
import tempfile
import os
from unittest.mock import patch


def test_trust_state_with_filters():
    """Test that trust state view accepts and applies filters."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trust_state (
                train_id TEXT, headcode TEXT, uid TEXT, toc_id TEXT,
                last_event_time TEXT, last_location TEXT, last_delay_min INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO trust_state VALUES 
            ('111A22333', '2C90', 'C12345', 'SW', '2024-01-01 12:00:00', 'Clapham Junction', 5),
            ('222B44555', '1P33', 'P67890', 'GW', '2024-01-01 12:05:00', 'London Paddington', -2),
            ('333C66777', '2C91', 'C12346', 'SW', '2024-01-01 12:10:00', 'Brighton', 0)
        """)
        conn.execute("""
            CREATE TABLE toc_reference (
                toc_code TEXT PRIMARY KEY, toc_name TEXT
            )
        """)
        conn.execute("INSERT INTO toc_reference VALUES ('SW', 'South Western Railway')")
        conn.execute("INSERT INTO toc_reference VALUES ('GW', 'Great Western Railway')")
        conn.execute("CREATE TABLE trust_messages (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
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
        
        # Test filter by headcode
        response = client.get('/trust?view=state&headcode=2C90')
        result = response.data.decode('utf-8')
        assert '2C90' in result, "Should contain filtered headcode"
        assert '1P33' not in result, "Should not contain other headcode"
        
        # Test filter by train_id
        response = client.get('/trust?view=state&train_id=222B44555')
        result = response.data.decode('utf-8')
        assert '222B44555' in result, "Should contain filtered train_id"
        assert '111A22333' not in result, "Should not contain other train_id"
        
        # Test filter by location
        response = client.get('/trust?view=state&location=Clapham')
        result = response.data.decode('utf-8')
        assert 'Clapham Junction' in result, "Should contain filtered location"
        
        # Test that filter form is present
        response = client.get('/trust?view=state')
        result = response.data.decode('utf-8')
        assert '<form method=\'get\'' in result, "Should contain filter form"
        assert 'name=\'train_id\'' in result, "Filter form should have train_id field"
        assert 'name=\'headcode\'' in result, "Filter form should have headcode field"
        assert 'name=\'location\'' in result, "Filter form should have location field"
                
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_state_with_sorting():
    """Test that trust state view supports sorting by various columns."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trust_state (
                train_id TEXT, headcode TEXT, uid TEXT, toc_id TEXT,
                last_event_time TEXT, last_location TEXT, last_delay_min INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO trust_state VALUES 
            ('111A22333', '2C90', 'C12345', 'SW', '2024-01-01 12:00:00', 'Clapham Junction', 5),
            ('222B44555', '1P33', 'P67890', 'GW', '2024-01-01 12:05:00', 'London Paddington', -2),
            ('333C66777', '2C91', 'C12346', 'SW', '2024-01-01 12:10:00', 'Brighton', 10)
        """)
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE trust_messages (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Test sortable headers are present
        response = client.get('/trust?view=state')
        result = response.data.decode('utf-8')
        assert 'sort=train_id' in result, "Should have sortable train_id column"
        assert 'sort=headcode' in result, "Should have sortable headcode column"
        assert 'sort=delay' in result, "Should have sortable delay column"
        assert 'sort=time' in result, "Should have sortable time column"
        
        # Test sort indicators
        response = client.get('/trust?view=state&sort=headcode&order=asc')
        result = response.data.decode('utf-8')
        assert '▲' in result or '▼' in result, "Should show sort indicator"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_messages_with_filters():
    """Test that trust messages view accepts and applies filters."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trust_messages (
                id INTEGER PRIMARY KEY, train_id TEXT, actual_timestamp_ms INTEGER,
                event_type TEXT, reporting_stanox TEXT, toc_id TEXT, toc_code TEXT,
                timetable_variation INTEGER, variation_status TEXT, platform TEXT,
                created_at_utc TEXT
            )
        """)
        conn.execute("""
            INSERT INTO trust_messages VALUES 
            (1, '111A22333', 1704110400000, 'ARRIVAL', '87701', 'SW', 'SW', 5, 'LATE', '2', '2024-01-01 12:00:00'),
            (2, '222B44555', 1704110700000, 'DEPARTURE', '87702', 'GW', 'GW', -2, 'EARLY', '1', '2024-01-01 12:05:00'),
            (3, '111A22333', 1704111000000, 'DEPARTURE', '87701', 'SW', 'SW', 3, 'LATE', '2', '2024-01-01 12:10:00')
        """)
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Test filter by event_type
        response = client.get('/trust?view=messages&event_type=ARRIVAL')
        result = response.data.decode('utf-8')
        assert 'ARRIVAL' in result, "Should contain ARRIVAL events"
        # Note: Due to SQL filtering, DEPARTURE might not appear in table rows
        
        # Test filter by stanox
        response = client.get('/trust?view=messages&stanox=87701')
        result = response.data.decode('utf-8')
        assert '87701' in result, "Should contain filtered STANOX"
        
        # Test filter by platform
        response = client.get('/trust?view=messages&platform=2')
        result = response.data.decode('utf-8')
        # Platform 2 messages should be present
        
        # Test filter by variation_status
        response = client.get('/trust?view=messages&variation_status=LATE')
        result = response.data.decode('utf-8')
        assert 'LATE' in result, "Should contain LATE status"
        
        # Test that filter form is present
        response = client.get('/trust?view=messages')
        result = response.data.decode('utf-8')
        assert '<form method=\'get\'' in result, "Should contain filter form"
        assert 'name=\'event_type\'' in result, "Filter form should have event_type field"
        assert 'name=\'stanox\'' in result, "Filter form should have stanox field"
        assert 'name=\'platform\'' in result, "Filter form should have platform field"
        assert 'name=\'variation_status\'' in result, "Filter form should have variation_status field"
                
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_messages_with_sorting():
    """Test that trust messages view supports sorting by various columns."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trust_messages (
                id INTEGER PRIMARY KEY, train_id TEXT, actual_timestamp_ms INTEGER,
                event_type TEXT, reporting_stanox TEXT, toc_id TEXT, toc_code TEXT,
                timetable_variation INTEGER, variation_status TEXT, platform TEXT,
                created_at_utc TEXT
            )
        """)
        conn.execute("""
            INSERT INTO trust_messages VALUES 
            (1, '111A22333', 1704110400000, 'ARRIVAL', '87701', 'SW', 'SW', 5, 'LATE', '2', '2024-01-01 12:00:00'),
            (2, '222B44555', 1704110700000, 'DEPARTURE', '87702', 'GW', 'GW', -2, 'EARLY', '1', '2024-01-01 12:05:00'),
            (3, '111A22333', 1704111000000, 'DEPARTURE', '87701', 'SW', 'SW', 10, 'LATE', '2', '2024-01-01 12:10:00')
        """)
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Test sortable headers are present
        response = client.get('/trust?view=messages')
        result = response.data.decode('utf-8')
        assert 'sort=train_id' in result, "Should have sortable train_id column"
        assert 'sort=time' in result, "Should have sortable time column"
        assert 'sort=event_type' in result, "Should have sortable event_type column"
        assert 'sort=variation' in result, "Should have sortable variation column"
        
        # Test sort indicators
        response = client.get('/trust?view=messages&sort=event_type&order=asc')
        result = response.data.decode('utf-8')
        assert '▲' in result or '▼' in result, "Should show sort indicator"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_filter_and_sort_combined():
    """Test that filters and sorting work together correctly."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE trust_state (
                train_id TEXT, headcode TEXT, uid TEXT, toc_id TEXT,
                last_event_time TEXT, last_location TEXT, last_delay_min INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO trust_state VALUES 
            ('111A22333', '2C90', 'C12345', 'SW', '2024-01-01 12:00:00', 'Clapham Junction', 5),
            ('222B44555', '2C91', 'C12346', 'SW', '2024-01-01 12:05:00', 'London Victoria', 10),
            ('333C66777', '2C92', 'C12347', 'SW', '2024-01-01 12:10:00', 'Brighton', 2)
        """)
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE trust_messages (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Test combined filter and sort
        response = client.get('/trust?view=state&headcode=2C90&sort=delay&order=desc')
        result = response.data.decode('utf-8')
        assert '2C90' in result, "Should contain filtered headcode"
        assert 'sort=delay' in result, "Should maintain sort parameter"
        assert 'headcode=2C90' in result, "Sort URLs should preserve filter"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_view_switching_preserves_filters():
    """Test that switching between state and messages views preserves filters."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE trust_state (train_id TEXT, headcode TEXT, uid TEXT, toc_id TEXT, last_event_time TEXT, last_location TEXT, last_delay_min INTEGER)")
        conn.execute("CREATE TABLE trust_messages (id INTEGER PRIMARY KEY, train_id TEXT, actual_timestamp_ms INTEGER, event_type TEXT, reporting_stanox TEXT, toc_id TEXT, toc_code TEXT, timetable_variation INTEGER, variation_status TEXT, platform TEXT, created_at_utc TEXT)")
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Check that view switcher link exists
        response = client.get('/trust?view=state')
        result = response.data.decode('utf-8')
        assert 'view=messages' in result, "Should have link to switch to messages view"
        
        response = client.get('/trust?view=messages')
        result = response.data.decode('utf-8')
        assert 'view=state' in result, "Should have link to switch to state view"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_xss_protection():
    """Test that user inputs are properly escaped to prevent XSS attacks."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE trust_state (train_id TEXT, headcode TEXT, uid TEXT, toc_id TEXT, last_event_time TEXT, last_location TEXT, last_delay_min INTEGER)")
        conn.execute("CREATE TABLE trust_messages (id INTEGER PRIMARY KEY, train_id TEXT, actual_timestamp_ms INTEGER, event_type TEXT, reporting_stanox TEXT, toc_id TEXT, toc_code TEXT, timetable_variation INTEGER, variation_status TEXT, platform TEXT, created_at_utc TEXT)")
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT PRIMARY KEY, toc_name TEXT)")
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        
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
        
        # Test XSS attempt in state view filters
        xss_payload = '<script>alert("XSS")</script>'
        response = client.get(f'/trust?view=state&train_id={xss_payload}')
        result = response.data.decode('utf-8')
        
        # Raw script tags should not appear in output (should be escaped)
        assert '<script>alert("XSS")</script>' not in result, "XSS payload should be escaped"
        # Escaped version should be present
        assert '&lt;script&gt;' in result or '&amp;lt;' in result, "HTML entities should be escaped"
        
        # Test XSS attempt in messages view filters
        response = client.get(f'/trust?view=messages&event_type={xss_payload}')
        result = response.data.decode('utf-8')
        
        assert '<script>alert("XSS")</script>' not in result, "XSS payload should be escaped in messages view"
        assert '&lt;script&gt;' in result or '&amp;lt;' in result, "HTML entities should be escaped in messages view"
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
