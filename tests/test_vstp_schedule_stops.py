#!/usr/bin/env python3
"""Comprehensive tests for VSTP schedule/stops parsing and persistence (issue #46)."""

import sqlite3
import tempfile
import os

from nrod_railhub.views import HumanView
from nrod_railhub.database import RailDB
from nrod_railhub.models import hhmmss_to_hhmm
from nrod_railhub.resolvers import LocationResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hv() -> HumanView:
    return HumanView(resolver=LocationResolver())


def _real_vstp_msg(
    uid: str = "W01880",
    start_date: str = "2026-02-16",
    end_date: str = "2026-02-16",
    signalling_id: str = "8Y92",
    locations=None,
    *,
    fields_inside_schedule: bool = False,
):
    """
    Build a VSTP message that mirrors the real Network Rail format.

    When ``fields_inside_schedule`` is True the uid/dates are placed inside
    the ``schedule`` sub-dict (as described in the issue spec) rather than at
    the ``VSTPCIFMsgV1`` root level.
    """
    if locations is None:
        locations = [
            {
                "location": {"tiploc": {"tiploc_id": "STRETHM"}},
                "scheduled_departure_time": "000900",
                "scheduled_arrival_time": " ",
                "CIF_activity": "TB",
            },
            {
                "location": {"tiploc": {"tiploc_id": "HORSHUS"}},
                "scheduled_departure_time": " ",
                "scheduled_arrival_time": "014500",
                "CIF_activity": "TF",
            },
        ]

    schedule_segment = [
        {
            "signalling_id": signalling_id,
            "atoc_code": "ZZ",
            "CIF_train_category": "DD",
            "schedule_location": locations,
        }
    ]

    if fields_inside_schedule:
        schedule = {
            "CIF_train_uid": uid,
            "schedule_start_date": start_date,
            "schedule_end_date": end_date,
            "schedule_days_runs": "1000000",
            "applicable_timetable": "Y",
            "CIF_stp_indicator": "C",
            "schedule_segment": schedule_segment,
        }
        root_extra: dict = {}
    else:
        schedule = {"schedule_segment": schedule_segment}
        root_extra = {
            "CIF_train_uid": uid,
            "schedule_start_date": start_date,
            "schedule_end_date": end_date,
            "schedule_days_runs": "1000000",
            "applicable_timetable": "Y",
            "CIF_stp_indicator": "C",
        }

    return {
        "VSTPCIFMsgV1": {
            "schedule": schedule,
            "transaction_type": "Create",
            "train_status": "2",
            **root_extra,
        }
    }


# ---------------------------------------------------------------------------
# hhmmss_to_hhmm
# ---------------------------------------------------------------------------

class TestHhmmssToHhmm:
    """Verify the time helper converts correctly and handles edge cases."""

    def test_six_digit_time(self):
        assert hhmmss_to_hhmm("000900") == "00:09"

    def test_six_digit_time_afternoon(self):
        assert hhmmss_to_hhmm("145500") == "14:55"

    def test_blank_space(self):
        assert hhmmss_to_hhmm(" ") == ""

    def test_empty_string(self):
        assert hhmmss_to_hhmm("") == ""

    def test_multiple_spaces(self):
        assert hhmmss_to_hhmm("      ") == ""

    def test_four_digit_time(self):
        assert hhmmss_to_hhmm("1430") == "14:30"


# ---------------------------------------------------------------------------
# upsert_vstp – fields at VSTPCIFMsgV1 root (real format)
# ---------------------------------------------------------------------------

