"""JSON backup and restore of layouts and their associated objects."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


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
    yield test_client
    Path(db_path).unlink()


def _populate(client, lid="bk"):
    assert client.post("/api/layouts", json={"id": lid, "name": "Backup", "description": "d", "data": {"k": 1}}).status_code == 200
    for name, x in (("A", 0), ("B", 200)):
        r = client.post("/api/berths", json={"id": f"{lid}:{name}", "layout_id": lid, "name": name, "x": x, "y": 100,
                                              "berth_type": "platform", "z_index": 3})
        assert r.status_code == 200
    assert client.post("/api/signals", json={"id": f"{lid}:S1", "layout_id": lid, "name": "S1", "x": 5, "y": 5,
                                              "signal_type": "controlled", "rotation": 90}).status_code == 200
    assert client.post("/api/layout-objects", json={"id": f"{lid}:X", "layout_id": lid, "object_type": "level_crossing",
                                                     "name": "X", "x": 10, "y": 10, "variant": "barrier",
                                                     "berth_id": f"{lid}:A", "rotation": 45}).status_code == 200
    track = {"id": f"{lid}:T1", "layout_id": lid, "name": "T1", "start": {"berth_id": f"{lid}:A", "side": "right"},
             "end": {"berth_id": f"{lid}:B", "side": "left"}, "points": [{"x": 60, "y": 115}, {"x": 200, "y": 115}]}
    assert client.post("/api/tracks", json=track).status_code == 200
    junction = {"id": f"{lid}:J1", "layout_id": lid, "track_id": f"{lid}:T1", "name": "J", "x": 100, "y": 115}
    assert client.post("/api/junctions", json=junction).status_code == 200


def _counts(client):
    with sqlite3.connect(client.db_path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("layout", "berth", "signal", "layout_object", "layout_track", "layout_junction")}


def test_export_contains_all_associated_objects(client):
    _populate(client)
    r = client.get("/api/layouts/bk/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    doc = r.json()
    assert doc["format"] == "nrod-railhub-layout-backup" and doc["version"] == 1
    assert doc["layout"]["data"] == {"k": 1}
    assert [len(doc[k]) for k in ("berths", "signals", "objects", "tracks", "junctions")] == [2, 1, 1, 1, 1]


def test_export_missing_layout_404(client):
    assert client.get("/api/layouts/nope/export").status_code == 404


def test_round_trip_after_delete(client):
    base = _counts(client)
    _populate(client)
    before = _counts(client)
    doc = client.get("/api/layouts/bk/export").json()
    assert client.delete("/api/layouts/bk").status_code == 200
    assert _counts(client) == base
    r = client.post("/api/layouts/import", content=json.dumps(doc))
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == {"berths": 2, "signals": 1, "objects": 1, "tracks": 1, "junctions": 1}
    assert _counts(client) == before
    assert client.get("/api/layouts/bk/export").json() == doc


def test_import_existing_requires_replace(client):
    _populate(client)
    doc = client.get("/api/layouts/bk/export").json()
    assert client.post("/api/layouts/import", json=doc).status_code == 409
    doc["layout"]["name"] = "Renamed"
    doc["signals"] = []
    assert client.post("/api/layouts/import?replace=true", json=doc).status_code == 200
    assert client.get("/api/layouts/bk").json()["name"] == "Renamed"
    assert len(client.get("/api/layouts/bk/export").json()["signals"]) == 0


def test_import_id_collision_with_other_layout_is_atomic(client):
    _populate(client, "one")
    doc = client.get("/api/layouts/one/export").json()
    text = json.dumps(doc).replace("one:", "two:").replace('"one"', '"two"').replace("two:S1", "one:S1")
    before = _counts(client)
    r = client.post("/api/layouts/import", content=text)
    assert r.status_code == 409, r.text
    assert "already used" in r.json()["detail"]
    assert _counts(client) == before


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(format="other"),
    lambda d: d.update(version=99),
    lambda d: d.update(version="1"),
    lambda d: d.pop("layout"),
    lambda d: d.update(berths="x"),
    lambda d: d["berths"][0].update(x=-1),
    lambda d: d["berths"][0].update(layout_id="other"),
    lambda d: d["berths"].append(dict(d["berths"][0])),
    lambda d: d["objects"][0].update(variant="bogus"),
    lambda d: d["tracks"][0]["start"].update(berth_id="bk:missing"),
    lambda d: d["tracks"][0].update(points=[{"x": 60, "y": 115}, {"x": 130, "y": 200}, {"x": 200, "y": 115}]),
    lambda d: d["junctions"][0].update(x=101, y=0),
    lambda d: d["junctions"][0].update(track_id="bk:none"),
])
def test_invalid_backups_rejected_without_changes(client, mutate):
    _populate(client)
    doc = client.get("/api/layouts/bk/export").json()
    assert client.delete("/api/layouts/bk").status_code == 200
    baseline = _counts(client)
    mutate(doc)
    r = client.post("/api/layouts/import", json=doc)
    assert r.status_code == 422, r.text
    assert "Invalid layout backup" in r.json()["detail"]
    assert _counts(client) == baseline


def test_malformed_json_and_non_object(client):
    r = client.post("/api/layouts/import", content="{not json")
    assert r.status_code == 400 and "malformed JSON" in r.json()["detail"]
    assert client.post("/api/layouts/import", json=[1]).status_code == 422


def test_admin_page_offers_export_and_import(client):
    _populate(client)
    page = client.get("/admin").text
    assert "/api/layouts/bk/export" in page and "import-layout-file" in page
    assert "/api/layouts/bk/export" in client.get("/admin/layouts/bk").text
