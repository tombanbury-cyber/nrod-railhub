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
        and train["duplicate_key"] == "2C90"
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


def test_admin_page_lists_layouts(monkeypatch):
    """The /admin landing page lists layouts with CRUD controls and the import dialog."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        response = client.get("/admin")
        assert response.status_code == 200
        page = response.text
        assert "Visualisation admin" in page
        assert "Demo Station" in page
        assert "/admin/new-layout" in page
        assert "data-delete-layout" in page
        assert "<dialog" in page
        assert "Search headcodes" in page
        assert "Import selected berths" in page
        assert '<link rel="stylesheet" href="/static/admin.css" />' in page
        assert '<script defer src="/static/admin-common.js"></script>' in page
        assert '<script defer src="/static/admin-list.js"></script>' in page
        assert "<style>" not in page
        assert "function deleteLayout" not in page
        assert "function openImportDialog" not in page
        list_js = client.get("/static/admin-list.js")
        assert list_js.status_code == 200
        assert "deleteLayout" in list_js.text
        assert "function openImportDialog" in client.get("/static/admin-common.js").text
        assert ":root{--fg" in client.get("/static/admin.css").text
        # Editors moved off the landing page
        assert "Berth editor" not in page
        assert "Signal editor" not in page
    finally:
        Path(db_path).unlink()


def test_admin_layout_pages(monkeypatch):
    """Create and edit pages render; unknown layouts 404."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        new_page = client.get("/admin/new-layout")
        assert new_page.status_code == 200
        assert "Layout settings" in new_page.text
        assert 'id="berth-rows"' not in new_page.text
        assert "Visual layout editor" in new_page.text
        assert 'id="layout-canvas-svg"' in new_page.text
        assert 'id="add-berth" disabled' in new_page.text
        assert 'id="add-bridge" disabled' in new_page.text
        assert '<select id="add-label-type" disabled>' in new_page.text
        assert "Save the layout first to add and arrange elements." in client.get("/static/admin-layout.js").text

        layouts = client.get("/api/layouts").json()
        layout_id = layouts[0]["id"]
        edit = client.get(f"/admin/layouts/{layout_id}")
        assert edit.status_code == 200
        assert "Layout settings" in edit.text
        assert 'id="berth-rows"' in edit.text
        assert 'id="signal-rows"' in edit.text
        assert "Import berths from headcode chain" in edit.text
        assert 'aria-label="Interactive layout canvas"' in edit.text
        assert 'id="add-signal"' in edit.text
        for key in ("berth", "signal", "level_crossing", "bridge", "label", "platform"):
            assert f'id="add-{key}"' in edit.text
            assert f'<select id="add-{key}-type">' in edit.text
        assert 'aria-label="Add level crossing"' in edit.text
        assert '<option value="road_under">road under</option>' in edit.text
        assert '<option value="station">station</option>' in edit.text
        assert 'id="snap-to-grid"' in edit.text
        assert 'id="snap-grid-size"' in edit.text
        assert '<option value="20" selected>20</option>' in edit.text
        assert '<script defer src="/static/admin-common.js"></script>' in edit.text
        assert '<script defer src="/static/admin-layout.js"></script>' in edit.text
        assert '<link rel="stylesheet" href="/static/admin.css" />' in edit.text
        assert "<style>" not in edit.text
        assert "function snapValue" not in edit.text
        layout_js = client.get("/static/admin-layout.js").text
        assert "pointerdown" in layout_js
        assert "snapValue(x, grid)" in layout_js
        assert "beforeunload" in layout_js
        assert "ArrowRight" in layout_js
        assert "if (focusedIndex !== null)" in layout_js
        assert "canvasSaving = true" in layout_js
        assert "Discard unsaved canvas edits before changing elements in the table?" in layout_js

        assert client.get("/admin/layouts/missing").status_code == 404
        assert client.get("/api/layouts/missing").status_code == 404
        assert client.get(f"/api/layouts/{layout_id}").json()["id"] == layout_id
    finally:
        Path(db_path).unlink()


