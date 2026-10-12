#!/usr/bin/env python3
"""Live PostgreSQL integration tests.

These run only when ``NROD_RAILHUB_TEST_POSTGRES_URL`` points at a PostgreSQL
database the test user may create schemas in, e.g.::

    NROD_RAILHUB_TEST_POSTGRES_URL=postgresql://postgres@localhost/railhub_test pytest tests/test_postgres_integration.py

Each test works in its own throw-away schema, which is dropped afterwards.
Without the variable (or without the psycopg driver) the tests are skipped, so
the normal suite never needs a PostgreSQL server.
"""

import os
import time
import uuid
from unittest import mock
from urllib.parse import quote

import pytest

BASE_URL = os.environ.get("NROD_RAILHUB_TEST_POSTGRES_URL")
psycopg = pytest.importorskip("psycopg", reason="psycopg driver not installed")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="NROD_RAILHUB_TEST_POSTGRES_URL not set")

from nrod_railhub.database import RailDB  # noqa: E402


@pytest.fixture
def pg_url():
    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(BASE_URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    sep = "&" if "?" in BASE_URL else "?"
    try:
        yield f"{BASE_URL}{sep}options={quote('-csearch_path=' + schema)}"
    finally:
        with psycopg.connect(BASE_URL, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def db(pg_url):
    database = RailDB(pg_url, enable_mapper=False)
    yield database
    database.close()


def _scalar(db, sql, params=()):
    return db._conn.execute(sql, params).fetchone()[0]


def test_schema_is_created_on_postgres_and_reopening_is_idempotent(pg_url, db):
    assert db.provider == "postgres"
    assert _scalar(db, "SELECT COUNT(*) FROM hex_byte") == 256
    reopened = RailDB(pg_url, enable_mapper=False)
    try:
        assert _scalar(reopened, "SELECT COUNT(*) FROM hex_byte") == 256
        assert reopened._conn.table_exists("cif_schedules")
        assert "record_fingerprint" in reopened._conn.table_columns("cif_schedules")
    finally:
        reopened.close()


def test_td_events_generated_ids_and_movements(db):
    db.insert_td_berth_event(1000, "2026-01-01T00:00:01Z", "EK", "2C90", "CA", "0152", "0153")
    db.insert_td_berth_event(2000, "2026-01-01T00:00:02Z", "EK", "2C90", "CC", "", "0154")
    ids = [r[0] for r in db._conn.execute("SELECT id FROM td_berth_events ORDER BY id")]
    assert len(ids) == 2 and ids[0] < ids[1]
    movements = db._conn.execute(
        "SELECT source_event_id, from_berth, to_berth FROM td_berth_movements ORDER BY ts_ms"
    ).fetchall()
    assert [tuple(m[1:]) for m in movements] == [("0152", "0153"), ("0153", "0154")]
    assert movements[0][0] == ids[0]

    # rebuild is idempotent and reports inserted rows through rowcount, not sqlite total_changes
    assert db.rebuild_td_berth_movements() == {"scanned": 2, "inserted": 2}
    assert _scalar(db, "SELECT COUNT(*) FROM td_berth_movements") == 2

    db.upsert_td_state("EK", "2C90", 2000, "2026-01-01T00:00:02Z", "0153", "0154")
    db.upsert_td_state("EK", "2C90", 3000, "2026-01-01T00:00:03Z", "0154", "0155", stanox="87701")
    assert _scalar(db, "SELECT COUNT(*) FROM td_state") == 1
    assert _scalar(db, "SELECT stanox FROM td_state WHERE headcode='2C90'") == "87701"


def test_signal_events_bytes_and_sclass_changes(db):
    db.insert_td_signal_event(1000, "2026-01-01T00:00:01Z", "EK", "SG", "D0", "3F")
    db.insert_td_signal_event(2000, "2026-01-01T00:00:02Z", "EK", "SF", "D0", "BF")
    assert _scalar(db, "SELECT COUNT(*) FROM td_signal_bytes") == 1
    change = db._conn.execute(
        "SELECT id, bit, old_state, new_state FROM td_sclass_changes"
    ).fetchone()
    assert tuple(change[1:]) == (7, 0, 1)
    assert db.rebuild_td_signal_bytes() == {"scanned": 1, "inserted": 1}


def test_trust_vstp_and_cif_persistence(db):
    db.upsert_trust("123456", "2C90", "C12345", "88", "2026-01-17 12:30:00", "VICTRIC", 0, {"a": 1})
    db.upsert_trust("123456", "2C90", "C12345", "88", "2026-01-17 12:31:00", "CLPHMJC", 2, {"a": 2})
    assert _scalar(db, "SELECT last_location FROM trust_state") == "CLPHMJC"

    body = {"train_id": "123456", "actual_timestamp": "1700000000000", "event_type": "ARRIVAL"}
    db.insert_trust_message(body)
    db.insert_trust_message(body)  # duplicate ignored
    assert _scalar(db, "SELECT COUNT(*) FROM trust_messages") == 1

    vstp = {"VSTPCIFMsgV1": {
        "schedule": {"schedule_segment": [{
            "schedule_location": [
                {"location": {"tiploc": {"tiploc_id": "STRETHM"}}, "scheduled_departure_time": "000900"},
                {"location": {"tiploc": {"tiploc_id": "HORSHUS"}}, "scheduled_arrival_time": "014500"},
            ],
            "signalling_id": "8Y92",
        }]},
        "transaction_type": "Create", "train_status": "2",
        "schedule_start_date": "2026-02-16", "schedule_end_date": "2026-02-16",
        "CIF_train_uid": "W01880", "CIF_stp_indicator": "C",
    }}
    db.insert_vstp_schedule(vstp)
    db.insert_vstp_schedule(vstp)  # replaces, never duplicates
    assert _scalar(db, "SELECT COUNT(*) FROM vstp_schedules") == 1
    assert _scalar(db, "SELECT COUNT(*) FROM vstp_schedule_locations") == 2

    cif = {
        "CIF_train_uid": "C12345", "schedule_start_date": "2026-02-11", "schedule_end_date": "2026-12-31",
        "schedule_days_runs": "1111100", "CIF_stp_indicator": "P", "train_status": "P",
        "schedule_segment": {
            "signalling_id": "2C90",
            "schedule_location": [
                {"tiploc_code": "CLPHMJC", "scheduled_departure_time": "0830", "platform": "1"},
                {"tiploc_code": "VICTRIC", "scheduled_arrival_time": "0845", "platform": "2"},
            ],
        },
    }
    assert db.insert_cif_schedule(cif, "SE")
    assert db.insert_cif_schedule(cif, "SE")  # unchanged fingerprint -> skipped
    cif["schedule_segment"]["signalling_id"] = "2C91"
    assert db.insert_cif_schedule(cif, "SE")  # changed -> upsert in place
    assert _scalar(db, "SELECT COUNT(*) FROM cif_schedules") == 1
    assert _scalar(db, "SELECT cif_headcode FROM cif_schedules") == "2C91"
    assert _scalar(db, "SELECT COUNT(*) FROM cif_schedule_locations") == 2


def test_retention_purge_uses_postgres_safe_sql(db):
    old_ms = int(time.time() * 1000) - 10 * 24 * 3600 * 1000
    db.insert_trust_message({"train_id": "OLD", "actual_timestamp": str(old_ms), "event_type": "X"})
    db._conn.execute("UPDATE trust_messages SET created_at_ts=?", (old_ms,))
    db.retain_trust_days = 5
    deleted = db.purge_old_data()
    assert deleted["trust_messages"] == 1
    assert _scalar(db, "SELECT COUNT(*) FROM trust_messages") == 0


def test_reference_data_toc_and_upserts_are_idempotent(db):
    corpus = [
        {"TIPLOC": "CLPHMJC", "STANOX": "87701", "3ALPHA": "CLJ", "NLC": "1234", "NLCDESC": "CLAPHAM JUNCTION"},
        {"TIPLOC": "ONLYTIP", "NLCDESC": "TIPLOC ONLY"},  # NULL stanox/crs must be storable
    ]
    assert db.populate_corpus_data(corpus) == 2
    assert db.populate_corpus_data(corpus) == 2
    assert _scalar(db, "SELECT COUNT(*) FROM corpus_locations") == 2
    assert db.get_corpus_location(tiploc="ONLYTIP")["name"] == "TIPLOC ONLY"

    smart = [{"TD": "EK", "FROMBERTH": "0152", "TOBERTH": "0153", "STANOX": "87701", "BERTHOFFSET": "2"}]
    assert db.populate_smart_data(smart) == 2
    assert db.populate_smart_data(smart) == 2
    assert _scalar(db, "SELECT COUNT(*) FROM smart_berths") == 2

    db.upsert_toc("88", "Southeastern", business_code="SE", sector_code="74", atoc_code="SE")
    db.upsert_toc("88", "Southeastern Railway", business_code="SE", sector_code="74", atoc_code="SE")
    assert db.get_toc_name("88") == "Southeastern Railway"
    assert db.get_canonical_toc_code("74") == "88"

    db.upsert_toc_td_area("88", "EK", is_primary=True, confidence=0.9)
    db.upsert_toc_td_area("88", "EK", is_primary=False, confidence=0.5)
    areas = db.get_toc_td_areas()
    assert len(areas) == 1 and areas[0]["is_primary"] is False
    db.delete_toc_td_area("88", "EK")
    assert db.get_toc_td_areas() == []


def test_mapper_scores_physical_mappings_and_topology(pg_url):
    db = RailDB(pg_url, enable_mapper=True)
    try:
        db.update_mapper_config(500, 4000, 2000)
        assert db.get_mapper_config() == {"pre_ms": 500, "post_ms": 4000, "tau_ms": 2000}

        obs = ("EK", 1, 1000, "0152", "0153", "", 2, 1500, "D0", "01", 500, 1.0)
        assert db.insert_observation(obs) is True
        assert db.insert_observation(obs) is False  # ON CONFLICT DO NOTHING -> rowcount 0
        score = ("EK", "0152", "0153", "D0", 1.0, 1500, "2026-01-01T00:00:01Z", "01")
        db.insert_score(score)
        db.insert_score(score)
        row = db._conn.execute("SELECT score, obs_count FROM berth_signal_scores").fetchone()
        assert tuple(row) == (2.0, 2)

        mapping_id = db.add_physical_signal_mapping(
            "EK", "D0", 0, 0, physical_signal_number="SN1", from_berth="0152", to_berth="0153",
            mapping_confidence=0.9, verification_status="inferred",
        )
        assert isinstance(mapping_id, int)
        db.revoke_physical_signal_mapping(mapping_id, reviewer="me")
        assert db.get_physical_signal_mappings(td_area="EK")[0]["verification_status"] == "revoked"

        db._conn.execute(
            "INSERT INTO td_berth_movements(ts_ms, ts_iso, td_area, headcode, from_berth, to_berth, source_msg_type) "
            "VALUES (1000, '2026-01-01T00:00:01Z', 'EK', '2C90', '0152', '0153', 'CC')"
        )
        first = db.rebuild_signalling_topology("EK")
        second = db.rebuild_signalling_topology("EK")
        assert first["candidate_edges"] == second["candidate_edges"] > 0
    finally:
        db.close()


def test_smart_serial_csv_import_upserts(db, tmp_path):
    csv_file = tmp_path / "smart.csv"
    csv_file.write_text(
        "td_area,interlocking,byte_dec,byte_hex,bit,section,function_type,function,location_context\n"
        "EK,1,136,88,0,S1,signal,Signal 1 proved,ctx\n"
    )
    assert db.import_smart_serial_outputs(str(csv_file)) == 1
    assert db.import_smart_serial_outputs(str(csv_file)) == 1
    assert _scalar(db, "SELECT COUNT(*) FROM smart_serial_bit_map") == 1


def test_failed_statement_does_not_poison_the_shared_connection(db):
    with pytest.raises(Exception):
        db._conn.execute("SELECT * FROM table_that_does_not_exist")
    assert _scalar(db, "SELECT 1") == 1  # autocommit: no aborted transaction left behind
    with pytest.raises(RuntimeError):
        with db._conn:
            db._conn.execute("INSERT INTO toc_reference(toc_code, toc_name) VALUES ('ZZ', 'Rolled back')")
            raise RuntimeError("boom")
    assert _scalar(db, "SELECT COUNT(*) FROM toc_reference WHERE toc_code='ZZ'") == 0


def _web_client(pg_url):
    from flask import Flask
    from nrod_railhub import web

    holder = {}
    original_init = Flask.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        holder["app"] = self

    with mock.patch.object(Flask, "__init__", patched_init), mock.patch("flask.Flask.run"):
        web.start_web_dashboard(pg_url, 8088, None, None)
    return holder["app"].test_client()


def test_web_dashboard_pages_render_against_postgres(pg_url):
    seed = RailDB(pg_url, enable_mapper=False)
    try:
        seed.populate_corpus_data([
            {"TIPLOC": "FAVRSHM", "STANOX": "87710", "3ALPHA": "FAV", "NLCDESC": "FAVERSHAM"},
            {"TIPLOC": "RAMSGTE", "STANOX": "87750", "3ALPHA": "RAM", "NLCDESC": "RAMSGATE"},
        ])
        seed.upsert_toc("88", "Southeastern", business_code="SE", sector_code="74", atoc_code="SE")
        seed.upsert_td_state("EK", "2C90", 2000, "2026-01-01T00:00:02Z", "0153", "0154")
        seed.insert_td_berth_event(1000, "2026-01-01T00:00:01Z", "EK", "2C90", "CA", "0152", "0153")
        seed.insert_td_signal_event(1000, "2026-01-01T00:00:01Z", "EK", "SG", "D0", "3F")
        seed.insert_td_signal_event(2000, "2026-01-01T00:00:02Z", "EK", "SF", "D0", "BF")
        seed.insert_trust_message({"train_id": "123456", "actual_timestamp": "1700000000000",
                                   "event_type": "ARRIVAL", "toc_id": "88"})
        seed.upsert_trust("123456", "2C90", "C12345", "88", "2026-01-17 12:30:00", "VICTRIC", 0, {})
        seed.insert_cif_schedule({
            "CIF_train_uid": "C12345", "schedule_start_date": "2026-02-11", "CIF_stp_indicator": "P",
            "schedule_segment": {"signalling_id": "2C90", "schedule_location": [
                {"tiploc_code": "FAVRSHM", "scheduled_departure_time": "0830"},
                {"tiploc_code": "RAMSGTE", "scheduled_arrival_time": "0845"}]},
        }, "SE")
        seed.upsert_toc_td_area("88", "EK", source="manual")
        seed.add_physical_signal_mapping("EK", "D0", 0, 7, physical_signal_number="SN1")
        seed.rebuild_td_berth_movements()
    finally:
        seed.close()

    client = _web_client(pg_url)
    routes = [
        "/", "/?area=EK&hc=2C90", "/interesting", "/train?area=EK&hc=2C90", "/events", "/signals",
        "/raw-events", "/trust", "/trust?headcode=2C90", "/vstp", "/cif", "/cif?headcode=2c90", "/tocs",
        "/toc-td-areas", "/stats", "/api/stats/messages-per-second", "/api/stats/records-per-minute",
        "/signal-mappings", "/signal-mappings?from_berth=01*", "/signal-mappings?from_station=FAV&to_station=RAM",
        "/api/physical-signal-mappings", "/api/signalling-topology",
        "/td-decode-lab", "/td-decode-lab?view=berth&area=EK", "/settings",
    ]
    for route in routes:
        response = client.get(route)
        body = response.get_data(as_text=True)
        assert response.status_code == 200, f"{route}: {response.status_code}"
        assert "Traceback" not in body, f"{route} rendered a traceback"
        assert "does not exist" not in body and "psycopg" not in body, f"{route}: {body[:300]}"

    assert "2C90" in client.get("/").get_data(as_text=True)
    assert "Southeastern" in client.get("/tocs").get_data(as_text=True)
    assert "SN1" in client.get("/signal-mappings").get_data(as_text=True)


def test_reference_import_and_bootstrap_check_on_postgres(pg_url, tmp_path):
    import shutil
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
    try:
        from nrod_ref_import import run_imports
    finally:
        sys.path.pop(0)
    from nrod_railhub.ref_bootstrap import reference_data_present

    root = os.path.join(os.path.dirname(__file__), "..", "json")
    shutil.copy(os.path.join(root, "CORPUSExtract.json"), tmp_path / "CORPUS.json")
    shutil.copy(os.path.join(root, "SMARTExtract.json"), tmp_path / "SMART.json")

    assert not reference_data_present(pg_url)
    counts = []
    for _ in range(2):  # re-running must upsert, not fail or duplicate
        summary = run_imports(pg_url, ["CORPUS", "SMART"], "u", "p", str(tmp_path), download=False)
        counts.append(summary["datasets"]["CORPUS"])
    assert counts[0] == counts[1]
    assert reference_data_present(pg_url)

    conn = RailDB(pg_url, enable_mapper=False)
    try:
        assert _scalar(conn, "SELECT COUNT(*) FROM corpus_tiploc") <= counts[0]["TIPLOCDATA"]
        assert _scalar(conn, "SELECT COUNT(*) FROM v_berths") > 0
    finally:
        conn.close()

    from nrod_railhub.resolvers import SmartResolver
    resolver = SmartResolver(db_path=pg_url)
    assert resolver._lookup_inferred_berth("EK", "0152") is None  # runs the portable query
    resolver._db_conn.close()
