"""Tests for berth-sequence visualisation cards (YAML persistence, discovery, live state)."""

import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from app.visualisation.app import app

SCHEMA = """
CREATE TABLE corpus_locations (tiploc TEXT, stanox TEXT, crs TEXT, name TEXT);
CREATE TABLE smart_berths (td_area TEXT, berth TEXT, stanox TEXT, platform TEXT, stanme TEXT);
CREATE TABLE td_berth_movements (td_area TEXT, headcode TEXT, from_berth TEXT, to_berth TEXT, ts_ms INTEGER);
CREATE TABLE td_berth_events (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE td_state (td_area TEXT, headcode TEXT, uid TEXT, last_time_ms INTEGER, last_time_iso TEXT,
                       from_berth TEXT, to_berth TEXT);
CREATE TABLE physical_signal_mappings (td_area TEXT, from_berth TEXT, to_berth TEXT,
    physical_signal_number TEXT, physical_signal_location TEXT, verification_status TEXT);
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "nrod.yaml"
    cfg.write_text("user: me@example.com\npassword: s3cret\ntd_area: [EK]\ntoc_td_areas:\n  SE: [EK]\n")
    db = tmp_path / "t.db"
    monkeypatch.setenv("NROD_RAILHUB_CONFIG", str(cfg))
    monkeypatch.setenv("NROD_RAILHUB_DB", str(db))
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO corpus_locations VALUES (?,?,?,?)", [
        ("AAA", "10001", "AAA", "Alpha Town"), ("BBB", "10002", "BBB", "Beta Cross")])
    conn.executemany("INSERT INTO smart_berths VALUES (?,?,?,?,?)", [
        ("EK", "0001", "10001", "1", "ALPHA"), ("EK", "0004", "10002", "2", "BETA")])
    # observed 0001 -> 0002 -> 0004 (x3) and a rarer 0001 -> 0003 -> 0004 (x1)
    moves = [("EK", "1A01", "0001", "0002", i) for i in range(3)] + \
            [("EK", "1A01", "0002", "0004", i) for i in range(3)] + \
            [("EK", "2B02", "0001", "0003", 9), ("EK", "2B02", "0003", "0004", 10)]
    conn.executemany("INSERT INTO td_berth_movements VALUES (?,?,?,?,?)", moves)
    conn.commit()
    yield {"cfg": cfg, "db": db, "conn": conn}
    conn.close()


def _card(**over):
    body = {
        "id": "c1", "name": "Alpha-Beta", "from_station": "Alpha", "to_station": "Beta",
        "sequence": [
            {"berth": "0001", "td_area": "EK", "provenance": "observed"},
            {"berth": "0002", "td_area": "EK", "provenance": "inferred"},
            {"berth": "0004", "td_area": "EK", "provenance": "observed"},
        ],
        "display": {"stations": True, "berth_labels": True, "occupancy": True, "signals": True},
    }
    body.update(over)
    return body


def test_card_crud_persists_to_yaml_and_preserves_other_keys(env):
    client = TestClient(app)
    assert client.post("/api/cards", json=_card()).status_code == 201
    assert client.post("/api/cards", json=_card()).status_code == 409
    data = yaml.safe_load(env["cfg"].read_text())
    assert data["password"] == "s3cret" and data["td_area"] == ["EK"]
    assert data["toc_td_areas"] == {"SE": ["EK"]}
    assert data["visualisation_cards"][0]["sequence"][1]["provenance"] == "inferred"
    assert "s3cret" not in client.get("/api/cards").text

    upd = _card(name="Renamed")
    assert client.put("/api/cards/c1", json=upd).json()["name"] == "Renamed"
    assert client.get("/api/cards/c1").json()["name"] == "Renamed"
    assert client.put("/api/cards/nope", json=_card(id="nope")).status_code == 404
    assert client.delete("/api/cards/c1").status_code == 200
    assert client.delete("/api/cards/c1").status_code == 404
    assert yaml.safe_load(env["cfg"].read_text())["password"] == "s3cret"


def test_card_validation_and_corrupt_yaml_not_overwritten(env):
    client = TestClient(app)
    assert client.post("/api/cards", json=_card(id="bad id!")).status_code == 422
    assert client.post("/api/cards", json=_card(display={"stations": "maybe"})).status_code == 422
    bad = _card(sequence=[{"berth": "1", "td_area": "EK", "provenance": "guessed"}])
    assert client.post("/api/cards", json=bad).status_code == 422
    env["cfg"].write_text("user: [unclosed\n")
    assert client.post("/api/cards", json=_card()).status_code == 500
    assert env["cfg"].read_text() == "user: [unclosed\n"


def test_db_path_from_shared_yaml_when_env_unset(env, monkeypatch):
    from app.visualisation.app import get_conn
    monkeypatch.delenv("NROD_RAILHUB_DB")
    env["cfg"].write_text(f"db_path: {env['db']}\n")
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM smart_berths").fetchone()[0] == 2
    env["cfg"].write_text("db_provider: postgres\ndb_url: postgresql://x@h/d\n")
    with pytest.raises(RuntimeError):
        with get_conn():
            pass


def test_suggest_ranks_observed_alternatives(env):
    r = TestClient(app).get("/api/cards/suggest", params={"from_station": "alpha", "to_station": "BBB"}).json()
    seqs = [[i["berth"] for i in c["sequence"]] for c in r["candidates"]]
    assert seqs[0] == ["0001", "0002", "0004"]
    assert ["0001", "0003", "0004"] in seqs
    assert r["candidates"][0]["confidence"] > r["candidates"][1]["confidence"]
    assert all(i["provenance"] == "observed" for i in r["candidates"][0]["sequence"])
    assert r["candidates"][0]["sequence"][0]["station"] == "Alpha Town"


def test_suggest_handles_missing_evidence_without_inventing(env):
    env["conn"].execute("DELETE FROM td_berth_movements")
    env["conn"].commit()
    client = TestClient(app)
    r = client.get("/api/cards/suggest", params={"from_station": "Alpha", "to_station": "Beta"}).json()
    assert r["candidates"] == [] and r["notes"]
    r = client.get("/api/cards/suggest", params={"from_station": "Alpha", "to_station": "Nowhere"}).json()
    assert r["candidates"] == [] and r["notes"]


def test_pattern_fallback_marks_unobserved_items_inferred(env):
    env["conn"].executescript(
        """
        DELETE FROM td_berth_movements;
        INSERT INTO td_berth_movements VALUES ('EK','1A01','0001','0002',1);
        CREATE TABLE headcode_route_patterns (headcode TEXT, route_key TEXT, berth_sequence_json TEXT,
            observations INTEGER, confidence REAL, updated_at TEXT);
        INSERT INTO headcode_route_patterns VALUES ('1A01','k','["0001","0002","0009","0004"]',4,0.8,'x');
        """
    )
    env["conn"].commit()
    r = TestClient(app).get("/api/cards/suggest", params={"from_station": "Alpha", "to_station": "Beta"}).json()
    c = r["candidates"][0]
    assert c["method"] == "historical_route_pattern"
    assert [i["provenance"] for i in c["sequence"]] == ["observed", "observed", "inferred", "inferred"]


def test_live_state_occupancy_signals_and_polling_updates(env):
    client = TestClient(app)
    env["conn"].executescript(
        """
        INSERT INTO physical_signal_mappings VALUES ('EK','0001','0002','SN1','Up main','confirmed');
        INSERT INTO physical_signal_mappings VALUES ('EK','0002','0004','SN2','Up main','inferred');
        INSERT INTO td_state VALUES ('EK','1A01','U1',1,'t1','0001','0002');
        INSERT INTO td_state VALUES ('EK','9Z99','U2',1,'t1',NULL,'0777');
        """
    )
    env["conn"].commit()
    client.post("/api/cards", json=_card())
    st = client.get("/api/cards/c1/state", params={"max_age_s": 0}).json()
    assert [bool(i["trains"]) for i in st["items"]] == [False, True, False]
    assert st["items"][1]["trains"][0]["headcode"] == "1A01"
    assert [i["provenance"] for i in st["items"]] == ["observed", "inferred", "observed"]
    assert st["items"][0]["station"] == "Alpha Town"
    assert st["off_sequence_trains"] == 1
    assert [s["signal"] for s in st["signals"]] == ["SN1"] and st["signal_live_state"] is False

    env["conn"].execute("UPDATE td_state SET to_berth='0004' WHERE headcode='1A01'")
    env["conn"].commit()
    st = client.get("/api/cards/c1/state", params={"max_age_s": 0}).json()
    assert [bool(i["trains"]) for i in st["items"]] == [False, False, True]
    # default staleness window hides ancient (ts=1) trains
    assert client.get("/api/cards/c1/state").json()["stale_trains_hidden"] == 1
    assert client.get("/api/cards/missing/state").status_code == 404


def test_live_state_without_td_tables_degrades(env):
    env["conn"].executescript("DROP TABLE td_state; DROP TABLE physical_signal_mappings;")
    env["conn"].commit()
    client = TestClient(app)
    client.post("/api/cards", json=_card())
    st = client.get("/api/cards/c1/state").json()
    assert st["td_data_available"] is False and len(st["items"]) == 3 and st["signals"] == []


def test_cards_page_served():
    r = TestClient(app).get("/cards")
    assert r.status_code == 200 and "Corridor Cards" in r.text
