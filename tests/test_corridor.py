#!/usr/bin/env python3
"""Tests for the station-to-station corridor explorer on the Berth / Signal page."""

import datetime
import sqlite3
import unittest.mock as mock

import pytest
from flask import Flask

from nrod_railhub import corridor, web
from nrod_railhub.database import RailDB

RUN_DATE = datetime.date(2026, 3, 4)  # a Wednesday


def _add_schedule(conn, uid, headcode, tiplocs, start="2026-01-01", end="2026-12-31",
                  days="1111111", stp="P", txn="Create"):
    conn.execute(
        "INSERT INTO cif_schedules(uid, schedule_start_date, schedule_end_date, toc_code, "
        "transaction_type, schedule_days_runs, CIF_stp_indicator, CIF_headcode) VALUES (?,?,?,?,?,?,?,?)",
        (uid, start, end, "SE", txn, days, stp, headcode),
    )
    for i, (tiploc, kind) in enumerate(tiplocs):
        times = {"scheduled_pass_time": "1000"} if kind == "pass" else {"public_departure_time": "1001"}
        conn.execute(
            "INSERT INTO cif_schedule_locations(uid, schedule_start_date, segment_index, location_index, "
            "tiploc, scheduled_pass_time, public_departure_time) VALUES (?,?,?,?,?,?,?)",
            (uid, start, 0, i, tiploc, times.get("scheduled_pass_time"), times.get("public_departure_time")),
        )


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "corridor.db")
    RailDB(path)
    return path


def _populate_reference(path, with_td=True):
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO corpus_locations(tiploc, stanox, crs, nlc, name) VALUES (?,?,?,?,?)",
        [
            ("FAVRSHM", "87001", "FAV", "1", "FAVERSHAM"),
            ("WHSTABL", "87002", "WHI", "2", "WHITSTABLE"),
            ("WHITLEY", "99001", "WTB", "3", "WHITLEY BAY"),
            ("MARGATE", "87003", "MAR", "4", "MARGATE"),
            ("RAMSGAT", "87004", "RAM", "5", "RAMSGATE"),
        ],
    )
    down = [("LONDON", "call"), ("FAVRSHM", "call"), ("WHSTABL", "call"), ("MARGATE", "call"), ("RAMSGAT", "call")]
    up = [("RAMSGAT", "call"), ("MARGATE", "pass"), ("WHSTABL", "call"), ("FAVRSHM", "call"), ("LONDON", "call")]
    _add_schedule(conn, "D1", "2C10", down)
    _add_schedule(conn, "U1", "2C11", up)
    _add_schedule(conn, "X1", "2C12", down, start="2025-01-01", end="2025-12-31")  # expired
    _add_schedule(conn, "S1", "2C13", down, days="0000011")  # weekend only
    _add_schedule(conn, "C1", "2C14", down)  # permanent but cancelled by overlay below
    _add_schedule(conn, "C1", "2C14", down, start="2026-02-01", stp="C")
    if with_td:
        conn.executemany(
            "INSERT INTO smart_berths(td_area, berth, stanox, platform, stanme) VALUES (?,?,?,?,?)",
            [("EK", "0101", "87001", "1", "FAVERSHAM"), ("EK", "0102", "87002", "1", "WHITSTABLE"),
             ("EK", "0103", "87004", "2", "RAMSGATE")],
        )
        mv = [
            (1, "2026-03-04T10:00:00Z", "EK", "2C10", "0101", "0102", "CA"),
            (2, "2026-03-04T10:05:00Z", "EK", "2C10", "0101", "0102", "CA"),
            (3, "2026-03-04T10:10:00Z", "EK", "2C11", "0102", "0101", "CA"),
            (4, "2026-03-04T10:11:00Z", "EK", "9Z99", "0102", "0103", "CA"),
        ]
        conn.executemany(
            "INSERT INTO td_berth_movements(ts_ms, ts_iso, td_area, headcode, from_berth, to_berth, source_msg_type) "
            "VALUES (?,?,?,?,?,?,?)", mv)
        conn.execute(
            "INSERT INTO physical_signal_mappings(td_area, address, byte_offset, bit, from_berth, to_berth, "
            "physical_signal_number, verification_status, mapping_confidence, source) "
            "VALUES ('EK','1A',0,3,'0101','0102','EK<script>12','confirmed',0.9,'manual')")
        conn.execute(
            "INSERT INTO td_state(td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth) "
            "VALUES ('EK','2C10',1,'2026-03-04T10:05:00Z','0101','0102')")
        conn.execute(
            "INSERT INTO td_state(td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth) "
            "VALUES ('XX','2C11',1,'2026-03-04T10:05:00Z','0900','0901')")
    conn.commit()
    conn.close()


def _conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _client(path):
    holder = {}
    original = Flask.__init__

    def patched(self, *a, **kw):
        original(self, *a, **kw)
        holder["app"] = self

    with mock.patch("flask.Flask.run"), mock.patch.object(Flask, "__init__", patched):
        web.start_web_dashboard(path, 8089, None, None)
    return holder["app"].test_client()


def test_resolves_station_by_name_crs_and_tiploc(db_path):
    _populate_reference(db_path)
    c = _conn(db_path)
    for text in ("Faversham", "fav", "FAVRSHM", "87001"):
        res = corridor.resolve_station(c, text)
        assert res["status"] == "ok" and res["station"]["crs"] == "FAV"


