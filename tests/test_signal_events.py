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


if __name__ == "__main__":
    test_signal_event_capture()
    test_berth_events_still_work()
    test_sclass_bit_changes_are_decoded_and_persisted()
    print("\nAll tests passed! ✓")
