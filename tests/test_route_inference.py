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
                ('2026-02-14T11:00:00Z', 'td', 'H2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T11:01:00Z', 'td', 'H2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T11:01:01Z', 'td', 'H2', 'berth_enter', 'BRTH_4', '{}'),
                ('2026-02-14T11:02:00Z', 'td', 'H2', 'berth_exit', 'BRTH_4', '{}'),
                ('2026-02-14T11:02:01Z', 'td', 'H2', 'berth_enter', 'BRTH_7', '{}'),
                ('2026-02-14T12:00:00Z', 'td', 'T2', 'berth_enter', 'BRTH_1', '{}'),
                ('2026-02-14T12:01:00Z', 'td', 'T2', 'berth_exit', 'BRTH_1', '{}'),
                ('2026-02-14T12:02:00Z', 'td', 'T2', 'berth_enter', 'BRTH_7', '{}');
            """
        )

        summary = rebuild_route_patterns(conn)
        chain = infer_train_chain(conn, "T2")

        assert summary["transition_rows"] == 3
        assert summary["pattern_rows"] == 2
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
