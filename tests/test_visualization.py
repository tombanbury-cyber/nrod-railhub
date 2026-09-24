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


def _create_td_visualisation_db(
    state_rows: list[tuple] | None = None,
    event_rows: list[tuple] | None = None,
) -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE td_state (
            td_area TEXT,
            headcode TEXT,
            last_time_ms INTEGER,
            last_time_iso TEXT,
            from_berth TEXT,
            to_berth TEXT,
            stanox TEXT,
            location_name TEXT,
            platform TEXT,
            uid TEXT
        );

        CREATE TABLE td_berth_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER,
            ts_iso TEXT,
            td_area TEXT,
            headcode TEXT,
            msg_type TEXT,
            from_berth TEXT,
            to_berth TEXT,
            descr TEXT
        );
        """
    )
    if state_rows:
        conn.executemany(
            "INSERT INTO td_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            state_rows,
        )
    if event_rows:
        conn.executemany(
            """
            INSERT INTO td_berth_events (
                ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event_rows,
        )
    conn.commit()
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


def test_trains_endpoint_includes_live_td_rows_alongside_train_rows(monkeypatch, visualisation_db_path):
    """Test /trains merges live TD rows with static rows and flags live duplicates."""
    conn = sqlite3.connect(visualisation_db_path)
    conn.executescript(
        """
        CREATE TABLE td_state (
            td_area TEXT,
            headcode TEXT,
            last_time_ms INTEGER,
            last_time_iso TEXT,
            from_berth TEXT,
            to_berth TEXT,
            stanox TEXT,
            location_name TEXT,
            platform TEXT,
            uid TEXT
        );
        INSERT INTO td_state VALUES
            ('EK', '1A23', 300, '2026-02-14T12:05:00Z', 'BRTH_9', 'BRTH_10', '12345', 'Waterloo', '1', NULL),
            ('WK', '2C90', 310, '2026-02-14T12:06:00Z', 'BRTH_10', 'BRTH_11', '54321', 'Victoria', '3', NULL);
        """
    )
    conn.commit()
    conn.close()

    import app.visualisation.app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", Path(visualisation_db_path))
    client = TestClient(app_module.app)
    response = client.get("/trains")

    assert response.status_code == 200
    trains = response.json()
    assert any(
        train["id"] == "EK:1A23"
        and train["headcode"] == "1A23"
        and train["td_area"] == "EK"
        and train["current_berth"] == "BRTH_10"
        and train["source"] == "td_state"
        for train in trains
    )
    assert any(
        train["id"] == "WK:2C90"
        and train["current_berth"] == "BRTH_11"
        and train["source"] == "td_state"
        for train in trains
    )
    assert any(
        train["id"] == "T1"
        and train["headcode"] == "2C90"
        and train["source"] == "train"
        and train["duplicate_key"] is None
        for train in trains
    )


