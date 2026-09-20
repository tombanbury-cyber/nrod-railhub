"""Tests for historical route inference in the visualization API."""

import sqlite3
import tempfile
from pathlib import Path

from app.visualisation.route_inference import (
    build_observed_chain,
    infer_train_chain,
    rebuild_route_patterns,
)


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


def test_build_observed_chain_pairs_raw_events_in_order():
    """Test raw enter/exit pairing remains unchanged for direct observations."""
    rows = [
        {"ts": "2026-02-14T10:00:00Z", "event_type": "berth_enter", "object_id": "BRTH_1"},
        {"ts": "2026-02-14T10:01:00Z", "event_type": "berth_exit", "object_id": "BRTH_1"},
        {"ts": "2026-02-14T10:01:01Z", "event_type": "berth_enter", "object_id": "BRTH_2"},
    ]

    chain = build_observed_chain(rows)

    assert chain == [
        {
            "berth_id": "BRTH_1",
            "enter_time": "2026-02-14T10:00:00Z",
            "exit_time": "2026-02-14T10:01:00Z",
            "inferred": False,
            "confidence": 1.0,
            "source": "observed",
            "reason": "Observed berth enter/exit events",
        },
        {
            "berth_id": "BRTH_2",
            "enter_time": "2026-02-14T10:01:01Z",
            "exit_time": None,
            "inferred": False,
            "confidence": 1.0,
            "source": "observed",
            "reason": "Observed berth enter event",
        },
    ]


def test_build_observed_chain_preserves_repeated_same_berth_entries():
    """Test repeated enters on the same berth do not overwrite the earlier segment."""
    rows = [
        {"ts": "2026-02-14T10:00:00Z", "event_type": "berth_enter", "object_id": "BRTH_1"},
        {"ts": "2026-02-14T10:00:30Z", "event_type": "berth_enter", "object_id": "BRTH_1"},
        {"ts": "2026-02-14T10:01:00Z", "event_type": "berth_exit", "object_id": "BRTH_1"},
    ]

    chain = build_observed_chain(rows)

    assert chain[0]["berth_id"] == "BRTH_1"
    assert chain[0]["enter_time"] == "2026-02-14T10:00:00Z"
    assert chain[0]["exit_time"] is None
    assert chain[1]["berth_id"] == "BRTH_1"
    assert chain[1]["enter_time"] == "2026-02-14T10:00:30Z"
    assert chain[1]["exit_time"] == "2026-02-14T10:01:00Z"


def test_rebuild_route_patterns_infers_non_sequential_route():
    """Test historical route patterns fill in missing non-sequential berths."""
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

        summary = rebuild_route_patterns(conn)
        chain = infer_train_chain(conn, "T2")

        assert summary["transition_rows"] == 2
        assert summary["pattern_rows"] == 1
        assert [item["berth_id"] for item in chain["chain"]] == ["BRTH_1", "BRTH_4", "BRTH_7"]
        assert chain["chain"][1]["inferred"] is True
        assert chain["chain"][1]["confidence"] > 0.5
    finally:
        conn.close()
        Path(db_path).unlink()


def test_infer_train_chain_falls_back_to_raw_order_without_history():
    """Test inference falls back to direct observed order when history is absent."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('T2', '2C90', 'Current Train', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T12:00:00Z', 'td', 'T2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T12:01:00Z', 'td', 'T2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T12:02:00Z', 'td', 'T2', 'berth_enter', 'BRTH_7', '{}');
            """
        )

        chain = infer_train_chain(conn, "T2")

        assert [item["berth_id"] for item in chain["chain"]] == ["BRTH_1", "BRTH_7"]
        assert all(item["inferred"] is False for item in chain["chain"])
    finally:
        conn.close()
        Path(db_path).unlink()


def test_infer_train_chain_does_not_duplicate_observed_intermediate_berths():
    """Test observed intermediate berths are not duplicated by inferred items."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('H1', '2C90', 'Historic One', 'GW'),
                ('T2', '2C90', 'Current Train', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T10:00:00Z', 'td', 'H1', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T10:01:00Z', 'td', 'H1', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T10:01:01Z', 'td', 'H1', 'berth_enter', 'BRTH_4', '{}'),
                ('2026-02-14T10:02:00Z', 'td', 'H1', 'berth_exit', 'BRTH_4', '{}'),
                ('2026-02-14T10:02:01Z', 'td', 'H1', 'berth_enter', 'BRTH_7', '{}'),
                ('2026-02-14T12:00:00Z', 'td', 'T2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T12:01:00Z', 'td', 'T2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T12:01:01Z', 'td', 'T2', 'berth_enter', 'BRTH_4', '{}'),
                ('2026-02-14T12:02:00Z', 'td', 'T2', 'berth_exit', 'BRTH_4', '{}'),
                ('2026-02-14T12:02:01Z', 'td', 'T2', 'berth_enter', 'BRTH_7', '{}');
            """
        )

        rebuild_route_patterns(conn)
        chain = infer_train_chain(conn, "T2")

        assert [item["berth_id"] for item in chain["chain"]] == ["BRTH_1", "BRTH_4", "BRTH_7"]
        assert sum(1 for item in chain["chain"] if item["berth_id"] == "BRTH_4") == 1
        assert all(item["inferred"] is False for item in chain["chain"])
    finally:
        conn.close()
        Path(db_path).unlink()


