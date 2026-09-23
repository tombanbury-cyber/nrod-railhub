"""Basic tests for the visualization API.

These tests verify that the FastAPI application and database schema
work correctly for the schematic visualization PoC.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.visualisation.route_inference import rebuild_route_patterns


def _create_visualisation_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    init_sql_path = Path(__file__).parent.parent / "sql" / "init_db.sql"
    with open(init_sql_path, "r", encoding="utf-8") as f:
        init_sql = f.read()

    conn = sqlite3.connect(db_path)
    conn.executescript(init_sql)
    conn.close()
    return db_path


@pytest.fixture
def visualisation_db_path() -> Iterator[str]:
    """Create and clean up a temporary visualisation database."""
    db_path = _create_visualisation_db()
    try:
        yield db_path
    finally:
        Path(db_path).unlink()


def test_database_schema():
    """Test that the database schema is created correctly."""
    db_path = _create_visualisation_db()

    try:
        conn = sqlite3.connect(db_path)
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
        assert 'berth_transition_counts' in table_names
        assert 'headcode_route_patterns' in table_names
        assert 'route_inference_runs' in table_names

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


def test_api_uses_default_db_path_when_env_unset(monkeypatch, visualisation_db_path):
    """Test API falls back to the default DB_PATH when env var is unset."""
    import app.visualisation.app as app_module

    monkeypatch.delenv("NROD_RAILHUB_DB", raising=False)
    monkeypatch.setattr(app_module, "DB_PATH", Path(visualisation_db_path))
    client = TestClient(app_module.app)
    trains_response = client.get("/trains")
    assert trains_response.status_code == 200
    trains = trains_response.json()
    assert len(trains) == 1
    assert trains[0]["id"] == "T1"
    assert trains[0]["headcode"] == "2C90"
    assert trains[0]["description"] == "Demo Train Service"
    assert trains[0]["toc"] == "GW"
    assert isinstance(trains[0]["created_at"], str)
    assert trains[0]["created_at"]

    layout_response = client.get("/layout/demo")
    assert layout_response.status_code == 200
    layout = layout_response.json()
    assert layout["id"] == "demo"
    assert layout["name"] == "Demo Station"


def test_api_uses_env_var_db_path(monkeypatch, visualisation_db_path):
    """Test API uses NROD_RAILHUB_DB when it is set."""
    import app.visualisation.app as app_module

    monkeypatch.setenv("NROD_RAILHUB_DB", visualisation_db_path)
    monkeypatch.setattr(app_module, "DB_PATH", Path("/does/not/exist.db"))

    client = TestClient(app_module.app)
    trains_response = client.get("/trains")
    assert trains_response.status_code == 200
    trains = trains_response.json()
    assert len(trains) == 1
    assert trains[0]["id"] == "T1"
    assert trains[0]["headcode"] == "2C90"
    assert trains[0]["description"] == "Demo Train Service"
    assert trains[0]["toc"] == "GW"
    assert isinstance(trains[0]["created_at"], str)
    assert trains[0]["created_at"]

    layout_response = client.get("/layout/demo")
    assert layout_response.status_code == 200
    layout = layout_response.json()
    assert layout["id"] == "demo"
    assert layout["name"] == "Demo Station"


def test_api_returns_500_when_env_var_db_lacks_visualisation_schema(monkeypatch):
    """Test API failure path when env-selected DB does not have the PoC schema."""
    import app.visualisation.app as app_module

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        monkeypatch.setenv("NROD_RAILHUB_DB", db_path)
        client = TestClient(app_module.app, raise_server_exceptions=False)
        response = client.get("/trains")

        assert response.status_code == 500
    finally:
        Path(db_path).unlink()


def test_train_chain_endpoint_returns_inference_metadata(monkeypatch):
    """Test chain endpoint exposes inferred route items with metadata."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('H1', '2C90', 'Historic One', 'GW'),
                ('H2', '2C90', 'Historic Two', 'GW'),
                ('T2', '2C90', 'Current Train', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T10:00:00Z', 'td', 'H1', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T10:01:00Z', 'td', 'H1', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T10:01:01Z', 'td', 'H1', 'berth_enter', 'BRTH_4', '{}'),
                ('2026-02-14T10:02:00Z', 'td', 'H1', 'berth_exit', 'BRTH_4', '{}'),
                ('2026-02-14T10:02:01Z', 'td', 'H1', 'berth_enter', 'BRTH_7', '{}'),
                ('2026-02-14T10:03:00Z', 'td', 'H1', 'berth_exit', 'BRTH_7', '{}'),
                ('2026-02-14T11:00:00Z', 'td', 'H2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T11:01:00Z', 'td', 'H2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T11:01:01Z', 'td', 'H2', 'berth_enter', 'BRTH_4', '{}'),
                ('2026-02-14T11:02:00Z', 'td', 'H2', 'berth_exit', 'BRTH_4', '{}'),
                ('2026-02-14T11:02:01Z', 'td', 'H2', 'berth_enter', 'BRTH_7', '{}'),
                ('2026-02-14T11:03:00Z', 'td', 'H2', 'berth_exit', 'BRTH_7', '{}'),
                ('2026-02-14T12:00:00Z', 'td', 'T2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T12:01:00Z', 'td', 'T2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T12:02:00Z', 'td', 'T2', 'berth_enter', 'BRTH_7', '{}');
            """
        )
        rebuild_route_patterns(conn)
        conn.close()

        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        response = client.get("/train/T2/chain")

        assert response.status_code == 200
        data = response.json()
        assert data["train_id"] == "T2"
        assert [item["berth_id"] for item in data["chain"]] == ["BRTH_1", "BRTH_4", "BRTH_7"]
        assert data["chain"][0]["inferred"] is False
        assert data["chain"][1]["inferred"] is True
        assert data["chain"][1]["confidence"] > 0
        assert data["chain"][1]["source"] == "historical_route_pattern"
        assert "historical headcode 2C90" in data["chain"][1]["reason"]
    finally:
        Path(db_path).unlink()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