def test_api_returns_500_when_env_var_db_lacks_visualisation_schema(monkeypatch):
    """Test API failure path when env-selected DB does not have the PoC schema."""
    import app.visualisation.app as app_module

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        monkeypatch.setenv("NROD_RAILHUB_DB", db_path)
        client = TestClient(app_module.app, raise_server_exceptions=False)
        response = client.post(
            "/event",
            json={
                "ts": "2026-02-14T10:00:00Z",
                "source": "td",
                "train_id": "T1",
                "event_type": "berth_enter",
                "object_id": "BRTH_1",
                "payload": {},
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "no such table: event"
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


def test_state_endpoint_reads_td_state_and_scopes_by_area(monkeypatch):
    """Test the live snapshot endpoint uses td_state and area filters."""
    db_path = _create_td_visualisation_db(
        state_rows=[
            ("EK", "2C90", 200, "2026-02-14T12:00:00Z", "BRTH_1", "BRTH_2", "87701", "Clapham Junction", "2", None),
            ("WK", "5Z50", 300, "2026-02-14T12:05:00Z", "BRTH_9", "BRTH_10", "12345", "Waterloo", "1", None),
        ],
        event_rows=[
            (150, "2026-02-14T11:59:00Z", "EK", "2C90", "CB", "BRTH_1", "BRTH_2", "EK move"),
            (250, "2026-02-14T12:04:00Z", "WK", "5Z50", "CA", "BRTH_9", "BRTH_10", "WK move"),
        ],
    )
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.get("/state?area=EK")
        assert response.status_code == 200
        data = response.json()

        assert data["source"] == "td_state"
        assert data["train_count"] == 1
        assert data["event_count"] == 1
        assert data["trains"][0]["td_area"] == "EK"
        assert data["trains"][0]["headcode"] == "2C90"
        assert data["trains"][0]["current_berth"] == "BRTH_2"
        assert data["events"][0]["td_area"] == "EK"
        assert data["events"][0]["headcode"] == "2C90"
    finally:
        Path(db_path).unlink()


def test_state_endpoint_handles_empty_td_data(monkeypatch):
    """Test the live snapshot endpoint returns an empty snapshot safely."""
    db_path = _create_td_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.get("/state")
        assert response.status_code == 200
        data = response.json()

        assert data["source"] == "empty"
        assert data["train_count"] == 0
        assert data["event_count"] == 0
        assert data["trains"] == []
        assert data["events"] == []
    finally:
        Path(db_path).unlink()


def test_state_endpoint_returns_503_for_database_errors(monkeypatch):
    """Test the live snapshot endpoint reports read failures clearly."""
    import app.visualisation.app as app_module

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_module, "_fetch_td_snapshot", boom)
    client = TestClient(app_module.app, raise_server_exceptions=False)

    response = client.get("/state")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unavailable: database is locked"


def test_admin_page_renders_editors(monkeypatch):
    """Test the browser CRUD page renders the expected editing sections."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.get("/admin")
        assert response.status_code == 200
        page = response.text
        assert "Visualisation admin" in page
        assert "Headcode chain import" in page
        assert "Search headcodes" in page
        assert "Import selected berths" in page
        assert "Layout editor" in page
        assert "Berth editor" in page
        assert "Signal editor" in page
        assert "Demo Station" in page
    finally:
        Path(db_path).unlink()


def test_admin_import_chain_endpoint_creates_selected_layout_berths(monkeypatch):
    """Test importing berth chain selections appends new berths to a layout."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO layout (id, name, description, data)
            VALUES ('north', 'North Layout', 'Import target', '{}')
            """
        )
        conn.commit()
        conn.close()

        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.post(
            "/api/berths/import-chain",
            json={
                "layout_id": "north",
                "berth_ids": ["BRTH_2", "BRTH_4", "BRTH_2"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "imported"
        assert payload["layout_id"] == "north"
        assert [row["name"] for row in payload["imported"]] == ["BRTH_2", "BRTH_4"]
        assert payload["skipped_existing"] == []

        repeat_response = client.post(
            "/api/berths/import-chain",
            json={"layout_id": "north", "berth_ids": ["BRTH_2", "BRTH_9"]},
        )
        assert repeat_response.status_code == 200
        repeat_payload = repeat_response.json()
        assert [row["name"] for row in repeat_payload["imported"]] == ["BRTH_9"]
        assert repeat_payload["skipped_existing"] == ["BRTH_2"]

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, name, x, y, width, height, berth_type
                FROM berth
                WHERE layout_id = 'north'
                ORDER BY x ASC
                """
            ).fetchall()
        finally:
            conn.close()

        assert rows == [
            ("north:BRTH_2", "BRTH_2", 50, 100, 60, 30, "normal"),
            ("north:BRTH_4", "BRTH_4", 120, 100, 60, 30, "normal"),
            ("north:BRTH_9", "BRTH_9", 190, 100, 60, 30, "normal"),
        ]
    finally:
        Path(db_path).unlink()


def test_admin_import_chain_endpoint_rejects_empty_selection(monkeypatch):
    """Test importing without any selected berths returns a validation error."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.post(
            "/api/berths/import-chain",
            json={"layout_id": "demo", "berth_ids": ["   ", ""]},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "At least one berth must be selected"
    finally:
        Path(db_path).unlink()


def test_admin_import_chain_endpoint_requires_existing_layout(monkeypatch):
    """Test importing chain berths rejects unknown layout IDs."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.post(
            "/api/berths/import-chain",
            json={"layout_id": "missing", "berth_ids": ["BRTH_2"]},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Layout not found"
    finally:
        Path(db_path).unlink()