def test_rebuild_route_patterns_splits_shared_train_history_by_payload_headcode():
    """Test rebuild attributes transitions to payload headcodes when train IDs are reused."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('SHARED', '2C90', 'Shared Record', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T10:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:01Z', 'td', 'SHARED', 'berth_enter', 'BRTH_2', '{"headcode":"2C90"}'),
                ('2026-02-14T10:02:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_2', '{"headcode":"2C90"}'),
                ('2026-02-14T11:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T11:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T11:01:01Z', 'td', 'SHARED', 'berth_enter', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T11:02:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_6', '{"headcode":"1A01"}');
            """
        )

        rebuild_route_patterns(conn)
        rows = conn.execute(
            """
            SELECT headcode, from_berth, to_berth, transition_count
            FROM berth_transition_counts
            ORDER BY headcode, from_berth, to_berth
            """
        ).fetchall()

        assert rows == [
            ("1A01", "BRTH_8", "BRTH_6", 1),
            ("2C90", "BRTH_1", "BRTH_2", 1),
        ]
    finally:
        conn.close()
        Path(db_path).unlink()


def test_infer_train_chain_uses_payload_headcode_for_reused_train_ids():
    """Test inference uses the active event payload headcode for reused train records."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('HNEW', '1A01', 'Historic New Route', 'GW'),
                ('HNEW2', '1A01', 'Historic New Route Two', 'GW'),
                ('SHARED', '2C90', 'Shared Record', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T09:00:00Z', 'td', 'HNEW', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:01:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:01:01Z', 'td', 'HNEW', 'berth_enter', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:02:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:02:01Z', 'td', 'HNEW', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:03:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:30:00Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:31:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:31:01Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:32:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:32:01Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:33:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T10:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:01Z', 'td', 'SHARED', 'berth_enter', 'BRTH_2', '{"headcode":"2C90"}'),
                ('2026-02-14T11:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T11:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T11:02:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}');
            """
        )

        rebuild_route_patterns(conn)
        chain = infer_train_chain(conn, "SHARED")

        assert [item["berth_id"] for item in chain["chain"]] == ["BRTH_8", "BRTH_6", "BRTH_4"]
        assert chain["chain"][1]["inferred"] is True
        assert chain["chain"][1]["source"] == "historical_route_pattern"
    finally:
        conn.close()
        Path(db_path).unlink()


def test_infer_train_chain_keeps_untagged_active_events_with_payload_headcodes():
    """Test active-chain filtering keeps untagged events while excluding other headcodes."""
    db_path = _create_visualisation_db()
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            INSERT INTO train (id, headcode, description, toc) VALUES
                ('HNEW', '1A01', 'Historic New Route', 'GW'),
                ('HNEW2', '1A01', 'Historic New Route Two', 'GW'),
                ('SHARED', '2C90', 'Shared Record', 'GW');

            INSERT INTO event (ts, source, train_id, event_type, object_id, payload) VALUES
                ('2026-02-14T09:00:00Z', 'td', 'HNEW', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:01:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:01:01Z', 'td', 'HNEW', 'berth_enter', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:02:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:02:01Z', 'td', 'HNEW', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:03:00Z', 'td', 'HNEW', 'berth_exit', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:30:00Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:31:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T09:31:01Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:32:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_6', '{"headcode":"1A01"}'),
                ('2026-02-14T09:32:01Z', 'td', 'HNEW2', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T09:33:00Z', 'td', 'HNEW2', 'berth_exit', 'BRTH_4', '{"headcode":"1A01"}'),
                ('2026-02-14T10:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_1', '{"headcode":"2C90"}'),
                ('2026-02-14T10:01:01Z', 'td', 'SHARED', 'berth_enter', 'BRTH_2', '{"headcode":"2C90"}'),
                ('2026-02-14T11:00:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_8', '{"headcode":"1A01"}'),
                ('2026-02-14T11:01:00Z', 'td', 'SHARED', 'berth_exit', 'BRTH_8', '{}'),
                ('2026-02-14T11:02:00Z', 'td', 'SHARED', 'berth_enter', 'BRTH_4', '{"headcode":"1A01"}');
            """
        )

        rebuild_route_patterns(conn)
        chain = infer_train_chain(conn, "SHARED")

        assert [item["berth_id"] for item in chain["chain"]] == ["BRTH_8", "BRTH_6", "BRTH_4"]
        assert chain["chain"][0]["exit_time"] == "2026-02-14T11:01:00Z"
        assert chain["chain"][1]["inferred"] is True
    finally:
        conn.close()
        Path(db_path).unlink()
