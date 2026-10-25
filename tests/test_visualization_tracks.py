"""Track and junction persistence for the visualisation layout editor."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.visualisation import tracks as track_store


@pytest.fixture
def client(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.executescript((Path(__file__).parent.parent / "sql" / "init_db.sql").read_text(encoding="utf-8"))
    conn.close()
    import app.visualisation.app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", Path(db_path))
    test_client = TestClient(app_module.app)
    test_client.db_path = db_path
    assert test_client.post("/api/layouts", json={"id": "tl", "name": "T", "data": {}}).status_code == 200
    for berth_id, x, y in (("tl:A", 0, 100), ("tl:B", 200, 100), ("tl:C", 200, 200)):
        berth = {"id": berth_id, "layout_id": "tl", "name": berth_id, "x": x, "y": y, "width": 60, "height": 30}
        assert test_client.post("/api/berths", json=berth).status_code == 200
    yield test_client
    Path(db_path).unlink()


def _track(track_id="tl:T1", start=None, end=None, points=None):
    # A right socket is (x + width, y + 15); a left socket is (x, y + 15).
    return {
        "id": track_id, "layout_id": "tl", "name": track_id,
        "start": start or {"berth_id": "tl:A", "side": "right"},
        "end": end or {"berth_id": "tl:B", "side": "left"},
        "points": points or [{"x": 60, "y": 115}, {"x": 200, "y": 115}],
    }


def _branch():
    """A track from berth C's left socket to junction J1 at (130, 115)."""
    return _track("tl:T2", start={"berth_id": "tl:C", "side": "left"}, end={"junction_id": "tl:J1"},
                  points=[{"x": 200, "y": 215}, {"x": 150, "y": 215}, {"x": 130, "y": 195}, {"x": 130, "y": 115}])


def _junction(junction_id="tl:J1", x=130, y=115, track_id="tl:T1"):
    return {"id": junction_id, "layout_id": "tl", "track_id": track_id, "name": junction_id, "x": x, "y": y}


def test_track_tables_created_for_existing_databases(client):
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("DROP TABLE layout_track")
        conn.execute("DROP TABLE layout_junction")
    assert client.get("/api/layouts/tl/tracks").json() == {"items": [], "total": 0}
    assert client.get("/api/layouts/tl/junctions").json()["total"] == 0
    assert client.post("/api/tracks", json=_track()).status_code == 200


