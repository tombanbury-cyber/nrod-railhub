#!/usr/bin/env python3
"""Unit tests for the standalone TD-only listener (td_listener.py)."""

import json
import sqlite3
import tempfile
from unittest.mock import Mock

import td_listener


def _make_frame(payload):
    frame = Mock()
    frame.body = json.dumps(payload)
    frame.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}
    return frame


def test_unwrap_td_item_handles_wrapped_and_unwrapped():
    """_MSG-wrapped and already-unwrapped TD items should both unwrap correctly."""
    wrapped = {"CA_MSG": {"msg_type": "CA", "area_id": "EK"}}
    assert td_listener.unwrap_td_item(wrapped) == {"msg_type": "CA", "area_id": "EK"}

    unwrapped = {"msg_type": "CA", "area_id": "EK"}
    assert td_listener.unwrap_td_item(unwrapped) == unwrapped

    assert td_listener.unwrap_td_item({"unrelated": "value"}) is None
    assert td_listener.unwrap_td_item("not-a-dict") is None


def test_td_event_db_creates_schema_and_indexes():
    """TdEventDB should create the td_events table and expected indexes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    try:
        cur = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='td_events'"
        )
        assert cur.fetchone() is not None

        idx_names = {
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='td_events'"
            ).fetchall()
        }
        assert "idx_td_events_ts" in idx_names
        assert "idx_td_events_area_ts" in idx_names
        assert "idx_td_events_area_headcode_ts" in idx_names
    finally:
        db.close()


def test_td_event_db_creates_domain_tables_and_indexes():
    """TdEventDB should also create td_berth_events and td_signal_events with indexes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    try:
        for table, indexes in (
            ("td_berth_events", {"idx_td_berth_ts", "idx_td_berth_area_hc_ts"}),
            ("td_signal_events", {"idx_td_signal_ts", "idx_td_signal_area_ts"}),
        ):
            cur = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            assert cur.fetchone() is not None

            idx_names = {
                row[0]
                for row in db._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                    (table,),
                ).fetchall()
            }
            assert indexes <= idx_names
    finally:
        db.close()


def test_berth_event_is_parsed_and_persisted():
    """A CA berth-stepping message should be persisted with the right fields."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db)

    payload = [
        {
            "CA_MSG": {
                "msg_type": "CA",
                "area_id": "EK",
                "time": "1700000000000",
                "from": "0001",
                "to": "0002",
                "descr": "2C90",
            }
        }
    ]
    listener.on_message(_make_frame(payload))

    rows = db._conn.execute(
        "SELECT area, msg_type, headcode, from_berth, to_berth FROM td_events"
    ).fetchall()
    assert rows == [("EK", "CA", "2C90", "0001", "0002")]

    berth_rows = db._conn.execute(
        "SELECT td_area, msg_type, headcode, from_berth, to_berth FROM td_berth_events"
    ).fetchall()
    assert berth_rows == [("EK", "CA", "2C90", "0001", "0002")]
    db.close()


def test_signal_event_is_parsed_and_persisted():
    """An SF signal (S-class) message should be persisted with address/data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db)

    payload = [
        {
            "SF_MSG": {
                "msg_type": "SF",
                "area_id": "EK",
                "time": "1700000000000",
                "address": "01",
                "data": "AA",
            }
        }
    ]
    listener.on_message(_make_frame(payload))

    rows = db._conn.execute(
        "SELECT area, msg_type, address, data FROM td_events"
    ).fetchall()
    assert rows == [("EK", "SF", "01", "AA")]

    signal_rows = db._conn.execute(
        "SELECT td_area, msg_type, address, data FROM td_signal_events"
    ).fetchall()
    assert signal_rows == [("EK", "SF", "01", "AA")]
    db.close()


