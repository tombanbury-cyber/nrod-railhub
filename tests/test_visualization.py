"""Basic tests for the visualization API.

These tests verify that the FastAPI application and database schema
work correctly for the schematic visualization PoC.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_database_schema():
    """Test that the database schema is created correctly."""
    # Create a temporary database
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Run the init script
        init_sql_path = Path(__file__).parent.parent / 'sql' / 'init_db.sql'
        with open(init_sql_path, 'r') as f:
            init_sql = f.read()
        
        conn = sqlite3.connect(db_path)
        conn.executescript(init_sql)
        
        # Verify tables exist
        cursor = conn.cursor()
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        
        assert 'layout' in table_names
        assert 'berth' in table_names
        assert 'signal' in table_names
        assert 'train' in table_names
        assert 'event' in table_names
        
        # Verify sample data
        layout_count = cursor.execute("SELECT COUNT(*) FROM layout").fetchone()[0]
        assert layout_count == 1
        
        berth_count = cursor.execute("SELECT COUNT(*) FROM berth").fetchone()[0]
        assert berth_count == 8
        
        train_count = cursor.execute("SELECT COUNT(*) FROM train").fetchone()[0]
        assert train_count == 1
        
        conn.close()
    finally:
        Path(db_path).unlink()


def test_api_endpoints():
    """Test basic API endpoints."""
    # This test would need proper database setup
    # For now, just verify the app can be imported
    from app.visualisation.app import app
    
    assert app is not None
    assert app.title == "NROD RailHub Visualization API"


def test_event_model():
    """Test the EventCreate model."""
    from app.visualisation.app import EventCreate
    
    # Valid event
    event = EventCreate(
        ts="2026-02-14T10:00:00Z",
        source="td",
        train_id="T1",
        event_type="berth_enter",
        object_id="BRTH_1",
        payload={}
    )
    
    assert event.ts == "2026-02-14T10:00:00Z"
    assert event.train_id == "T1"
    assert event.event_type == "berth_enter"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