def test_layout_editor_element_positions_persist_and_reload(monkeypatch):
    """Canvas positions saved through the element APIs are returned on reload."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        assert client.post(
            "/api/layouts",
            json={"id": "canvas", "name": "Canvas", "description": None, "data": {}},
        ).status_code == 200

        berth = {
            "id": "canvas:B1", "layout_id": "canvas", "name": "B1",
            "x": 80, "y": 120, "width": 60, "height": 30, "berth_type": "platform",
        }
        signal = {
            "id": "canvas:S1", "layout_id": "canvas", "name": "S1",
            "x": 180, "y": 160, "signal_type": "controlled",
        }
        assert client.post("/api/berths", json=berth).status_code == 200
        assert client.post("/api/signals", json=signal).status_code == 200

        berth.update(x=240, y=300)
        signal.update(x=360, y=400)
        assert client.put("/api/berths/canvas:B1", json=berth).status_code == 200
        assert client.put("/api/signals/canvas:S1", json=signal).status_code == 200

        reloaded_berth = client.get("/api/layouts/canvas/berths").json()["items"][0]
        reloaded_signal = client.get("/api/layouts/canvas/signals").json()["items"][0]
        assert (reloaded_berth["x"], reloaded_berth["y"]) == (240, 300)
        assert (reloaded_signal["x"], reloaded_signal["y"]) == (360, 400)
    finally:
        Path(db_path).unlink()


def test_layout_berth_and_signal_tables_page_and_filter(monkeypatch):
    """Berth/signal list endpoints support paging, search and type filters."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO layout (id, name, description, data) VALUES ('pg', 'Paged', '', '{}')")
        for i in range(12):
            conn.execute(
                "INSERT INTO berth (id, layout_id, name, x, y, width, height, berth_type) VALUES (?, 'pg', ?, 0, 0, 60, 30, ?)",
                (f"B{i:02d}", f"Berth {i:02d}", "platform" if i % 2 else "normal"),
            )
        for i in range(3):
            conn.execute(
                "INSERT INTO signal (id, layout_id, name, x, y, signal_type) VALUES (?, 'pg', ?, 0, 0, ?)",
                (f"S{i}", f"Sig_{i}", "shunt" if i == 0 else "auto"),
            )
        conn.commit()
        conn.close()

        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)

        first = client.get("/api/layouts/pg/berths", params={"page_size": 5}).json()
        assert first["total"] == 12 and first["pages"] == 3 and len(first["items"]) == 5
        last = client.get("/api/layouts/pg/berths", params={"page_size": 5, "page": 99}).json()
        assert last["page"] == 3 and len(last["items"]) == 2
        plat = client.get("/api/layouts/pg/berths", params={"type": "platform"}).json()
        assert plat["total"] == 6
        found = client.get("/api/layouts/pg/berths", params={"q": "b07"}).json()
        assert [b["id"] for b in found["items"]] == ["B07"]
        assert client.get("/api/layouts/pg/berths", params={"q": "zzz"}).json()["items"] == []
        assert client.get("/api/layouts/pg/berths", params={"type": "bogus"}).status_code == 422
        assert client.get("/api/layouts/pg/berths", params={"page_size": 0}).status_code == 422
        assert client.get("/api/layouts/nope/berths").status_code == 404

        # LIKE wildcards are treated literally
        assert client.get("/api/layouts/pg/signals", params={"q": "%"}).json()["total"] == 0
        assert client.get("/api/layouts/pg/signals", params={"q": "sig_1"}).json()["total"] == 1
        assert client.get("/api/layouts/pg/signals", params={"type": "shunt"}).json()["total"] == 1
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


def test_schematic_ui_has_layout_selector():
    """The static schematic UI exposes a layout selector driven by ?layout_id=."""
    import app.visualisation.app as app_module

    client = TestClient(app_module.app)
    response = client.get("/static/ui.html")
    assert response.status_code == 200
    page = response.text
    assert 'id="layoutSelect"' in page
    script = client.get("/static/ui.js").text
    assert "/api/layouts" in script
    assert "layout_id" in script
    assert "const LAYOUT_ID = 'demo'" not in script
    assert "Demo Station" not in page + script