def test_sg_message_is_expanded_into_signal_bytes():
    """An SG message should be expanded into per-byte rows in td_signal_bytes."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db)

    payload = [
        {
            "SG_MSG": {
                "msg_type": "SG",
                "area_id": "EK",
                "time": "1700000000000",
                "address": "88",
                "data": "41689559",
            }
        }
    ]
    listener.on_message(_make_frame(payload))

    raw_message_id = db._conn.execute(
        "SELECT id FROM td_signal_events WHERE address='88'"
    ).fetchone()[0]

    byte_rows = db._conn.execute(
        "SELECT area_id, address_int, value_int, address, value, source_type, raw_message_id "
        "FROM td_signal_bytes ORDER BY address_int"
    ).fetchall()
    assert byte_rows == [
        ("EK", 0x88, 0x41, "88", "41", "SG", raw_message_id),
        ("EK", 0x89, 0x68, "89", "68", "SG", raw_message_id),
        ("EK", 0x8A, 0x95, "8A", "95", "SG", raw_message_id),
        ("EK", 0x8B, 0x59, "8B", "59", "SG", raw_message_id),
    ]
    db.close()


def test_rebuild_signal_bytes_backfills_from_existing_events():
    """rebuild_signal_bytes() should backfill td_signal_bytes from td_signal_events."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    db.insert_signal_event(1000, "2024-01-01T00:00:01.000Z", "EK", "SH", "04", "35000000")

    db._conn.execute("DELETE FROM td_signal_bytes")

    result = db.rebuild_signal_bytes()
    assert result == {"scanned": 1, "inserted": 4}

    byte_rows = db._conn.execute(
        "SELECT area_id, address_int, value_int, address, value, source_type "
        "FROM td_signal_bytes ORDER BY address_int"
    ).fetchall()
    assert byte_rows == [
        ("EK", 0x04, 0x35, "04", "35", "SH"),
        ("EK", 0x05, 0x00, "05", "00", "SH"),
        ("EK", 0x06, 0x00, "06", "00", "SH"),
        ("EK", 0x07, 0x00, "07", "00", "SH"),
    ]
    db.close()


def test_td_area_filter_excludes_other_areas():
    """Messages outside the configured td_area filter should be skipped."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db, td_area=["EK"])

    payload = [
        {"CA_MSG": {"msg_type": "CA", "area_id": "EK", "descr": "2C90", "from": "1", "to": "2"}},
        {"CA_MSG": {"msg_type": "CA", "area_id": "XX", "descr": "2C91", "from": "1", "to": "2"}},
    ]
    listener.on_message(_make_frame(payload))

    rows = db._conn.execute("SELECT area FROM td_events").fetchall()
    assert rows == [("EK",)]
    db.close()


def test_malformed_and_unrelated_messages_are_ignored():
    """Malformed/unrelated messages must not crash the listener or be persisted."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db)

    payload = [
        {"msg_type": "CA"},  # missing area_id/descr
        {"VSTPCIFMsgV1": {"foo": "bar"}},  # unrelated feed, no msg_type wrapper
        "not-a-dict",
        {"unrelated_key": "value"},
    ]
    listener.on_message(_make_frame(payload))

    rows = db._conn.execute("SELECT * FROM td_events").fetchall()
    assert rows == []
    berth_rows = db._conn.execute("SELECT * FROM td_berth_events").fetchall()
    assert berth_rows == []
    signal_rows = db._conn.execute("SELECT * FROM td_signal_events").fetchall()
    assert signal_rows == []
    db.close()

    # Non-JSON body should also be handled gracefully.
    bad_frame = Mock()
    bad_frame.body = "not json {"
    listener.on_message(bad_frame)  # should not raise


def test_unknown_msg_type_is_skipped_without_crashing():
    """Unrecognised TD msg_type values should be skipped, logged, and not crash the listener."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    db = td_listener.TdEventDB(db_path)
    listener = td_listener.TdListener(db, verbose=True)

    payload = [
        {"XX_MSG": {"msg_type": "XX", "area_id": "EK", "descr": "2C90"}},
    ]
    listener.on_message(_make_frame(payload))

    assert db._conn.execute("SELECT * FROM td_events").fetchall() == []
    assert db._conn.execute("SELECT * FROM td_berth_events").fetchall() == []
    assert db._conn.execute("SELECT * FROM td_signal_events").fetchall() == []
    db.close()


def test_parse_args_loads_yaml_config(tmp_path):
    """parse_args should merge values from a YAML config file, same format as nrod_railhub.py."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "user: cfguser@example.com\n"
        "password: cfgpass\n"
        "td_area:\n"
        "  - EK\n"
        f"db_path: {tmp_path / 'events.db'}\n"
    )

    args = td_listener.parse_args(["--config", str(config_file)])

    assert args.user == "cfguser@example.com"
    assert args.password == "cfgpass"
    assert args.td_area == ["EK"]
    assert args.db_path == str(tmp_path / "events.db")


def test_parse_args_cli_overrides_and_normalizes_td_area():
    """Comma-separated and repeated --td-area values should normalize to a clean list."""
    args = td_listener.parse_args(
        ["--user", "u", "--password", "p", "--td-area", "EK,WR", "--db-path", "x.db"]
    )
    assert args.td_area == ["EK", "WR"]
