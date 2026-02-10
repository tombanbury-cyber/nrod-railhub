#!/usr/bin/env python3
"""Test VSTP parsing with real message structure from issue."""

import argparse
import json
import sqlite3
import tempfile
from unittest.mock import Mock

from nrod_railhub.listener import Listener
from nrod_railhub.views import HumanView
from nrod_railhub.database import RailDB
from nrod_railhub.resolvers import LocationResolver


def test_real_vstp_message_structure():
    """Test that real VSTP message structure from issue is correctly parsed."""
    # Sample VSTP message from the issue (simplified for testing)
    vstp_message = {
        "VSTPCIFMsgV1": {
            "schedule": {
                "schedule_segment": [{
                    "schedule_location": [
                        {
                            "location": {"tiploc": {"tiploc_id": "STRETHM"}},
                            "scheduled_pass_time": " ",
                            "scheduled_departure_time": "000900",
                            "scheduled_arrival_time": " ",
                            "public_departure_time": "      ",
                            "public_arrival_time": " ",
                            "CIF_platform": "2",
                            "CIF_path": " ",
                            "CIF_activity": "TB"
                        },
                        {
                            "location": {"tiploc": {"tiploc_id": "HORSHUS"}},
                            "scheduled_pass_time": " ",
                            "scheduled_departure_time": " ",
                            "scheduled_arrival_time": "014500",
                            "public_departure_time": " ",
                            "public_arrival_time": "      ",
                            "CIF_activity": "TF"
                        }
                    ],
                    "signalling_id": "8Y92",
                    "atoc_code": "ZZ",
                    "CIF_train_service_code": "95999801",
                    "CIF_train_category": "DD",
                    "CIF_speed": "090",
                    "CIF_power_type": "D",
                    "CIF_course_indicator": "1"
                }]
            },
            "transaction_type": "Create",
            "train_status": "2",
            "schedule_start_date": "2026-02-16",
            "schedule_end_date": "2026-02-16",
            "schedule_days_runs": "1000000",
            "applicable_timetable": "Y",
            "CIF_train_uid": "W01880",
            "CIF_stp_indicator": "C"
        },
        "Sender": {
            "organisation": "Network Rail",
            "application": "TSIA",
            "component": "INTEGRALE-VSTP"
        }
    }
    
    # Create HumanView with resolver
    resolver = LocationResolver()
    hv = HumanView(resolver=resolver)
    
    # Parse the message
    vs = hv.upsert_vstp(vstp_message)
    
    # Verify the schedule was parsed correctly
    assert vs is not None, "VSTP message should be parsed"
    assert vs.uid == "W01880", f"Expected uid='W01880', got '{vs.uid}'"
    assert vs.signalling_id == "8Y92", f"Expected signalling_id='8Y92', got '{vs.signalling_id}'"
    assert vs.start_date == "2026-02-16", f"Expected start_date='2026-02-16', got '{vs.start_date}'"
    assert vs.end_date == "2026-02-16", f"Expected end_date='2026-02-16', got '{vs.end_date}'"
    
    # Verify locations were parsed
    assert len(vs.locations) == 2, f"Expected 2 locations, got {len(vs.locations)}"
    assert vs.locations[0][0] == "STRETHM", f"First location should be STRETHM, got {vs.locations[0][0]}"
    assert vs.locations[1][0] == "HORSHUS", f"Second location should be HORSHUS, got {vs.locations[1][0]}"


def test_real_vstp_message_db_persistence():
    """Test that real VSTP message structure is correctly persisted to database."""
    # Sample VSTP message from the issue (simplified)
    vstp_message = {
        "VSTPCIFMsgV1": {
            "schedule": {
                "schedule_segment": [{
                    "schedule_location": [
                        {
                            "location": {"tiploc": {"tiploc_id": "STRETHM"}},
                            "scheduled_departure_time": "000900",
                            "scheduled_arrival_time": " ",
                            "CIF_platform": "2"
                        },
                        {
                            "location": {"tiploc": {"tiploc_id": "HORSHUS"}},
                            "scheduled_arrival_time": "014500"
                        }
                    ],
                    "signalling_id": "8Y92",
                    "CIF_train_service_code": "95999801"
                }]
            },
            "transaction_type": "Create",
            "train_status": "2",
            "schedule_start_date": "2026-02-16",
            "schedule_end_date": "2026-02-16",
            "CIF_train_uid": "W01880",
            "CIF_stp_indicator": "C"
        }
    }
    
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    # Setup components
    db = RailDB(db_path, enable_mapper=False)
    resolver = LocationResolver()
    hv = HumanView(resolver=resolver)
    
    # Mock argparse namespace
    args = argparse.Namespace(
        verbose=False,
        trace_headcode=False,
        headcode=None,
        uid=None,
        td_area=None,
        width=96,
        only_changes=True,
        repeat_after=300
    )
    
    # Create listener with database
    listener = Listener(hv, args, db=db)
    
    # Create frame mock
    frame = Mock()
    frame.body = json.dumps([vstp_message])
    frame.headers = {"destination": "/topic/VSTP_ALL"}
    
    # Process the message
    listener.on_message(frame)
    
    # Verify vstp_state was updated
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT uid, headcode, start_date, end_date FROM vstp_state WHERE uid=?", ("W01880",))
    state_row = cursor.fetchone()
    assert state_row is not None, "VSTP state should be persisted"
    assert state_row[0] == "W01880", f"Expected uid='W01880', got '{state_row[0]}'"
    assert state_row[1] == "8Y92", f"Expected headcode='8Y92', got '{state_row[1]}'"
    assert state_row[2] == "2026-02-16", f"Expected start_date='2026-02-16', got '{state_row[2]}'"
    
    # Verify vstp_schedules header was created
    cursor.execute("SELECT uid, schedule_start_date, signalling_id, CIF_train_uid FROM vstp_schedules WHERE uid=?", ("W01880",))
    schedule_row = cursor.fetchone()
    assert schedule_row is not None, "VSTP schedule header should be persisted"
    assert schedule_row[0] == "W01880", f"Expected uid='W01880', got '{schedule_row[0]}'"
    assert schedule_row[1] == "2026-02-16", f"Expected schedule_start_date='2026-02-16', got '{schedule_row[1]}'"
    assert schedule_row[2] == "8Y92", f"Expected signalling_id='8Y92', got '{schedule_row[2]}'"
    
    # Verify vstp_schedule_locations were created
    cursor.execute(
        "SELECT tiploc, scheduled_departure_time, scheduled_arrival_time FROM vstp_schedule_locations WHERE uid=? ORDER BY location_index",
        ("W01880",)
    )
    location_rows = cursor.fetchall()
    conn.close()
    
    assert len(location_rows) == 2, f"Expected 2 location rows, got {len(location_rows)}"
    assert location_rows[0][0] == "STRETHM", f"First location should be STRETHM, got {location_rows[0][0]}"
    assert location_rows[0][1] == "000900", f"First departure should be 000900, got {location_rows[0][1]}"
    assert location_rows[1][0] == "HORSHUS", f"Second location should be HORSHUS, got {location_rows[1][0]}"
    assert location_rows[1][2] == "014500", f"Second arrival should be 014500, got {location_rows[1][2]}"
    
    # Cleanup
    db.close()
    import os
    os.unlink(db_path)
