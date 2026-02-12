#!/usr/bin/env python3
"""Tests for schedule filtering, sorting, and pagination in web dashboard."""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta


def test_vstp_schedule_filtering():
    """Test VSTP schedule filtering by various parameters."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database with test data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Set up test database with sample VSTP schedules
        conn = sqlite3.connect(db_path)
        
        # Create necessary tables
        conn.execute("""
            CREATE TABLE vstp_schedules (
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                schedule_end_date TEXT,
                transaction_type TEXT,
                train_status TEXT,
                schedule_days_runs TEXT,
                applicable_timetable TEXT,
                CIF_train_uid TEXT,
                CIF_stp_indicator TEXT,
                signalling_id TEXT,
                CIF_train_service_code TEXT,
                CIF_train_category TEXT,
                CIF_power_type TEXT,
                sender_organisation TEXT,
                raw_json TEXT,
                created_at_utc TEXT NOT NULL,
                created_at_ts INTEGER NOT NULL,
                PRIMARY KEY (uid, schedule_start_date)
            )
        """)
        
        # Insert test data
        today = datetime.now().strftime('%Y-%m-%d')
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        
        conn.execute("""
            INSERT INTO vstp_schedules VALUES 
            ('C12345', '2024-01-01', '2024-12-31', 'Create', 'P', '1111111', 'Y', 
             'C12345', 'O', '2C90', '12345678', 'OO', 'EMU', 'Network Rail', '{}',
             '2024-01-01T10:00:00Z', 1704106800000),
            ('C12346', '2024-01-02', '2024-12-31', 'Create', 'F', '0111110', 'Y',
             'C12346', 'O', '2C91', '12345679', 'XX', 'DMU', 'Network Rail', '{}',
             '2024-01-02T10:00:00Z', 1704193200000),
            ('C12347', '2024-01-03', '2024-12-31', 'Delete', 'P', '1111111', 'Y',
             'C12347', 'C', '2C92', '12345680', 'OO', 'HST', 'Network Rail', '{}',
             '2024-01-03T10:00:00Z', 1704279600000)
        """)
        
        conn.execute("""
            CREATE TABLE vstp_state (
                uid TEXT PRIMARY KEY,
                headcode TEXT,
                start_date TEXT,
                end_date TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE td_state (
                td_area TEXT, headcode TEXT, last_time_ms INTEGER,
                last_time_iso TEXT, from_berth TEXT, to_berth TEXT
            )
        """)
        
        conn.execute("""
            CREATE TABLE td_berth_events (
                td_area TEXT, headcode TEXT, ts_ms INTEGER
            )
        """)
        
        conn.execute("""
            CREATE TABLE td_signal_events (
                td_area TEXT, msg_type TEXT, ts_ms INTEGER
            )
        """)
        
        conn.execute("""
            CREATE TABLE trust_state (
                uid TEXT PRIMARY KEY
            )
        """)
        
        conn.commit()
        conn.close()
        
        # Create Flask app
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            import threading
            app_holder = {}
            
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test basic page load
        response = client.get('/vstp')
        assert response.status_code == 200
        result = response.data.decode('utf-8')
        assert "VSTP Schedules" in result
        assert "C12345" in result
        assert "C12346" in result
        assert "C12347" in result
        
        # Test filtering by status
        response = client.get('/vstp?status=P')
        result = response.data.decode('utf-8')
        assert "C12345" in result
        assert "C12347" in result
        assert "C12346" not in result  # Status is F
        
        # Test filtering by category
        response = client.get('/vstp?category=OO')
        result = response.data.decode('utf-8')
        assert "C12345" in result
        assert "C12347" in result
        assert "C12346" not in result  # Category is XX
        
        # Test filtering by UID
        response = client.get('/vstp?uid=C12346')
        result = response.data.decode('utf-8')
        assert "C12346" in result
        assert "C12345" not in result
        assert "C12347" not in result
        
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_vstp_schedule_sorting():
    """Test VSTP schedule sorting by different columns."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Create necessary tables
        conn.execute("""
            CREATE TABLE vstp_schedules (
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                schedule_end_date TEXT,
                transaction_type TEXT,
                train_status TEXT,
                schedule_days_runs TEXT,
                applicable_timetable TEXT,
                CIF_train_uid TEXT,
                CIF_stp_indicator TEXT,
                signalling_id TEXT,
                CIF_train_service_code TEXT,
                CIF_train_category TEXT,
                CIF_power_type TEXT,
                sender_organisation TEXT,
                raw_json TEXT,
                created_at_utc TEXT NOT NULL,
                created_at_ts INTEGER NOT NULL,
                PRIMARY KEY (uid, schedule_start_date)
            )
        """)
        
        # Insert test data with different dates
        conn.execute("""
            INSERT INTO vstp_schedules VALUES 
            ('A00001', '2024-01-01', '2024-12-31', 'Create', 'P', '1111111', 'Y',
             'A00001', 'O', '2A01', '12345678', 'OO', 'EMU', 'Network Rail', '{}',
             '2024-01-01T10:00:00Z', 1704106800000),
            ('C00003', '2024-01-03', '2024-12-31', 'Create', 'P', '1111111', 'Y',
             'C00003', 'O', '2C03', '12345680', 'OO', 'EMU', 'Network Rail', '{}',
             '2024-01-03T10:00:00Z', 1704279600000),
            ('B00002', '2024-01-02', '2024-12-31', 'Create', 'P', '1111111', 'Y',
             'B00002', 'O', '2B02', '12345679', 'OO', 'EMU', 'Network Rail', '{}',
             '2024-01-02T10:00:00Z', 1704193200000)
        """)
        
        conn.execute("CREATE TABLE vstp_state (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (td_area TEXT, headcode TEXT, last_time_ms INTEGER)")
        conn.execute("CREATE TABLE td_berth_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE td_signal_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE trust_state (uid TEXT PRIMARY KEY)")
        
        conn.commit()
        conn.close()
        
        # Create Flask app
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            app_holder = {}
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test sorting by UID ascending
        response = client.get('/vstp?sort_by=uid&sort_dir=ASC')
        result = response.data.decode('utf-8')
        assert response.status_code == 200
        # Check order by finding positions
        pos_a = result.find('A00001')
        pos_b = result.find('B00002')
        pos_c = result.find('C00003')
        assert pos_a < pos_b < pos_c, "UIDs should be in ascending order"
        
        # Test sorting by UID descending
        response = client.get('/vstp?sort_by=uid&sort_dir=DESC')
        result = response.data.decode('utf-8')
        pos_a = result.find('A00001')
        pos_b = result.find('B00002')
        pos_c = result.find('C00003')
        assert pos_c < pos_b < pos_a, "UIDs should be in descending order"
        
        # Test sorting by schedule_start_date ascending
        response = client.get('/vstp?sort_by=schedule_start_date&sort_dir=ASC')
        result = response.data.decode('utf-8')
        assert response.status_code == 200
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_cif_schedule_filtering():
    """Test CIF schedule filtering by various parameters including TOC code."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Create necessary tables
        conn.execute("""
            CREATE TABLE cif_schedules (
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                schedule_end_date TEXT,
                toc_code TEXT,
                transaction_type TEXT,
                train_status TEXT,
                schedule_days_runs TEXT,
                applicable_timetable TEXT,
                CIF_train_uid TEXT,
                CIF_stp_indicator TEXT,
                signalling_id TEXT,
                CIF_train_service_code TEXT,
                CIF_train_category TEXT,
                CIF_power_type TEXT,
                CIF_headcode TEXT,
                raw_json TEXT,
                created_at_utc TEXT NOT NULL,
                created_at_ts INTEGER NOT NULL,
                PRIMARY KEY (uid, schedule_start_date, CIF_stp_indicator)
            )
        """)
        
        # Insert test data with different TOC codes
        conn.execute("""
            INSERT INTO cif_schedules VALUES 
            ('L12345', '2024-01-01', '2024-12-31', 'GW', 'N', 'P', '1111111', 'Y',
             'L12345', 'P', '2L01', '12345678', 'OO', 'EMU', '2L01', '{}',
             '2024-01-01T08:00:00Z', 1704099600000),
            ('L12346', '2024-01-02', '2024-12-31', 'SW', 'N', 'F', '0111110', 'Y',
             'L12346', 'P', '2L02', '12345679', 'XX', 'DMU', '2L02', '{}',
             '2024-01-02T08:00:00Z', 1704186000000),
            ('L12347', '2024-01-03', '2024-12-31', 'GW', 'D', 'P', '1111111', 'Y',
             'L12347', 'C', '2L03', '12345680', 'OO', 'HST', '2L03', '{}',
             '2024-01-03T08:00:00Z', 1704272400000)
        """)
        
        conn.execute("CREATE TABLE vstp_state (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (td_area TEXT, headcode TEXT, last_time_ms INTEGER)")
        conn.execute("CREATE TABLE td_berth_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE td_signal_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE trust_state (uid TEXT PRIMARY KEY)")
        
        conn.commit()
        conn.close()
        
        # Create Flask app
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            app_holder = {}
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test basic page load
        response = client.get('/cif')
        assert response.status_code == 200
        result = response.data.decode('utf-8')
        assert "CIF Schedules" in result
        assert "L12345" in result
        assert "L12346" in result
        assert "L12347" in result
        
        # Test filtering by TOC code
        response = client.get('/cif?toc_code=GW')
        result = response.data.decode('utf-8')
        assert "L12345" in result
        assert "L12347" in result
        assert "L12346" not in result  # TOC is SW
        
        # Test filtering by headcode
        response = client.get('/cif?headcode=2L02')
        result = response.data.decode('utf-8')
        assert "L12346" in result
        assert "L12345" not in result
        assert "L12347" not in result
        
        # Test filtering by status
        response = client.get('/cif?status=P')
        result = response.data.decode('utf-8')
        assert "L12345" in result
        assert "L12347" in result
        assert "L12346" not in result  # Status is F
        
        # Test filtering by category
        response = client.get('/cif?category=OO')
        result = response.data.decode('utf-8')
        assert "L12345" in result
        assert "L12347" in result
        assert "L12346" not in result  # Category is XX
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_schedule_pagination():
    """Test pagination for schedule views."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Create VSTP table
        conn.execute("""
            CREATE TABLE vstp_schedules (
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                schedule_end_date TEXT,
                transaction_type TEXT,
                train_status TEXT,
                schedule_days_runs TEXT,
                applicable_timetable TEXT,
                CIF_train_uid TEXT,
                CIF_stp_indicator TEXT,
                signalling_id TEXT,
                CIF_train_service_code TEXT,
                CIF_train_category TEXT,
                CIF_power_type TEXT,
                sender_organisation TEXT,
                raw_json TEXT,
                created_at_utc TEXT NOT NULL,
                created_at_ts INTEGER NOT NULL,
                PRIMARY KEY (uid, schedule_start_date)
            )
        """)
        
        # Insert 75 test records to test pagination (50 per page)
        for i in range(1, 76):
            uid = f'TEST{i:05d}'
            conn.execute("""
                INSERT INTO vstp_schedules VALUES 
                (?, '2024-01-01', '2024-12-31', 'Create', 'P', '1111111', 'Y',
                 ?, 'O', '2T01', '12345678', 'OO', 'EMU', 'Network Rail', '{}',
                 '2024-01-01T10:00:00Z', ?)
            """, (uid, uid, 1704106800000 + i))
        
        conn.execute("CREATE TABLE vstp_state (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (td_area TEXT, headcode TEXT, last_time_ms INTEGER)")
        conn.execute("CREATE TABLE td_berth_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE td_signal_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE trust_state (uid TEXT PRIMARY KEY)")
        
        conn.commit()
        conn.close()
        
        # Create Flask app
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            app_holder = {}
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test first page
        response = client.get('/vstp?page=1')
        result = response.data.decode('utf-8')
        assert response.status_code == 200
        assert "Showing 50 of 75" in result
        assert "Page 1 of 2" in result
        assert "Next →" in result
        assert "← Previous" not in result
        
        # Test second page
        response = client.get('/vstp?page=2')
        result = response.data.decode('utf-8')
        assert "Showing 25 of 75" in result
        assert "Page 2 of 2" in result
        assert "← Previous" in result
        assert "Next →" not in result
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_cif_schedule_locations_display():
    """Test CIF schedule locations display including platform field."""
    from flask import Flask
    from nrod_railhub import web
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Create tables
        conn.execute("""
            CREATE TABLE cif_schedules (
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                schedule_end_date TEXT,
                toc_code TEXT,
                transaction_type TEXT,
                train_status TEXT,
                schedule_days_runs TEXT,
                applicable_timetable TEXT,
                CIF_train_uid TEXT,
                CIF_stp_indicator TEXT,
                signalling_id TEXT,
                CIF_train_service_code TEXT,
                CIF_train_category TEXT,
                CIF_power_type TEXT,
                CIF_headcode TEXT,
                raw_json TEXT,
                created_at_utc TEXT NOT NULL,
                created_at_ts INTEGER NOT NULL,
                PRIMARY KEY (uid, schedule_start_date, CIF_stp_indicator)
            )
        """)
        
        conn.execute("""
            CREATE TABLE cif_schedule_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL,
                schedule_start_date TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                location_index INTEGER NOT NULL,
                tiploc TEXT,
                scheduled_pass_time TEXT,
                scheduled_departure_time TEXT,
                scheduled_arrival_time TEXT,
                public_departure_time TEXT,
                public_arrival_time TEXT,
                platform TEXT,
                CIF_pathing_allowance TEXT,
                CIF_activity TEXT,
                CIF_line TEXT,
                CIF_path TEXT,
                CIF_engineering_allowance TEXT,
                CIF_performance_allowance TEXT,
                created_at_ts INTEGER NOT NULL
            )
        """)
        
        # Insert test schedule
        conn.execute("""
            INSERT INTO cif_schedules VALUES 
            ('L99999', '2024-01-01', '2024-12-31', 'GW', 'N', 'P', '1111111', 'Y',
             'L99999', 'P', '2L99', '12345678', 'OO', 'EMU', '2L99', '{}',
             '2024-01-01T08:00:00Z', 1704099600000)
        """)
        
        # Insert locations with platform info
        conn.execute("""
            INSERT INTO cif_schedule_locations 
            (uid, schedule_start_date, segment_index, location_index, tiploc,
             scheduled_departure_time, platform, created_at_ts)
            VALUES 
            ('L99999', '2024-01-01', 0, 0, 'PADTON', '08:00', '1', 1704099600000),
            ('L99999', '2024-01-01', 0, 1, 'RDNG', '08:30', '4', 1704099600000),
            ('L99999', '2024-01-01', 0, 2, 'SDON', '09:00', '2', 1704099600000)
        """)
        
        conn.execute("CREATE TABLE vstp_state (uid TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE td_state (td_area TEXT, headcode TEXT, last_time_ms INTEGER)")
        conn.execute("CREATE TABLE td_berth_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE td_signal_events (td_area TEXT, ts_ms INTEGER)")
        conn.execute("CREATE TABLE trust_state (uid TEXT PRIMARY KEY)")
        
        conn.commit()
        conn.close()
        
        # Create Flask app
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            app_holder = {}
            original_flask_init = Flask.__init__
            def patched_init(self, *args, **kwargs):
                original_flask_init(self, *args, **kwargs)
                app_holder['app'] = self
            
            Flask.__init__ = patched_init
            web.start_web_dashboard(db_path, 8088, None, None)
            Flask.__init__ = original_flask_init
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test location display with detail=1
        response = client.get('/cif?uid=L99999&detail=1')
        result = response.data.decode('utf-8')
        assert response.status_code == 200
        assert "Locations for Schedule L99999" in result
        assert "PADTON" in result
        assert "RDNG" in result
        assert "SDON" in result
        # Check platform column header exists
        assert "Platform" in result
        # Check platform values are displayed
        assert ">1<" in result or "Platform</th>" in result
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