def test_track_round_trip_and_endpoints(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    items = client.get("/api/layouts/tl/tracks").json()["items"]
    assert items == [_track()]
    assert client.get("/tracks/tl").json()["tracks"] == items
    updated = _track(points=[{"x": 60, "y": 115}, {"x": 80, "y": 115}, {"x": 180, "y": 215}, {"x": 200, "y": 215}],
                     end={"berth_id": "tl:C", "side": "left"})
    assert client.put("/api/tracks/tl:T1", json=updated).status_code == 200
    assert client.get("/api/layouts/tl/tracks").json()["items"][0]["end"] == {"berth_id": "tl:C", "side": "left"}
    assert client.put("/api/tracks/tl:T1", json={**updated, "id": "other"}).status_code == 422
    assert client.delete("/api/tracks/tl:T1").status_code == 200
    assert client.delete("/api/tracks/tl:T1").status_code == 404


@pytest.mark.parametrize(
    "mutation, status",
    [
        ({"points": [{"x": 60, "y": 115}, {"x": 130, "y": 160}, {"x": 200, "y": 115}]}, 422),  # not 0/45/90
        ({"points": [{"x": 60, "y": 115}]}, 422),  # too few points
        ({"points": [{"x": 60, "y": 115}, {"x": 60, "y": 115}, {"x": 200, "y": 115}]}, 422),  # zero length
        ({"points": [{"x": 61, "y": 115}, {"x": 200, "y": 115}]}, 422),  # not at the socket
        ({"points": [{"x": 60, "y": -1}, {"x": 200, "y": 115}]}, 422),
        ({"start": {"berth_id": "tl:A"}}, 422),  # side missing
        ({"start": {"berth_id": "tl:A", "side": "top"}}, 422),
        ({"start": {"berth_id": "tl:A", "side": "right", "junction_id": "x"}}, 422),
        ({"start": {"berth_id": "nope", "side": "right"}}, 404),
        ({"layout_id": "missing"}, 404),
        ({"end": {"berth_id": "tl:A", "side": "right"}, "points": [{"x": 60, "y": 115}, {"x": 60, "y": 130},
                                                                    {"x": 60, "y": 115}]}, 422),
    ],
)
def test_track_validation(client, mutation, status):
    assert client.post("/api/tracks", json={**_track(), **mutation}).status_code == status
    assert client.get("/api/layouts/tl/tracks").json()["total"] == 0


def test_track_endpoints_follow_socket_midpoints_for_odd_heights(client):
    berth = {"id": "tl:O", "layout_id": "tl", "name": "O", "x": 300, "y": 10, "width": 61, "height": 31}
    assert client.post("/api/berths", json=berth).status_code == 200
    assert track_store.socket_point(berth, "left") == (300, 26)
    assert track_store.socket_point(berth, "right") == (361, 26)
    track = _track("tl:TO", start={"berth_id": "tl:O", "side": "right"}, end={"berth_id": "tl:B", "side": "left"},
                   points=[{"x": 361, "y": 26}, {"x": 361, "y": 115}, {"x": 200, "y": 115}])
    assert client.post("/api/tracks", json=track).status_code == 200


def test_junctions_attach_tracks_and_stay_coherent(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    assert client.post("/api/junctions", json=_junction(x=130, y=130)).status_code == 422  # off the route
    assert client.post("/api/junctions", json=_junction(track_id="nope")).status_code == 404
    assert client.post("/api/junctions", json=_junction()).status_code == 200
    # A second track starts at berth C and ends on the junction (all segments 0/45/90 degrees).
    assert client.post("/api/tracks", json={**_branch(), "points": [
        {"x": 200, "y": 215}, {"x": 150, "y": 215}, {"x": 130, "y": 195}, {"x": 130, "y": 100}]}).status_code == 422
    branch = _branch()
    assert client.post("/api/tracks", json=branch).status_code == 200
    stored = {t["id"]: t for t in client.get("/api/layouts/tl/tracks").json()["items"]}
    assert stored["tl:T2"]["end"] == {"junction_id": "tl:J1"}
    # The endpoint must coincide with the junction.
    assert client.put("/api/tracks/tl:T2", json={**branch, "points": branch["points"][:-1] + [{"x": 130, "y": 100}]}).status_code == 422
    # Moving a junction that has connected tracks is refused; renaming it is fine.
    assert client.put("/api/junctions/tl:J1", json=_junction(x=150)).status_code == 409
    assert client.put("/api/junctions/tl:J1", json={**_junction(), "name": "Renamed"}).status_code == 200
    # Editing the host route keeps its junction on the new route.
    host = _track(points=[{"x": 60, "y": 115}, {"x": 100, "y": 115}, {"x": 120, "y": 95}, {"x": 140, "y": 95},
                          {"x": 160, "y": 115}, {"x": 200, "y": 115}])
    assert client.put("/api/tracks/tl:T1", json=host).status_code == 200
    junction = client.get("/api/layouts/tl/junctions").json()["items"][0]
    on_route = track_store.nearest_point_on_route(track_store.route_tuples(host["points"]), junction["x"], junction["y"])
    assert on_route[2] == 0


def test_junction_cycles_are_rejected(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    assert client.post("/api/junctions", json=_junction()).status_code == 200
    loop = _track("tl:T1", start={"berth_id": "tl:A", "side": "right"}, end={"junction_id": "tl:J1"},
                  points=[{"x": 60, "y": 115}, {"x": 130, "y": 115}])
    assert client.put("/api/tracks/tl:T1", json=loop).status_code == 422


def test_deleting_tracks_junctions_and_berths_cascades(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    assert client.post("/api/junctions", json=_junction()).status_code == 200
    branch = _branch()
    assert client.post("/api/tracks", json=branch).status_code == 200

    deleted = client.delete("/api/junctions/tl:J1").json()
    assert deleted["deleted_tracks"] == ["tl:T2"]
    assert [t["id"] for t in client.get("/api/layouts/tl/tracks").json()["items"]] == ["tl:T1"]

    assert client.post("/api/junctions", json=_junction()).status_code == 200
    assert client.post("/api/tracks", json=branch).status_code == 200
    deleted = client.delete("/api/tracks/tl:T1").json()
    assert sorted(deleted["deleted_tracks"]) == ["tl:T1", "tl:T2"]
    assert deleted["deleted_junctions"] == ["tl:J1"]

    assert client.post("/api/tracks", json=_track()).status_code == 200
    assert client.delete("/api/berths/tl:A").status_code == 200
    assert client.get("/api/layouts/tl/tracks").json()["total"] == 0


def test_berth_rename_and_layout_rename_and_delete_keep_tracks_consistent(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    assert client.post("/api/junctions", json=_junction()).status_code == 200
    berth = {"id": "tl:A2", "layout_id": "tl", "name": "A", "x": 0, "y": 100, "width": 60, "height": 30}
    assert client.put("/api/berths/tl:A", json=berth).status_code == 200
    assert client.get("/api/layouts/tl/tracks").json()["items"][0]["start"] == {"berth_id": "tl:A2", "side": "right"}

    layout = {"id": "tl2", "name": "T", "data": {}}
    assert client.put("/api/layouts/tl", json=layout).status_code == 200
    assert client.get("/api/layouts/tl2/tracks").json()["total"] == 1
    assert client.get("/api/layouts/tl2/junctions").json()["total"] == 1
    assert client.get("/api/layouts/tl/tracks").status_code == 404

    assert client.delete("/api/layouts/tl2").status_code == 200
    with sqlite3.connect(client.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM layout_track").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM layout_junction").fetchone()[0] == 0


def test_track_apis_require_existing_layout(client):
    assert client.get("/api/layouts/none/tracks").status_code == 404
    assert client.get("/api/layouts/none/junctions").status_code == 404
    assert client.get("/tracks/none").status_code == 404
    assert client.put("/api/tracks/none", json=_track()).status_code == 404
    assert client.put("/api/junctions/none", json=_junction()).status_code == 404
    assert client.delete("/api/junctions/none").status_code == 404


def test_track_points_are_stored_as_json(client):
    assert client.post("/api/tracks", json=_track()).status_code == 200
    with sqlite3.connect(client.db_path) as conn:
        raw = conn.execute("SELECT points FROM layout_track").fetchone()[0]
    assert json.loads(raw) == _track()["points"]


def test_layout_editor_exposes_track_controls_and_layers():
    import app.visualisation.app as app_module

    page = app_module._render_layout_page("tl")
    assert '/static/track-routing.js' in page
    assert page.index("track-routing.js") < page.index("admin-layout.js")
    assert 'id="add-junction"' in page and 'id="track-hint"' in page
    web = TestClient(app_module.app)
    js = web.get("/static/admin-layout.js").text
    for token in ("/api/tracks", "/api/junctions", "berth-socket", "track-layer", "junction-layer",
                  "renderSockets", "createTrack", "addJunctionAt", "deleteTrackElement", "Escape"):
        assert token in js
    assert "innerHTML" not in js
    # Tracks are drawn before (beneath) berths and other elements; junctions after (above) them.
    assert "editorSvg.insertBefore(group, junctionLayer)" in js
    assert js.index("editorSvg.append(trackLayer, junctionLayer, draftLayer)") < js.index("function renderCanvas()")
    assert ".track-line" in web.get("/static/admin.css").text
    assert "smoothPath" in web.get("/static/track-routing.js").text
