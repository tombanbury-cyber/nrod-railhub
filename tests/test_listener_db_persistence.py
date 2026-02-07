#!/usr/bin/env python3
"""Unit tests for Listener DB persistence of VSTP and TRUST messages."""

import argparse
import json
import sqlite3
import tempfile
from unittest.mock import Mock

from nrod_railhub.listener import Listener
from nrod_railhub.views import HumanView
from nrod_railhub.database import RailDB
from nrod_railhub.models import VstpSchedule, TrustState


def test_vstp_message_persists_to_db():
    """Test that VSTP messages are persisted to the database."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    # Setup components
    db = RailDB(db_path, enable_mapper=False)
    hv = Mock(spec=HumanView)
    
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
    
    # Mock HumanView.upsert_vstp to return a VstpSchedule
    vstp_schedule = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        end_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    hv.upsert_vstp.return_value = vstp_schedule
    
    # Create a VSTP message
    vstp_message = {
        "VSTPCIFMsgV1": {
            "schedule": {
                "CIF_train_uid": "C12345",
                "schedule_start_date": "2026-01-17",
                "schedule_end_date": "2026-01-17",
                "train_uid": "C12345",
                "signalling_id": "2C90",
                "schedule_location": [
                    {
                        "location_type": "LO",
                        "tiploc_code": "CLPHMJC",
                        "scheduled_departure_time": "1230"
                    },
                    {
                        "location_type": "LT",
                        "tiploc_code": "VICTRIC",
                        "scheduled_arrival_time": "1245"
                    }
                ]
            }
        }
    }
    
    # Create frame mock
    frame = Mock()
    frame.body = json.dumps([vstp_message])
    frame.headers = {"destination": "/topic/VSTP_ALL"}
    
    # Process the message
    listener.on_message(frame)
    
    # Verify HumanView was called
    assert hv.upsert_vstp.called
    
    # Verify database was updated
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT uid, headcode, start_date, end_date FROM vstp_state WHERE uid=?", ("C12345",))
    row = cursor.fetchone()
    
    assert row is not None, "VSTP message should be persisted to database"
    assert row[0] == "C12345", f"Expected uid='C12345', got '{row[0]}'"
    assert row[1] == "2C90", f"Expected headcode='2C90', got '{row[1]}'"
    assert row[2] == "2026-01-17", f"Expected start_date='2026-01-17', got '{row[2]}'"
    assert row[3] == "2026-01-17", f"Expected end_date='2026-01-17', got '{row[3]}'"
    
    # Verify vstp_location table has entries
    cursor.execute("SELECT tiploc, planned_arr, planned_dep FROM vstp_location WHERE uid=? AND start_date=? ORDER BY stop_index", ("C12345", "2026-01-17"))
    location_rows = cursor.fetchall()
    conn.close()
    
    assert len(location_rows) == 2, f"Expected 2 location rows, got {len(location_rows)}"
    assert location_rows[0][0] == "CLPHMJC", f"Expected first location 'CLPHMJC', got '{location_rows[0][0]}'"
    assert location_rows[0][1] == "12:30", f"Expected first arrival '12:30', got '{location_rows[0][1]}'"
    assert location_rows[0][2] == "12:31", f"Expected first departure '12:31', got '{location_rows[0][2]}'"
    assert location_rows[1][0] == "VICTRIC", f"Expected second location 'VICTRIC', got '{location_rows[1][0]}'"
    
    # Cleanup
    db.close()
    import os
    os.unlink(db_path)


def test_trust_message_persists_to_db():
    """Test that TRUST messages are persisted to the database."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    # Setup components
    db = RailDB(db_path, enable_mapper=False)
    hv = Mock(spec=HumanView)
    
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
    
    # Mock HumanView.upsert_trust to return a TrustState
    trust_state = TrustState(
        train_id="123456789",
        train_uid="C12345",
        toc_id="SW",
        last_event_time="2026-01-17T12:30:00Z",
        last_location="87701",
        last_delay_min=0,
        activated=True
    )
    hv.upsert_trust.return_value = trust_state
    
    # Create a TRUST activation message
    trust_message = {
        "header": {
            "msg_type": "0001",
            "source_dev_id": "",
            "user_id": "",
            "original_data_source": "TRUST",
            "msg_queue_timestamp": "1737116400000",
            "source_system_id": "TRUST"
        },
        "body": {
            "msg_type": "0001",
            "train_id": "123456789",
            "train_uid": "C12345",
            "train_reporting_number": "2C90",
            "toc_id": "SW",
            "tp_origin_timestamp": "2026-01-17",
            "creation_timestamp": "1737116400000",
            "origin_dep_timestamp": "1737116400000",
            "loc_stanox": "87701",
            "d1266_record_number": "00000"
        }
    }
    
    # Create frame mock
    frame = Mock()
    frame.body = json.dumps([trust_message])
    frame.headers = {"destination": "/topic/TRAIN_MVT_ALL_TOC"}
    
    # Process the message
    listener.on_message(frame)
    
    # Verify HumanView was called
    assert hv.upsert_trust.called
    
    # Verify database was updated
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT train_id, headcode, uid, toc_id, last_event_time, last_location, last_delay_min, last_event_time_ms FROM trust_state WHERE train_id=?", ("123456789",))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None, "TRUST message should be persisted to database"
    assert row[0] == "123456789", f"Expected train_id='123456789', got '{row[0]}'"
    assert row[1] == "2C90", f"Expected headcode='2C90', got '{row[1]}'"
    assert row[2] == "C12345", f"Expected uid='C12345', got '{row[2]}'"
    assert row[3] == "SW", f"Expected toc_id='SW', got '{row[3]}'"
    assert row[4] == "2026-01-17T12:30:00Z", f"Expected last_event_time='2026-01-17T12:30:00Z', got '{row[4]}'"
    assert row[5] == "87701", f"Expected last_location='87701', got '{row[5]}'"
    assert row[6] == 0, f"Expected last_delay_min=0, got {row[6]}"
    assert row[7] == 1737116400000, f"Expected last_event_time_ms=1737116400000, got {row[7]}"
    
    # Cleanup
    db.close()
    import os
    os.unlink(db_path)


