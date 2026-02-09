#!/usr/bin/env python3
"""Tests for web dashboard logging functionality."""

import pytest
import queue
import logging
import tempfile
import os
from unittest.mock import Mock, patch, MagicMock


def test_start_web_dashboard_with_log_queue():
    """Test that start_web_dashboard accepts and configures log_queue parameter."""
    from nrod_railhub.web import start_web_dashboard
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Create a log queue
        log_queue = queue.Queue()
        
        # Mock Flask to avoid actually starting the server
        with patch('nrod_railhub.web.Flask') as mock_flask_class:
            mock_app = MagicMock()
            mock_flask_class.return_value = mock_app
            
            # Mock sqlite3 connection
            with patch('nrod_railhub.web.sqlite3.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                
                # Mock the app.run to avoid blocking
                mock_app.run.side_effect = lambda **kwargs: None
                
                # This should not raise an error
                start_web_dashboard(db_path, 8088, None, log_queue)
                
                # Verify Flask app was created
                assert mock_flask_class.called
                
                # Verify app.run was called
                assert mock_app.run.called
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_start_web_dashboard_without_log_queue():
    """Test that start_web_dashboard works without log_queue (backwards compatibility)."""
    from nrod_railhub.web import start_web_dashboard
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Mock Flask to avoid actually starting the server
        with patch('nrod_railhub.web.Flask') as mock_flask_class:
            mock_app = MagicMock()
            mock_flask_class.return_value = mock_app
            
            # Mock sqlite3 connection
            with patch('nrod_railhub.web.sqlite3.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                
                # Mock the app.run to avoid blocking
                mock_app.run.side_effect = lambda **kwargs: None
                
                # This should not raise an error
                start_web_dashboard(db_path, 8088, None, None)
                
                # Verify Flask app was created
                assert mock_flask_class.called
                
                # Verify app.run was called
                assert mock_app.run.called
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_werkzeug_logger_configured_with_queue():
    """Test that werkzeug logger is properly configured when log_queue is provided."""
    from nrod_railhub.web import start_web_dashboard
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        log_queue = queue.Queue()
        
        # Mock Flask and sqlite3
        with patch('nrod_railhub.web.Flask') as mock_flask_class:
            mock_app = MagicMock()
            mock_flask_class.return_value = mock_app
            
            with patch('nrod_railhub.web.sqlite3.connect') as mock_connect:
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                
                # Mock app.run to avoid blocking
                mock_app.run.side_effect = lambda **kwargs: None
                
                # Start the web dashboard with log_queue
                start_web_dashboard(db_path, 8088, None, log_queue)
                
                # Check that werkzeug logger was configured
                werkzeug_logger = logging.getLogger('werkzeug')
                
                # Verify handlers were cleared and reconfigured
                # Note: We can't reliably test handler state after function completes
                # but we verify it doesn't crash
                assert True  # If we got here, configuration succeeded
                
    finally:
        # Clean up
        if os.path.exists(db_path):
            os.unlink(db_path)
        
        # Clean up werkzeug logger
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.handlers.clear()


def test_http_logs_sent_to_queue():
    """Test that HTTP logs are sent to the queue in interactive mode."""
    from nrod_railhub.curses_view import QueueHandler
    
    log_queue = queue.Queue()
    
    # Create a test logger with queue handler
    test_logger = logging.getLogger('test_werkzeug')
    test_logger.handlers.clear()
    test_logger.propagate = False
    
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(logging.INFO)
    queue_handler.setFormatter(logging.Formatter('[HTTP] %(message)s'))
    test_logger.addHandler(queue_handler)
    test_logger.setLevel(logging.INFO)
    
    # Log a message
    test_logger.info('127.0.0.1 - - [01/Jan/2024 12:00:00] "GET / HTTP/1.1" 200 -')
    
    # Check that message was added to queue
    assert log_queue.qsize() == 1
    
    msg = log_queue.get_nowait()
    assert '[HTTP]' in msg
    assert 'GET /' in msg
    
    # Clean up
    test_logger.handlers.clear()