def test_admin_crud_endpoints_manage_layout_berth_and_signal(monkeypatch):
    """Test the CRUD endpoints can create, update, rename, and delete rows."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        create_layout = client.post(
            "/api/layouts",
            json={
                "id": "north",
                "name": "North Layout",
                "description": "Original layout",
                "data": {"version": "1.0"},
            },
        )
        assert create_layout.status_code == 200

        update_layout = client.put(
            "/api/layouts/north",
            json={
                "id": "north-main",
                "name": "North Layout v2",
                "description": "Updated layout",
                "data": {"version": "2.0", "tracks": 4},
            },
        )
        assert update_layout.status_code == 200

        layout_response = client.get("/layout/north-main")
        assert layout_response.status_code == 200
        layout = layout_response.json()
        assert layout["id"] == "north-main"
        assert layout["name"] == "North Layout v2"
        assert layout["data"]["tracks"] == 4

        create_berth = client.post(
            "/api/berths",
            json={
                "id": "N-B1",
                "layout_id": "north-main",
                "name": "B1",
                "x": 10,
                "y": 20,
                "width": 70,
                "height": 35,
                "berth_type": "platform",
            },
        )
        assert create_berth.status_code == 200

        create_signal = client.post(
            "/api/signals",
            json={
                "id": "N-S1",
                "layout_id": "north-main",
                "name": "S1",
                "x": 30,
                "y": 40,
                "signal_type": "controlled",
            },
        )
        assert create_signal.status_code == 200

        update_berth = client.put(
            "/api/berths/N-B1",
            json={
                "id": "N-B1A",
                "layout_id": "north-main",
                "name": "B1A",
                "x": 15,
                "y": 25,
                "width": 80,
                "height": 40,
                "berth_type": "siding",
            },
        )
        assert update_berth.status_code == 200

        update_signal = client.put(
            "/api/signals/N-S1",
            json={
                "id": "N-S1A",
                "layout_id": "north-main",
                "name": "S1A",
                "x": 35,
                "y": 45,
                "signal_type": "shunt",
            },
        )
        assert update_signal.status_code == 200

        berths_response = client.get("/berths/north-main")
        assert berths_response.status_code == 200
        berths = berths_response.json()
        assert any(row["id"] == "N-B1A" and row["name"] == "B1A" for row in berths)

        signals_response = client.get("/signals/north-main")
        assert signals_response.status_code == 200
        signals = signals_response.json()
        assert any(row["id"] == "N-S1A" and row["signal_type"] == "shunt" for row in signals)

        assert client.delete("/api/signals/N-S1A").status_code == 200
        assert client.delete("/api/berths/N-B1A").status_code == 200
        assert client.delete("/api/layouts/north-main").status_code == 200

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM layout WHERE id = 'north-main'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM berth WHERE layout_id = 'north-main'").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM signal WHERE layout_id = 'north-main'").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        Path(db_path).unlink()


def test_admin_crud_endpoints_validate_payloads(monkeypatch):
    """Test CRUD payload validation rejects empty, invalid, and out-of-range values."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.post(
            "/api/layouts",
            json={"id": "   ", "name": "North Layout", "description": None, "data": {}},
        )
        assert response.status_code == 422

        response = client.post(
            "/api/berths",
            json={
                "id": "N-B1",
                "layout_id": "demo",
                "name": "B1",
                "x": 10,
                "y": 20,
                "width": 0,
                "height": 35,
                "berth_type": "platform",
            },
        )
        assert response.status_code == 422

        response = client.post(
            "/api/signals",
            json={
                "id": "N-S1",
                "layout_id": "demo",
                "name": "S1",
                "x": 30,
                "y": -1,
                "signal_type": "invalid",
            },
        )
        assert response.status_code == 422
    finally:
        Path(db_path).unlink()


def test_admin_crud_endpoints_require_existing_layouts(monkeypatch):
    """Test berth and signal CRUD rejects unknown layout references."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.post(
            "/api/berths",
            json={
                "id": "N-B1",
                "layout_id": "missing",
                "name": "B1",
                "x": 10,
                "y": 20,
                "width": 70,
                "height": 35,
                "berth_type": "platform",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Layout not found"

        response = client.post(
            "/api/signals",
            json={
                "id": "N-S1",
                "layout_id": "missing",
                "name": "S1",
                "x": 30,
                "y": 40,
                "signal_type": "controlled",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Layout not found"

        assert client.post(
            "/api/berths",
            json={
                "id": "N-B2",
                "layout_id": "demo",
                "name": "B2",
                "x": 15,
                "y": 25,
                "width": 80,
                "height": 40,
                "berth_type": "siding",
            },
        ).status_code == 200

        assert client.post(
            "/api/signals",
            json={
                "id": "N-S2",
                "layout_id": "demo",
                "name": "S2",
                "x": 35,
                "y": 45,
                "signal_type": "shunt",
            },
        ).status_code == 200

        response = client.put(
            "/api/berths/N-B2",
            json={
                "id": "N-B2",
                "layout_id": "missing",
                "name": "B2",
                "x": 15,
                "y": 25,
                "width": 80,
                "height": 40,
                "berth_type": "siding",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Layout not found"

        response = client.put(
            "/api/signals/N-S2",
            json={
                "id": "N-S2",
                "layout_id": "missing",
                "name": "S2",
                "x": 35,
                "y": 45,
                "signal_type": "shunt",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Layout not found"
    finally:
        Path(db_path).unlink()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