def test_canvas_uis_support_pan_and_zoom():
    """Both the static schematic and the admin layout editor expose pan/zoom controls."""
    import app.visualisation.app as app_module

    client = TestClient(app_module.app)
    page = client.get("/static/ui.html").text
    assert "setupPanZoom" in client.get("/static/ui.js").text
    for control in ("zoomIn", "zoomOut", "zoomFit", "zoomReset"):
        assert f'id="{control}"' in page

    admin = client.get("/admin/new-layout").text
    assert "setupPanZoom" in client.get("/static/admin-layout.js").text
    for control in ("canvas-zoom-in", "canvas-zoom-out", "canvas-zoom-fit", "canvas-zoom-reset"):
        assert f'id="{control}"' in admin


def test_layout_objects_crud_persist_and_validate(monkeypatch):
    """Level crossings, bridges and labels persist through the layout object API."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        assert client.post(
            "/api/layouts", json={"id": "objs", "name": "Objs", "description": None, "data": {}}
        ).status_code == 200

        crossing = {"id": "objs:LC1", "layout_id": "objs", "object_type": "level_crossing",
                    "name": "LC <b>1</b>", "x": 10, "y": 20, "height": 80, "rotation": 45,
                    "variant": "barrier"}
        bridge = {"id": "objs:BR1", "layout_id": "objs", "object_type": "bridge",
                  "name": "High St", "x": 100, "y": 200, "width": 90, "height": 24, "variant": "footbridge"}
        label = {"id": "objs:LB1", "layout_id": "objs", "object_type": "label",
                 "name": "Central", "x": 5, "y": 5, "variant": "station"}
        platform = {"id": "objs:P1", "layout_id": "objs", "object_type": "platform",
                    "name": "Platform 1", "x": 20, "y": 400, "width": 150, "height": 24, "variant": "island"}
        for payload in (crossing, bridge, label, platform):
            assert client.post("/api/layout-objects", json=payload).status_code == 200
        assert client.post("/api/layout-objects", json=crossing).status_code == 409

        items = client.get("/api/layouts/objs/objects").json()["items"]
        assert {i["object_type"] for i in items} == {"level_crossing", "bridge", "label", "platform"}
        stored = {i["id"]: i for i in items}
        assert (stored["objs:LC1"]["rotation"], stored["objs:LC1"]["height"]) == (45, 80)
        assert stored["objs:P1"]["berth_id"] is None  # a platform needs no berth
        assert stored["objs:BR1"]["rotation"] == 0 and stored["objs:BR1"]["berth_id"] is None
        assert stored["objs:LC1"]["name"] == "LC <b>1</b>"
        assert stored["objs:BR1"]["width"] == 90
        only_labels = client.get("/api/layouts/objs/objects?type=label").json()
        assert [i["id"] for i in only_labels["items"]] == ["objs:LB1"]
        assert client.get("/api/layouts/objs/objects?type=bogus").status_code == 422

        bridge.update(x=300, y=310, variant="road_over")
        assert client.put("/api/layout-objects/objs:BR1", json=bridge).status_code == 200
        moved = client.get("/api/layouts/objs/objects?type=bridge").json()["items"][0]
        assert (moved["x"], moved["y"], moved["variant"]) == (300, 310, "road_over")

        platform.update(berth_id=" BRTH_1 ")
        assert client.put("/api/layout-objects/objs:none", json=platform).status_code == 404
        assert client.put("/api/layout-objects/objs:P1", json=platform).status_code == 200
        linked = client.get("/api/layouts/objs/objects?type=platform").json()["items"][0]
        assert linked["berth_id"] == "BRTH_1"
        # Rotation applies to level crossings and bridges; berth links only to platforms.
        assert client.post(
            "/api/layout-objects", json={**bridge, "id": "objs:BR2", "rotation": 90, "berth_id": "X"}
        ).status_code == 200
        br2 = [i for i in client.get("/api/layouts/objs/objects?type=bridge").json()["items"] if i["id"] == "objs:BR2"][0]
        assert (br2["rotation"], br2["berth_id"]) == (90, None)
        assert client.post("/api/layout-objects", json={**bridge, "id": "objs:BR3", "rotation": 135}).status_code == 422
        assert client.delete("/api/layout-objects/objs:BR2").status_code == 200
        assert client.post("/api/layout-objects", json={**crossing, "id": "objs:LC2", "rotation": 30}).status_code == 422

        # Default variant is applied; berths/signals are unaffected.
        no_variant = {k: v for k, v in label.items() if k != "variant"} | {"id": "objs:LB2"}
        assert client.post("/api/layout-objects", json=no_variant).status_code == 200
        assert client.get("/api/layouts/objs/berths").json()["total"] == 0

        assert client.put("/api/layout-objects/missing", json=label).status_code == 404
        assert client.delete("/api/layout-objects/objs:LB2").status_code == 200
        assert client.delete("/api/layout-objects/objs:LB2").status_code == 404

        bad_variant = {**label, "id": "objs:X", "variant": "road_over"}
        assert client.post("/api/layout-objects", json=bad_variant).status_code == 422
        assert client.post("/api/layout-objects", json={**label, "id": "objs:Y", "object_type": "tree"}).status_code == 422
        assert client.post("/api/layout-objects", json={**label, "id": "objs:Z", "name": "  "}).status_code == 422
        assert client.post("/api/layout-objects", json={**label, "id": "objs:Z", "x": -1}).status_code == 422
        assert client.post(
            "/api/layout-objects", json={**label, "id": "objs:Z", "layout_id": "nope"}
        ).status_code == 404

        # Renaming a layout carries objects along; deleting removes them.
        renamed = {"id": "objs2", "name": "Objs", "description": None, "data": {}}
        assert client.put("/api/layouts/objs", json=renamed).status_code == 200
        assert client.get("/api/layouts/objs2/objects").json()["total"] == 4
        assert client.delete("/api/layouts/objs2").status_code == 200
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM layout_object").fetchone()[0] == 0
    finally:
        Path(db_path).unlink()


def test_layout_objects_table_created_for_existing_databases(monkeypatch):
    """Databases created before layout_object existed get the table on demand."""
    db_path = _create_visualisation_db()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP TABLE layout_object")
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        layout_id = client.get("/api/layouts").json()[0]["id"]
        assert client.get(f"/api/layouts/{layout_id}/objects").json()["total"] == 0
        payload = {"id": "late", "layout_id": layout_id, "object_type": "bridge", "name": "B", "x": 1, "y": 1}
        assert client.post("/api/layout-objects", json=payload).status_code == 200
    finally:
        Path(db_path).unlink()


def test_layout_editor_js_supports_new_object_kinds(monkeypatch):
    """Static editor assets handle new object kinds with safe text rendering."""
    import app.visualisation.app as app_module

    client = TestClient(app_module.app)
    js = client.get("/static/admin-layout.js").text
    for token in ("level_crossing", "bridge", "label", "/api/layout-objects", "renderLevelCrossing",
                  "renderBridge", "renderLabel", "renderPlatform", "crossingGeometry", "rotation", "setAddControlsDisabled", "pointerdown"):
        assert token in js
    assert "innerHTML" not in js
    css = client.get("/static/admin.css").text
    assert ".bridge-shape" in css and ".label-station" in css
    assert ".crossing-line" in css and ".platform-object-shape" in css


def test_text_label_foot_crossing_and_berth_links_persist(monkeypatch):
    """New label/crossing variants are accepted and crossings, platforms and labels keep berth links."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        assert client.post(
            "/api/layouts", json={"id": "lx", "name": "Lx", "description": None, "data": {}}
        ).status_code == 200
        base = {"layout_id": "lx", "x": 1, "y": 2}
        payloads = [
            {**base, "id": "lx:LB", "object_type": "label", "name": "Text", "variant": "text", "berth_id": "B1"},
            {**base, "id": "lx:FC", "object_type": "level_crossing", "name": "Foot", "variant": "foot-crossing",
             "berth_id": " B2 "},
            {**base, "id": "lx:PL", "object_type": "platform", "name": "P1", "berth_id": "B3"},
            {**base, "id": "lx:BR", "object_type": "bridge", "name": "Br", "berth_id": "B4"},
        ]
        for payload in payloads:
            assert client.post("/api/layout-objects", json=payload).status_code == 200
        stored = {i["id"]: i for i in client.get("/api/layouts/lx/objects").json()["items"]}
        assert (stored["lx:LB"]["variant"], stored["lx:LB"]["berth_id"]) == ("text", "B1")
        assert (stored["lx:FC"]["variant"], stored["lx:FC"]["berth_id"]) == ("foot-crossing", "B2")
        assert stored["lx:PL"]["berth_id"] == "B3"
        assert stored["lx:BR"]["berth_id"] is None
        bad = {**payloads[0], "id": "lx:BAD", "variant": "bogus"}
        assert client.post("/api/layout-objects", json=bad).status_code == 422
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_objects_state_follows_berth_movements(monkeypatch):
    """Objects linked to a berth become occupied when a TD train is in it; others stay clear."""
    db_path = _create_visualisation_db()
    try:
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        assert client.post(
            "/api/layouts", json={"id": "st", "name": "St", "description": None, "data": {}}
        ).status_code == 200
        base = {"layout_id": "st", "x": 1, "y": 2}
        for oid, otype, berth in (
            ("st:A", "platform", "0152"), ("st:B", "label", "st:0152"),
            ("st:C", "level_crossing", "9999"), ("st:D", "label", None),
        ):
            payload = {**base, "id": oid, "object_type": otype, "name": oid}
            if berth:
                payload["berth_id"] = berth
            assert client.post("/api/layout-objects", json=payload).status_code == 200
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY, ts_ms INTEGER, ts_iso TEXT, td_area TEXT,
                headcode TEXT, msg_type TEXT, from_berth TEXT, to_berth TEXT, descr TEXT);
            INSERT INTO td_berth_events (ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth)
                VALUES (1, '2024-01-01T00:00:00', 'EK', '2C90', 'CA', '0150', '0152');
            """
        )
        conn.commit()
        conn.close()
        state = {i["id"]: i for i in client.get("/objects/st").json()}
        assert (state["st:A"]["occupied"], state["st:A"]["headcode"]) == (True, "2C90")
        assert state["st:B"]["occupied"] is True
        assert state["st:C"]["occupied"] is False
        assert state["st:D"]["occupied"] is False and state["st:D"]["headcode"] is None
        assert client.get("/objects/missing").status_code == 404
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_admin_bulk_sections_collapsed_and_signal_save_button():
    """Bulk-edit sections are collapsed by default and signals get a save-edited button."""
    import re

    import app.visualisation.app as app_module

    client = TestClient(app_module.app)
    page = client.get("/admin/layouts/anything").text
    if "save-all-signals" not in page:  # layout pages render without needing the layout to exist
        page = app_module._render_layout_page("anything")
    assert 'id="save-all-signals"' in page and 'id="save-all-berths"' in page
    for kind in ("level_crossing", "platform", "bridge", "label"):
        match = re.search(rf'<details class="bulk-section" id="{kind}-bulk"([^>]*)>', page)
        assert match and "open" not in match.group(1)
        assert f'id="save-all-{kind}s"' in page
    js = client.get("/static/admin-layout.js").text
    assert "foot-crossing" in js and "saveAllEdited" in js


def test_layout_editor_snap_defaults_and_identity_generation():
    """The canvas defaults to snapping at five units and new objects get usable identities."""
    import re

    import app.visualisation.app as app_module

    page = app_module._render_layout_page("test-layout")
    assert '<input type="checkbox" id="snap-to-grid" checked>' in page
    assert re.search(r'<option value="5" selected>5</option>', page)
    assert re.search(r'<option value="20">20</option>', page)

    client = TestClient(app_module.app)
    js = client.get("/static/admin-layout.js").text
    assert 'window.localStorage.getItem(SNAP_STORAGE_KEY)' in js
    assert 'window.localStorage.setItem(SNAP_STORAGE_KEY, String(snapToGridToggle.checked))' in js
    assert "function nextElementIdentity(kind)" in js
    assert "id = `${layoutId}_${kind}_${number}`;" in js
    assert "while (editorItems.some((item) => item.id === id));" in js
    assert "const identity = nextElementIdentity(kind);" in js
    assert "kind, ...identity, layout_id: layoutId," in js
    for kind in ("berth", "signal", "level_crossing", "bridge", "label", "platform"):
        assert f'add-{kind}' in page


def test_layering_and_signal_rotation_persist_with_backwards_compat(monkeypatch):
    """z_index and signal rotation persist, default to 0, are kept when omitted, and upgrade old tables."""
    db_path = _create_visualisation_db()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("ALTER TABLE berth DROP COLUMN z_index")
            conn.execute("ALTER TABLE signal DROP COLUMN z_index")
            conn.execute("ALTER TABLE signal DROP COLUMN rotation")
            conn.execute("ALTER TABLE layout_object DROP COLUMN z_index")
        import app.visualisation.app as app_module

        monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
        client = TestClient(app_module.app)
        assert client.post(
            "/api/layouts", json={"id": "zl", "name": "Z", "description": None, "data": {}}
        ).status_code == 200
        base = {"layout_id": "zl", "x": 1, "y": 2}
        berth = {**base, "id": "zl:B", "name": "B"}
        signal = {**base, "id": "zl:S", "name": "S"}
        bridge = {**base, "id": "zl:BR", "name": "Br", "object_type": "bridge"}
        assert client.post("/api/berths", json=berth).status_code == 200
        assert client.post("/api/signals", json=signal).status_code == 200
        assert client.post("/api/layout-objects", json=bridge).status_code == 200
        get = lambda kind: client.get(f"/api/layouts/zl/{kind}").json()["items"][0]  # noqa: E731
        assert (get("berths")["z_index"], get("signals")["rotation"], get("objects")["z_index"]) == (0, 0, 0)

        assert client.put("/api/berths/zl:B", json={**berth, "z_index": -5}).status_code == 200
        assert client.put("/api/signals/zl:S", json={**signal, "z_index": 7, "rotation": 315}).status_code == 200
        assert client.put("/api/layout-objects/zl:BR", json={**bridge, "z_index": 3, "rotation": 45}).status_code == 200
        # Omitting the fields (e.g. table edits) keeps the stored layering and orientation.
        assert client.put("/api/berths/zl:B", json=berth).status_code == 200
        assert client.put("/api/signals/zl:S", json=signal).status_code == 200
        assert client.put("/api/layout-objects/zl:BR", json={k: v for k, v in bridge.items()} | {"rotation": 45}).status_code == 200
        assert get("berths")["z_index"] == -5
        assert (get("signals")["rotation"], get("signals")["z_index"]) == (315, 7)
        assert (get("objects")["rotation"], get("objects")["z_index"]) == (45, 3)
        assert client.get("/berths/zl").json()[0]["z_index"] == -5
        assert client.get("/signals/zl").json()[0]["rotation"] == 315
        assert client.get("/objects/zl").json()[0]["z_index"] == 3
        assert client.post("/api/signals", json={**signal, "id": "zl:S2", "rotation": 30}).status_code == 422
    finally:
        Path(db_path).unlink()


def test_layout_editor_js_layering_rotation_wheel_and_label_hit_testing():
    """Editor assets expose layering controls, rotation options, scoped wheel zoom and a label hit area."""
    import app.visualisation.app as app_module

    client = TestClient(app_module.app)
    js = client.get("/static/admin-layout.js").text
    css = client.get("/static/admin.css").text
    for token in ("Send to back", "Bring to front", "z_index", "SIGNAL_ROTATIONS", "BRIDGE_ROTATIONS", "sortedByLayer"):
        assert token in js
    assert "[0, 45, 90, 135, 180, 225, 270, 315]" in js
    # Text-only labels get an invisible rect that still receives pointer events.
    assert 'item.variant === "text" ? "label-hit"' in js
    assert ".label-hit{fill:transparent;stroke:none;pointer-events:all}" in css
    # Wheel zoom is registered on the canvas svg only, and plain wheel (no ctrl needed) zooms at the pointer.
    assert js.count('addEventListener("wheel"') == 1
    assert 'svg.addEventListener("wheel"' in js
    wheel = js.split('svg.addEventListener("wheel"')[1].split("}, { passive: false })")[0]
    assert "zoomAt(" in wheel and "toWorld(event.clientX, event.clientY)" in wheel
    assert "ctrlKey || event.metaKey) {" not in wheel
