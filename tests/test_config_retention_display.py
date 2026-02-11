#!/usr/bin/env python3
"""Test that data retention settings are displayed on the config page."""

import pytest
import sqlite3
import tempfile
import os
import yaml
from pathlib import Path


def test_config_page_shows_retention_settings():
    """Test that the /config page displays data retention settings."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    # Create a temporary config file with retention settings
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            'user': 'test@example.com',
            'password': 'testpass',
            'retain-trust-days': 30,
            'retain-vstp-days': 60,
            'retention-interval': 3600,
            'retention-batch-size': 1000,
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # Set up minimal test database
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE vstp_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_signal_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT, toc_name TEXT)")
        conn.execute("CREATE TABLE mapper_config (key TEXT PRIMARY KEY, value INTEGER, updated_at_utc TEXT)")
        conn.execute("INSERT INTO mapper_config VALUES ('pre_ms', 1000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('post_ms', 5000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('tau_ms', 2500, '2024-01-01T00:00:00Z')")
        conn.execute("CREATE TABLE berth_signal_observations (td_area TEXT)")
        conn.execute("CREATE TABLE berth_signal_scores (td_area TEXT)")
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
            web.start_web_dashboard(db_path, 8088, config_path, None)
            Flask.__init__ = original_flask_init
        
        # Start in thread but don't actually run the server
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test the config route (GET)
        response = client.get('/config')
        result = response.data.decode('utf-8')
        
        # Verify retention settings section is present
        assert "Data Retention" in result, "Should contain 'Data Retention' section heading"
        assert "Retain TRUST" in result, "Should display TRUST retention field"
        assert "Retain VSTP" in result, "Should display VSTP retention field"
        assert "Check Interval" in result, "Should display retention interval field"
        assert "Batch Size" in result, "Should display batch size field"
        
        # Verify the values are populated from config
        assert 'value="30"' in result or "value='30'" in result, "Should show TRUST retention value of 30 days"
        assert 'value="60"' in result or "value='60'" in result, "Should show VSTP retention value of 60 days"
        assert 'value="3600"' in result or "value='3600'" in result, "Should show interval value of 3600"
        assert 'value="1000"' in result or "value='1000'" in result, "Should show batch size value of 1000"
        
        # Verify field names for form submission
        assert 'name="retain_trust_days"' in result or "name='retain_trust_days'" in result
        assert 'name="retain_vstp_days"' in result or "name='retain_vstp_days'" in result
        assert 'name="retention_interval"' in result or "name='retention_interval'" in result
        assert 'name="retention_batch_size"' in result or "name='retention_batch_size'" in result
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)
        if os.path.exists(config_path):
            os.unlink(config_path)


def test_config_page_saves_retention_settings():
    """Test that the /config page can save retention settings."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    # Create a temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {'user': 'test@example.com'}
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # Set up minimal test database
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE vstp_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_signal_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT, toc_name TEXT)")
        conn.execute("CREATE TABLE mapper_config (key TEXT PRIMARY KEY, value INTEGER, updated_at_utc TEXT)")
        conn.execute("INSERT INTO mapper_config VALUES ('pre_ms', 1000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('post_ms', 5000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('tau_ms', 2500, '2024-01-01T00:00:00Z')")
        conn.execute("CREATE TABLE berth_signal_observations (td_area TEXT)")
        conn.execute("CREATE TABLE berth_signal_scores (td_area TEXT)")
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
            web.start_web_dashboard(db_path, 8088, config_path, None)
            Flask.__init__ = original_flask_init
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        # Submit retention settings via POST
        response = client.post('/config', data={
            'action': 'save_config',
            'user': 'test@example.com',
            'password': 'testpass',
            'host': 'publicdatafeeds.networkrail.co.uk',
            'port': '61618',
            'vhost': 'publicdatafeeds.networkrail.co.uk',
            'retain_trust_days': '45',
            'retain_vstp_days': '90',
            'retention_interval': '7200',
            'retention_batch_size': '2000',
            'width': '96',
            'pretty': 'on',
            'only_changes': 'on',
            'repeat_after': '300',
            'log_level': 'error',
            'corpus_cache': '~/.cache/openraildata/CORPUSExtract.json',
            'smart_cache': '~/.cache/openraildata/SMART.json',
            'schedule_cache': '~/.cache/openraildata/SCHEDULE_toc-full.json.gz',
            'use_schedule': 'on',
            'schedule_type': 'CIF_ALL_FULL_DAILY',
            'schedule_day': 'toc-full',
            'db_path': db_path,
            'web_port': '8088',
            'enable_mapper': 'on',
            'save_raw_json': 'on',
        })
        
        # Read the saved config file
        with open(config_path, 'r') as f:
            saved_config = yaml.safe_load(f)
        
        # Verify retention settings were saved
        assert saved_config['retain-trust-days'] == 45, "TRUST retention should be saved as 45"
        assert saved_config['retain-vstp-days'] == 90, "VSTP retention should be saved as 90"
        assert saved_config['retention-interval'] == 7200, "Retention interval should be saved as 7200"
        assert saved_config['retention-batch-size'] == 2000, "Batch size should be saved as 2000"
        
        # Verify success message in response
        result = response.data.decode('utf-8')
        assert "Configuration saved successfully" in result or "✓" in result
                
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        if os.path.exists(config_path):
            os.unlink(config_path)


def test_config_page_shows_empty_retention_when_not_set():
    """Test that retention fields are empty when not configured."""
    from flask import Flask
    from nrod_railhub import web
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    # Create a temporary config file WITHOUT retention settings
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {'user': 'test@example.com', 'password': 'testpass'}
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # Set up minimal test database
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE td_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE trust_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE vstp_state (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE td_signal_events (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE toc_reference (toc_code TEXT, toc_name TEXT)")
        conn.execute("CREATE TABLE mapper_config (key TEXT PRIMARY KEY, value INTEGER, updated_at_utc TEXT)")
        conn.execute("INSERT INTO mapper_config VALUES ('pre_ms', 1000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('post_ms', 5000, '2024-01-01T00:00:00Z')")
        conn.execute("INSERT INTO mapper_config VALUES ('tau_ms', 2500, '2024-01-01T00:00:00Z')")
        conn.execute("CREATE TABLE berth_signal_observations (td_area TEXT)")
        conn.execute("CREATE TABLE berth_signal_scores (td_area TEXT)")
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
            web.start_web_dashboard(db_path, 8088, config_path, None)
            Flask.__init__ = original_flask_init
        
        import unittest.mock as mock
        with mock.patch('flask.Flask.run'):
            start_app()
        
        app = app_holder['app']
        client = app.test_client()
        
        # Test the config route
        response = client.get('/config')
        result = response.data.decode('utf-8')
        
        # Verify retention section is present
        assert "Data Retention" in result
        
        # Verify placeholders for empty fields
        assert "Leave empty to disable" in result
                
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        if os.path.exists(config_path):
            os.unlink(config_path)
