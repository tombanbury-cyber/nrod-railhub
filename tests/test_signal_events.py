#!/usr/bin/env python3
"""Test signal event capture."""

import argparse
import json
import tempfile
import os

from nrod_railhub.listener import Listener
from nrod_railhub.views import HumanView
from nrod_railhub.database import RailDB


def test_signal_event_capture():
    """Test that signal events (SF, SG, SH) are captured in the database."""
    
    # Create temporary database
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Setup
        db = RailDB(db_path, enable_mapper=False)
        hv = HumanView()
        args = argparse.Namespace(
            verbose=False,
            width=96,
            headcode=None,
            uid=None,
            td_area=None,
            trace_headcode=False,
            only_changes=True,
            repeat_after=300
        )
        listener = Listener(hv, args, db)
        
        # Create a mock STOMP frame with a signal event (SF_MSG)
        class MockFrame:
            def __init__(self, body):
                self.body = body
                self.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}
        
        # Signal event message - SF (Signal Fail), SG (Signal Pass), or SH (Signal Hold)
        signal_payload = [
            {
                "SF_MSG": {
                    "msg_type": "SF",
                    "area_id": "EK",
                    "address": "SN123",
                    "data": "04",
                    "time": "1675354321000"
                }
            }
        ]
        
        frame = MockFrame(json.dumps(signal_payload))
        
        # Process the message
        listener.on_message(frame)
        
        # Verify signal event was inserted
        with db._conn:
            cursor = db._conn.execute(
                "SELECT COUNT(*) FROM td_signal_events WHERE msg_type='SF' AND address='SN123'"
            )
            count = cursor.fetchone()[0]
        
        assert count == 1, f"Expected 1 signal event, but found {count}"
        
        # Verify the details
        with db._conn:
            cursor = db._conn.execute(
                "SELECT td_area, msg_type, address, data FROM td_signal_events WHERE address='SN123'"
            )
            row = cursor.fetchone()
        
        assert row is not None, "Signal event not found in database"
        assert row[0] == "EK", f"Expected area 'EK', got {row[0]}"
        assert row[1] == "SF", f"Expected msg_type 'SF', got {row[1]}"
        assert row[2] == "SN123", f"Expected address 'SN123', got {row[2]}"
        assert row[3] == "04", f"Expected data '04', got {row[3]}"
        
        print("✓ Signal event capture test passed")
        
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_berth_events_still_work():
    """Verify that berth events (CA, CB, CC) still work after the fix."""
    
    # Create temporary database
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Setup
        db = RailDB(db_path, enable_mapper=False)
        hv = HumanView()
        args = argparse.Namespace(
            verbose=False,
            width=96,
            headcode=None,
            uid=None,
            td_area=None,
            trace_headcode=False,
            only_changes=True,
            repeat_after=300
        )
        listener = Listener(hv, args, db)
        
        # Create a mock STOMP frame with a berth event (CA_MSG)
        class MockFrame:
            def __init__(self, body):
                self.body = body
                self.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}
        
        # Berth event message - CA (Cancel from berth)
        berth_payload = [
            {
                "CA_MSG": {
                    "msg_type": "CA",
                    "area_id": "EK",
                    "descr": "2C90",
                    "from": "0152",
                    "time": "1675354321000"
                }
            }
        ]
        
        frame = MockFrame(json.dumps(berth_payload))
        
        # Process the message
        listener.on_message(frame)
        
        # Verify berth event was inserted
        with db._conn:
            cursor = db._conn.execute(
                "SELECT COUNT(*) FROM td_berth_events WHERE msg_type='CA' AND headcode='2C90'"
            )
            count = cursor.fetchone()[0]
        
        assert count == 1, f"Expected 1 berth event, but found {count}"
        
        print("✓ Berth event capture test passed")
        
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_sclass_bit_changes_are_decoded_and_persisted():
    """Test that S-class snapshots and bit transitions are stored."""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        hv = HumanView()
        args = argparse.Namespace(
            verbose=False,
            width=96,
            headcode=None,
            uid=None,
            td_area=None,
            trace_headcode=False,
            only_changes=True,
            repeat_after=300
        )
        listener = Listener(hv, args, db)

        class MockFrame:
            def __init__(self, body):
                self.body = body
                self.headers = {"destination": "/topic/TD_ALL_SIG_AREA"}

        snapshot_payload = [
            {
                "SG_MSG": {
                    "msg_type": "SG",
                    "area_id": "EK",
                    "address": "D0",
                    "data": "3F",
                    "time": "1675354321000"
                }
            }
        ]
        change_payload = [
            {
                "SF_MSG": {
                    "msg_type": "SF",
                    "area_id": "EK",
                    "address": "D0",
                    "data": "BF",
                    "time": "1675354322000"
                }
            }
        ]

        listener.on_message(MockFrame(json.dumps(snapshot_payload)))
        listener.on_message(MockFrame(json.dumps(change_payload)))

        with db._conn:
            cursor = db._conn.execute(
                "SELECT msg_type, raw_data FROM td_sclass_state WHERE td_area='EK' AND address='D0'"
            )
            state_row = cursor.fetchone()
            assert state_row is not None
            assert state_row[0] == "SF"
            assert state_row[1] == "BF"

            cursor = db._conn.execute(
                """
                SELECT ts_ms, td_area, address, byte_offset, bit, old_state, new_state, raw_old, raw_new
                FROM td_sclass_changes
                WHERE td_area='EK' AND address='D0'
                ORDER BY ts_ms
                """
            )
            changes = cursor.fetchall()

        assert len(changes) == 1
        change = changes[0]
        assert change[3] == 0
        assert change[4] == 7
        assert change[5] == 0
        assert change[6] == 1
        assert change[7] == "3F"
        assert change[8] == "BF"

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_mapper_requires_matching_areas_and_preserves_signed_dt():
    """Test that correlations stay area-scoped and keep signed time deltas."""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=True)

        db.insert_td_berth_event(2000, '2024-01-01T00:00:02.000Z', 'EK', '2C90', 'CA', '0001', '0002', '2C90')
        db.insert_td_signal_event(1500, '2024-01-01T00:00:01.500Z', 'EK', 'SF', 'EK123', '01')
        db.insert_td_signal_event(1800, '2024-01-01T00:00:01.800Z', 'WK', 'SF', 'WK999', '02')

        with db._batch_lock:
            db._process_mapper_batch()

        with db._conn:
            cursor = db._conn.execute("SELECT COUNT(*) FROM berth_signal_observations")
            obs_count = cursor.fetchone()[0]
            cursor = db._conn.execute("SELECT COUNT(*) FROM berth_signal_scores")
            score_count = cursor.fetchone()[0]
            cursor = db._conn.execute(
                """
                SELECT td_area, address, dt_ms
                FROM berth_signal_observations
                WHERE td_area='EK' AND address='EK123'
                """
            )
            row = cursor.fetchone()

        assert obs_count == 1
        assert score_count == 1
        assert row is not None
        assert row[0] == 'EK'
        assert row[1] == 'EK123'
        assert row[2] == -500

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_mapper_rebuilds_across_batch_boundaries():
    """Test that retained batch overlap allows cross-batch correlations."""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=True)

        db.insert_td_berth_event(1000, '2024-01-01T00:00:01.000Z', 'EK', '2C90', 'CA', '0001', '0002', '2C90')
        with db._batch_lock:
            db._process_mapper_batch()

        with db._conn:
            cursor = db._conn.execute("SELECT COUNT(*) FROM berth_signal_observations")
            assert cursor.fetchone()[0] == 0

        db.insert_td_signal_event(1200, '2024-01-01T00:00:01.200Z', 'EK', 'SF', 'EK123', '01')
        with db._batch_lock:
            db._process_mapper_batch()

        with db._conn:
            cursor = db._conn.execute("SELECT COUNT(*) FROM berth_signal_observations")
            assert cursor.fetchone()[0] == 1
            cursor = db._conn.execute(
                "SELECT dt_ms FROM berth_signal_observations WHERE td_area='EK' AND address='EK123'"
            )
            assert cursor.fetchone()[0] == 200
            cursor = db._conn.execute(
                "SELECT score, obs_count FROM berth_signal_scores WHERE td_area='EK' AND address='EK123'"
            )
            score_row = cursor.fetchone()

        assert score_row is not None
        assert score_row[1] == 1
        assert score_row[0] > 0

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_sclass_movement_correlations_rebuild_and_dedupe():
    """Test S-class to berth correlations are reproducible and rebuild-safe."""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        db = RailDB(db_path, enable_mapper=False)
        db.update_sclass_correlation_config(pre_ms=2000, post_ms=2000, tau_ms=1000)

        db.insert_td_signal_event(800, '2024-01-01T00:00:00.800Z', 'EK', 'SF', 'D0', '00')
        db.insert_td_signal_event(1100, '2024-01-01T00:00:01.100Z', 'EK', 'SF', 'D0', '80')
        db.insert_td_berth_event(1000, '2024-01-01T00:00:01.000Z', 'EK', '2C90', 'CA', '0152', '0153', '2C90')

        status = db.get_sclass_correlation_status()
        assert status['config']['pre_ms'] == 2000
        assert status['observation_count'] == 1
        assert status['score_count'] == 1
        assert status['movement_count'] == 1
        assert status['change_count'] == 1

        with db._conn:
            row = db._conn.execute(
                """
                SELECT td_area, from_berth, to_berth, observation_count, matching_count,
                       correlation_pct, lead_count, lag_count, on_count, off_count, associated_bits_json
                FROM td_sclass_movement_scores
                WHERE td_area='EK' AND from_berth='0152' AND to_berth='0153'
                """
            ).fetchone()

        assert row is not None
        assert row[0] == 'EK'
        assert row[1] == '0152'
        assert row[2] == '0153'
        assert row[3] == 1
        assert row[4] == 1
        assert row[5] == 1.0
        assert row[6] == 0
        assert row[7] == 1
        assert row[8] == 1
        assert row[9] == 0
        assert row[10] == '["D0.7"]'

        first = db.rebuild_td_sclass_correlations()
        second = db.rebuild_td_sclass_correlations()

        assert first['inserted_observations'] == 1
        assert second['inserted_observations'] == 1

        with db._conn:
            obs_count = db._conn.execute("SELECT COUNT(*) FROM td_sclass_movement_observations").fetchone()[0]
            score_count = db._conn.execute("SELECT COUNT(*) FROM td_sclass_movement_scores").fetchone()[0]

        assert obs_count == 1
        assert score_count == 1

        db.close()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    test_signal_event_capture()
    test_berth_events_still_work()
    test_sclass_bit_changes_are_decoded_and_persisted()
    test_mapper_requires_matching_areas_and_preserves_signed_dt()
    test_mapper_rebuilds_across_batch_boundaries()
    print("\nAll tests passed! ✓")