class TestUpsertVstpRootFields:
    """Test that uid/dates at the VSTPCIFMsgV1 root level are correctly read."""

    def test_uid_extracted_from_root(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs is not None
        assert vs.uid == "W01880"

    def test_start_date_extracted_from_root(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs is not None
        assert vs.start_date == "2026-02-16"

    def test_end_date_extracted_from_root(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs is not None
        assert vs.end_date == "2026-02-16"

    def test_signalling_id_from_segment(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs is not None
        assert vs.signalling_id == "8Y92"


# ---------------------------------------------------------------------------
# upsert_vstp – fields inside "schedule" sub-dict (alternate real format)
# ---------------------------------------------------------------------------

class TestUpsertVstpScheduleSubdictFields:
    """Test that uid/dates inside the schedule sub-dict are also handled."""

    def test_uid_extracted_from_schedule_subdict(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg(fields_inside_schedule=True))
        assert vs is not None
        assert vs.uid == "W01880"

    def test_start_date_from_schedule_subdict(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg(fields_inside_schedule=True))
        assert vs is not None
        assert vs.start_date == "2026-02-16"


# ---------------------------------------------------------------------------
# upsert_vstp – schedule_segment as dict (not a list)
# ---------------------------------------------------------------------------

class TestUpsertVstpSegmentAsDict:
    """schedule_segment can be a dict rather than a list; both must work."""

    def _msg_with_dict_segment(self) -> dict:
        return {
            "VSTPCIFMsgV1": {
                "schedule": {
                    "schedule_segment": {
                        "signalling_id": "5A01",
                        "schedule_location": [
                            {
                                "location": {"tiploc": {"tiploc_id": "VICTRIC"}},
                                "scheduled_departure_time": "080000",
                                "scheduled_arrival_time": " ",
                            }
                        ],
                    }
                },
                "CIF_train_uid": "X99999",
                "schedule_start_date": "2026-03-01",
                "schedule_end_date": "2026-03-01",
            }
        }

    def test_dict_segment_parsed(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(self._msg_with_dict_segment())
        assert vs is not None
        assert vs.signalling_id == "5A01"
        assert vs.uid == "X99999"

    def test_dict_segment_locations_populated(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(self._msg_with_dict_segment())
        assert vs is not None
        assert len(vs.locations) == 1
        assert vs.locations[0][0] == "VICTRIC"


# ---------------------------------------------------------------------------
# upsert_vstp – locations list
# ---------------------------------------------------------------------------

class TestUpsertVstpLocations:
    """Verify locations are populated correctly."""

    def test_two_stops_parsed(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs is not None
        assert len(vs.locations) == 2

    def test_first_stop_tiploc(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs.locations[0][0] == "STRETHM"

    def test_first_stop_departure_time(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        # dep is index 2 in (tiploc, arr, dep)
        assert vs.locations[0][2] == "00:09"

    def test_first_stop_blank_arrival(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs.locations[0][1] == ""

    def test_last_stop_tiploc(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs.locations[1][0] == "HORSHUS"

    def test_last_stop_arrival_time(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert vs.locations[1][1] == "01:45"

    def test_blank_tiploc_excluded(self):
        """Stops with empty TIPLOC must not be added to the locations list."""
        locs = [
            {
                "location": {"tiploc": {"tiploc_id": "  "}},
                "scheduled_departure_time": "100000",
                "scheduled_arrival_time": " ",
            },
            {
                "location": {"tiploc": {"tiploc_id": "PADTON"}},
                "scheduled_departure_time": "110000",
                "scheduled_arrival_time": " ",
            },
        ]
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg(locations=locs))
        assert vs is not None
        assert len(vs.locations) == 1
        assert vs.locations[0][0] == "PADTON"


# ---------------------------------------------------------------------------
# upsert_vstp – in-memory indices
# ---------------------------------------------------------------------------

class TestUpsertVstpIndices:
    """Verify in-memory indices are populated after upsert_vstp."""

    def test_vstp_by_uid_date_indexed(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert ("W01880", "2026-02-16") in hv.vstp_by_uid_date

    def test_vstp_by_headcode_indexed(self):
        hv = _make_hv()
        vs = hv.upsert_vstp(_real_vstp_msg())
        assert "8Y92" in hv.vstp_by_headcode
        assert vs in hv.vstp_by_headcode["8Y92"]

    def test_schedules_by_tiploc_indexed(self):
        hv = _make_hv()
        hv.upsert_vstp(_real_vstp_msg())
        assert "STRETHM" in hv.schedules_by_tiploc
        assert "HORSHUS" in hv.schedules_by_tiploc

    def test_headcode_by_uid_indexed(self):
        hv = _make_hv()
        hv.upsert_vstp(_real_vstp_msg())
        assert hv.headcode_by_uid.get("W01880") == "8Y92"


# ---------------------------------------------------------------------------
# insert_vstp_schedule – DB persistence
# ---------------------------------------------------------------------------

class TestInsertVstpScheduleDb:
    """Verify insert_vstp_schedule persists correct rows to the database."""

    def _db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return RailDB(tmp.name, enable_mapper=False), tmp.name

    def _query(self, db_path: str, sql: str, params=()):
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    def test_schedule_header_persisted(self):
        db, path = self._db()
        try:
            db.insert_vstp_schedule(_real_vstp_msg())
            rows = self._query(
                path,
                "SELECT uid, schedule_start_date, signalling_id FROM vstp_schedules WHERE uid=?",
                ("W01880",),
            )
            assert len(rows) == 1
            assert rows[0][0] == "W01880"
            assert rows[0][1] == "2026-02-16"
            assert rows[0][2] == "8Y92"
        finally:
            db.close()
            os.unlink(path)

    def test_location_rows_persisted(self):
        db, path = self._db()
        try:
            db.insert_vstp_schedule(_real_vstp_msg())
            rows = self._query(
                path,
                "SELECT tiploc, scheduled_departure_time, scheduled_arrival_time "
                "FROM vstp_schedule_locations WHERE uid=? ORDER BY location_index",
                ("W01880",),
            )
            assert len(rows) == 2
            assert rows[0][0] == "STRETHM"
            assert rows[0][1] == "000900"
            assert rows[1][0] == "HORSHUS"
            assert rows[1][2] == "014500"
        finally:
            db.close()
            os.unlink(path)

    def test_fields_inside_schedule_subdict_persisted(self):
        """When uid/dates are inside the schedule sub-dict they must still be stored."""
        db, path = self._db()
        try:
            db.insert_vstp_schedule(_real_vstp_msg(fields_inside_schedule=True))
            rows = self._query(
                path,
                "SELECT uid, schedule_start_date FROM vstp_schedules WHERE uid=?",
                ("W01880",),
            )
            assert len(rows) == 1
            assert rows[0][0] == "W01880"
            assert rows[0][1] == "2026-02-16"
        finally:
            db.close()
            os.unlink(path)

    def test_no_uid_does_not_crash(self):
        """Messages with no uid should be silently skipped, not raise."""
        db, path = self._db()
        try:
            msg = {
                "VSTPCIFMsgV1": {
                    "schedule": {
                        "schedule_segment": [{"signalling_id": "1A01", "schedule_location": []}]
                    },
                    "transaction_type": "Create",
                }
            }
            db.insert_vstp_schedule(msg)  # Must not raise
        finally:
            db.close()
            os.unlink(path)


# ---------------------------------------------------------------------------
# upsert_vstp (DB method) – vstp_state table
# ---------------------------------------------------------------------------

class TestDbUpsertVstp:
    """Verify the DB upsert_vstp method populates vstp_state correctly."""

    def _db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return RailDB(tmp.name, enable_mapper=False), tmp.name

    def _query(self, db_path: str, sql: str, params=()):
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    def test_vstp_state_row_created(self):
        db, path = self._db()
        try:
            db.upsert_vstp("W01880", "8Y92", "2026-02-16", "2026-02-16", {})
            rows = self._query(
                path,
                "SELECT uid, headcode, start_date, end_date FROM vstp_state WHERE uid=?",
                ("W01880",),
            )
            assert len(rows) == 1
            assert rows[0] == ("W01880", "8Y92", "2026-02-16", "2026-02-16")
        finally:
            db.close()
            os.unlink(path)

    def test_vstp_state_row_upserted_on_repeat(self):
        db, path = self._db()
        try:
            db.upsert_vstp("W01880", "8Y92", "2026-02-16", "2026-02-16", {})
            db.upsert_vstp("W01880", "9Z99", "2026-02-16", "2026-02-17", {})
            rows = self._query(
                path,
                "SELECT headcode, end_date FROM vstp_state WHERE uid=?",
                ("W01880",),
            )
            assert len(rows) == 1
            assert rows[0][0] == "9Z99"
            assert rows[0][1] == "2026-02-17"
        finally:
            db.close()
            os.unlink(path)

    def test_vstp_state_skipped_without_uid(self):
        db, path = self._db()
        try:
            db.upsert_vstp("", "8Y92", "2026-02-16", "2026-02-16", {})
            rows = self._query(path, "SELECT COUNT(*) FROM vstp_state")
            assert rows[0][0] == 0
        finally:
            db.close()
            os.unlink(path)