def test_unknown_and_ambiguous_locations(db_path):
    _populate_reference(db_path)
    c = _conn(db_path)
    assert corridor.resolve_station(c, "Nowhereville")["status"] == "unknown"
    amb = corridor.resolve_station(c, "whit")
    assert amb["status"] == "ambiguous" and len(amb["candidates"]) == 2
    res = corridor.build_corridor(c, "whit", "Ramsgate", RUN_DATE)
    assert res["problems"] and "a" in res["ambiguous"]
    assert corridor.build_corridor(c, "Faversham", "Faversham", RUN_DATE)["problems"]


def test_faversham_ramsgate_direction_separation(db_path):
    _populate_reference(db_path)
    res = corridor.build_corridor(_conn(db_path), "Faversham", "Ramsgate", RUN_DATE)
    assert not res["problems"]
    assert res["orientation"]["known"] is True
    down, up = res["views"]["down"], res["views"]["up"]
    assert [t["headcode"] for t in down["trains"]] == ["2C10"]
    assert [t["headcode"] for t in up["trains"]] == ["2C11"]
    # Expired, weekend-only and cancelled schedules are excluded for the chosen date.
    all_hc = {t["headcode"] for v in res["views"].values() for t in v["trains"]}
    assert all_hc == {"2C10", "2C11"}
    # Passing and calling locations between the endpoints are listed; LONDON is outside the corridor.
    tiplocs = [l["tiploc"] for l in res["locations"]]
    assert "MARGATE" in tiplocs and "LONDON" not in tiplocs
    # Reversed selection flips the mapping rather than swapping trains.
    rev = corridor.build_corridor(_conn(db_path), "Ramsgate", "Faversham", RUN_DATE)
    assert [t["headcode"] for t in rev["views"]["down"]["trains"]] == ["2C10"]


def test_movements_signals_and_live_state(db_path):
    _populate_reference(db_path)
    res = corridor.build_corridor(_conn(db_path), "Faversham", "Ramsgate", RUN_DATE)
    down_mv = res["views"]["down"]["movements"]
    up_mv = res["views"]["up"]["movements"]
    assert [(m["from_berth"], m["to_berth"], m["count"]) for m in down_mv] == [("0101", "0102", 2)]
    assert [(m["from_berth"], m["to_berth"]) for m in up_mv] == [("0102", "0101")]
    assert down_mv[0]["signals"][0]["kind"] == "physical"
    assert down_mv[0]["signals"][0]["status"] == "confirmed"
    # Unrecognised headcode stays visible but unassigned to a direction.
    assert [m["headcodes"] for m in res["unassigned_movements"]] == [["9Z99"]]
    live = res["views"]["down"]["trains"][0]["live"]
    assert live and live[0]["berth"] == "0102" and live[0]["in_corridor_berth"]
    # Same headcode in an unrelated TD area is not shown as corridor traffic.
    assert res["views"]["up"]["trains"][0]["live"] == []


def test_sparse_topology_evidence_is_reported_not_invented(db_path):
    _populate_reference(db_path, with_td=False)
    res = corridor.build_corridor(_conn(db_path), "FAV", "RAM", RUN_DATE)
    assert res["views"]["down"]["trains"] and res["berths"] == []
    assert res["topology"] == []
    assert any("SMART" in g for g in res["gaps"])
    out = corridor.render_corridor_html(res)
    assert "Insufficient evidence" in out and "2C10" in out


def test_missing_cif_tables_do_not_crash(tmp_path):
    path = str(tmp_path / "bare.db")
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE corpus_locations(tiploc TEXT, stanox TEXT, crs TEXT, nlc TEXT, name TEXT)")
    c.executemany("INSERT INTO corpus_locations VALUES (?,?,?,?,?)",
                  [("AAA", "1", "AAA", "1", "ALPHA"), ("BBB", "2", "BBB", "2", "BETA")])
    res = corridor.build_corridor(c, "Alpha", "Beta", RUN_DATE)
    assert res["orientation"]["known"] is False
    assert set(res["views"]) == {"a_to_b", "b_to_a"}
    assert res["gaps"]


def test_unknown_orientation_is_not_assumed_up_down(db_path):
    _populate_reference(db_path)
    res = corridor.build_corridor(_conn(db_path), "Faversham", "Margate", RUN_DATE)
    assert res["orientation"]["known"] is False
    assert set(res["views"]) == {"a_to_b", "b_to_a"}
    assert [t["headcode"] for t in res["views"]["a_to_b"]["trains"]] == ["2C10"]


def test_route_renders_and_preserves_query(db_path):
    _populate_reference(db_path)
    client = _client(db_path)
    r = client.get("/signal-mappings?from_station=Faversham&to_station=Ramsgate&direction=down&date=2026-03-04")
    body = r.data.decode()
    assert r.status_code == 200
    assert "value='Faversham'" in body and "value='Ramsgate'" in body
    assert "<option value='down' selected>" in body
    assert "Down (away from London)" in body
    assert "2C10" in body and "2C11" not in body
    assert "EK&lt;script&gt;12" in body and "EK<script>12" not in body


def test_route_escapes_user_input_and_handles_unknown(db_path):
    _populate_reference(db_path)
    client = _client(db_path)
    r = client.get("/signal-mappings?from_station=<script>alert(1)</script>&to_station=Ramsgate")
    body = r.data.decode()
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in body
    assert "was not found" in body


def test_route_ambiguous_offers_choices(db_path):
    _populate_reference(db_path)
    body = _client(db_path).get("/signal-mappings?from_station=whit&to_station=Ramsgate").data.decode()
    assert "matches several stations" in body
    assert "from_station=WHI" in body and "from_station=WTB" in body


def test_route_without_corridor_tables(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    r = _client(path).get("/signal-mappings?from_station=A&to_station=B")
    assert r.status_code == 200