def test_listener_works_without_db():
    """Test that listener still works when db is None."""
    # Setup components without database
    hv = Mock(spec=HumanView)
    
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
    
    # Create listener without database
    listener = Listener(hv, args, db=None)
    
    # Mock HumanView.upsert_vstp to return a VstpSchedule
    vstp_schedule = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        end_date="2026-01-17"
    )
    hv.upsert_vstp.return_value = vstp_schedule
    
    # Create a VSTP message
    vstp_message = {
        "VSTPCIFMsgV1": {
            "schedule": {
                "CIF_train_uid": "C12345",
                "train_uid": "C12345",
                "signalling_id": "2C90"
            }
        }
    }
    
    # Create frame mock
    frame = Mock()
    frame.body = json.dumps([vstp_message])
    frame.headers = {"destination": "/topic/VSTP_ALL"}
    
    # Process the message - should not raise exception
    listener.on_message(frame)
    
    # Verify HumanView was still called
    assert hv.upsert_vstp.called


def test_db_error_does_not_crash_listener():
    """Test that database errors don't crash the listener."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    # Setup components
    db = RailDB(db_path, enable_mapper=False)
    hv = Mock(spec=HumanView)
    
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
    
    # Mock HumanView.upsert_vstp to return a VstpSchedule
    vstp_schedule = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        end_date="2026-01-17"
    )
    hv.upsert_vstp.return_value = vstp_schedule
    
    # Close the database to trigger an error
    db.close()
    
    # Create a VSTP message
    vstp_message = {
        "VSTPCIFMsgV1": {
            "schedule": {
                "CIF_train_uid": "C12345",
                "train_uid": "C12345",
                "signalling_id": "2C90"
            }
        }
    }
    
    # Create frame mock
    frame = Mock()
    frame.body = json.dumps([vstp_message])
    frame.headers = {"destination": "/topic/VSTP_ALL"}
    
    # Process the message - should not crash despite DB error
    listener.on_message(frame)
    
    # Verify HumanView was still called
    assert hv.upsert_vstp.called
    
    # Cleanup
    import os
    os.unlink(db_path)
