#!/usr/bin/env python3
"""Tests for normalized C-Class berth movement extraction."""

import os
import tempfile

from nrod_railhub.database import RailDB


def _fetch_movements(db: RailDB) -> list[tuple]:
    with db._conn:
        return db._conn.execute(
            """
            SELECT source_event_id, ts_ms, td_area, headcode, from_berth, to_berth, source_msg_type
            FROM td_berth_movements
            ORDER BY ts_ms, id
            """
        ).fetchall()


def test_td_berth_movements_extract_from_ca_cc_sequence():
    """CA then CC with same train should yield one normalized berth transition."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CA", "0152", "", "2C90")
        db.insert_td_berth_event(1100, "2024-01-01T00:00:01.100Z", "EK", "2C90", "CC", "", "0153", "2C90")

        rows = _fetch_movements(db)
        assert len(rows) == 1
        assert rows[0][1:] == (1100, "EK", "2C90", "0152", "0153", "CC")

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_td_berth_movements_ignore_missing_and_reset_events():
    """Missing data and reset markers should not produce synthetic movements."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)

        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CA", "0152", "", "2C90")
        db.insert_td_berth_event(1100, "2024-01-01T00:00:01.100Z", "EK", "2C90", "CC", "", "0153", "2C90")
        db.insert_td_berth_event(1200, "2024-01-01T00:00:01.200Z", "EK", "2C90", "CB", "0000", "0000", "2C90")
        db.insert_td_berth_event(1300, "2024-01-01T00:00:01.300Z", "EK", "2C90", "CC", "", "0154", "2C90")
        db.insert_td_berth_event(1400, "2024-01-01T00:00:01.400Z", "EK", "", "CC", "", "0155", "")

        rows = _fetch_movements(db)
        assert len(rows) == 1
        assert rows[0][1:] == (1100, "EK", "2C90", "0152", "0153", "CC")

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_td_berth_movements_dedupe_and_handle_out_of_order_safely():
    """Repeated and out-of-order rows should not create extra inferred transitions."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)

        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CB", "0100", "0101", "2C90")
        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CB", "0100", "0101", "2C90")
        db.insert_td_berth_event(900, "2024-01-01T00:00:00.900Z", "EK", "2C90", "CC", "", "0102", "2C90")
        db.insert_td_berth_event(900, "2024-01-01T00:00:00.900Z", "EK", "2C90", "CB", "0099", "0100", "2C90")

        rows = _fetch_movements(db)
        assert len(rows) == 1
        assert rows[0][1:] == (1000, "EK", "2C90", "0100", "0101", "CB")

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_td_berth_movement_rebuild_backfills_historical_rows():
    """Historical td_berth_events can be deterministically backfilled into movements."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CA", "0152", "", "2C90")
        db.insert_td_berth_event(1100, "2024-01-01T00:00:01.100Z", "EK", "2C90", "CC", "", "0153", "2C90")
        db.insert_td_berth_event(1200, "2024-01-01T00:00:01.200Z", "WK", "1A01", "CB", "0200", "0201", "1A01")

        first = db.rebuild_td_berth_movements()
        rows_after_first = _fetch_movements(db)
        second = db.rebuild_td_berth_movements()
        rows_after_second = _fetch_movements(db)

        assert first["scanned"] == 3
        assert first["inserted"] == 2
        assert second["scanned"] == 3
        assert second["inserted"] == 2
        assert rows_after_first == rows_after_second

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_td_berth_movement_rebuild_can_target_single_area():
    """Area-scoped rebuild should only refresh that area and keep others intact."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        db.insert_td_berth_event(1000, "2024-01-01T00:00:01.000Z", "EK", "2C90", "CA", "0152", "", "2C90")
        db.insert_td_berth_event(1100, "2024-01-01T00:00:01.100Z", "EK", "2C90", "CC", "", "0153", "2C90")
        db.insert_td_berth_event(1200, "2024-01-01T00:00:01.200Z", "WK", "1A01", "CB", "0200", "0201", "1A01")

        db.rebuild_td_berth_movements()
        scoped = db.rebuild_td_berth_movements(td_area="EK")
        rows = _fetch_movements(db)

        assert scoped["scanned"] == 2
        assert scoped["inserted"] == 1
        assert len(rows) == 2
        assert rows[0][2] == "EK"
        assert rows[1][2] == "WK"

        db.insert_td_berth_event(1300, "2024-01-01T00:00:01.300Z", "EK", "2C90", "CC", "", "0154", "2C90")
        rows = _fetch_movements(db)
        assert len(rows) == 3
        assert rows[2][1:] == (1300, "EK", "2C90", "0153", "0154", "CC")

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
