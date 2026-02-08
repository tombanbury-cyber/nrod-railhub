#!/usr/bin/env python3
"""Unit tests for data retention functionality."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

from nrod_railhub.database import RailDB


def test_purge_trust_messages():
    """Test purging old trust_messages."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # Create database without retention
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert test messages with different timestamps
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - (10 * 24 * 60 * 60 * 1000)  # 10 days ago
        recent_ms = now_ms - (2 * 24 * 60 * 60 * 1000)  # 2 days ago
        
        # Insert old message
        test_body_old = {
            "train_id": "OLD_TRAIN_001",
            "actual_timestamp": str(old_ms),
            "event_type": "DEPARTURE"
        }
        db.insert_trust_message(test_body_old)
        
        # Manually update created_at_ts to old timestamp
        cursor = db._conn.cursor()
        cursor.execute(
            "UPDATE trust_messages SET created_at_ts=? WHERE train_id=?",
            (old_ms, "OLD_TRAIN_001")
        )
        db._conn.commit()
        
        # Insert recent message
        test_body_recent = {
            "train_id": "RECENT_TRAIN_001",
            "actual_timestamp": str(recent_ms),
            "event_type": "ARRIVAL"
        }
        db.insert_trust_message(test_body_recent)
        
        # Manually update created_at_ts to recent timestamp
        cursor.execute(
            "UPDATE trust_messages SET created_at_ts=? WHERE train_id=?",
            (recent_ms, "RECENT_TRAIN_001")
        )
        db._conn.commit()
        
        # Verify both messages exist
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 2
        
        # Purge messages older than 5 days (should delete the old one)
        cutoff_ms = now_ms - (5 * 24 * 60 * 60 * 1000)
        deleted = db._purge_trust_messages(cutoff_ms, 1000)
        
        # Verify only 1 message was deleted
        assert deleted == 1
        
        # Verify only recent message remains
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 1
        
        cursor.execute("SELECT train_id FROM trust_messages")
        remaining_train_id = cursor.fetchone()[0]
        assert remaining_train_id == "RECENT_TRAIN_001"
        
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_purge_vstp_schedules():
    """Test purging old vstp_schedules and their locations."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # Create database without retention
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert test schedules with different timestamps
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - (10 * 24 * 60 * 60 * 1000)  # 10 days ago
        recent_ms = now_ms - (2 * 24 * 60 * 60 * 1000)  # 2 days ago
        
        # Insert old schedule
        test_vstp_old = {
            "VSTPCIFMsgV1": {
                "schedule_start_date": "2026-01-01",
                "schedule_end_date": "2026-01-01",
                "CIF_train_uid": "OLD_UID_001",
                "schedule": {
                    "schedule_segment": [
                        {
                            "signalling_id": "2C90",
                            "schedule_location": [
                                {"tiploc": "CLPHMJC", "scheduled_departure_time": "12:30"}
                            ]
                        }
                    ]
                }
            }
        }
        db.insert_vstp_schedule(test_vstp_old)
        
        # Manually update created_at_ts to old timestamp
        cursor = db._conn.cursor()
        cursor.execute(
            "UPDATE vstp_schedules SET created_at_ts=? WHERE uid=?",
            (old_ms, "OLD_UID_001")
        )
        db._conn.commit()
        
        # Insert recent schedule
        test_vstp_recent = {
            "VSTPCIFMsgV1": {
                "schedule_start_date": "2026-02-01",
                "schedule_end_date": "2026-02-01",
                "CIF_train_uid": "RECENT_UID_001",
                "schedule": {
                    "schedule_segment": [
                        {
                            "signalling_id": "2C91",
                            "schedule_location": [
                                {"tiploc": "VICTRIC", "scheduled_arrival_time": "12:45"}
                            ]
                        }
                    ]
                }
            }
        }
        db.insert_vstp_schedule(test_vstp_recent)
        
        # Manually update created_at_ts to recent timestamp
        cursor.execute(
            "UPDATE vstp_schedules SET created_at_ts=? WHERE uid=?",
            (recent_ms, "RECENT_UID_001")
        )
        db._conn.commit()
        
        # Verify both schedules exist
        cursor.execute("SELECT COUNT(*) FROM vstp_schedules")
        assert cursor.fetchone()[0] == 2
        
        cursor.execute("SELECT COUNT(*) FROM vstp_schedule_locations")
        assert cursor.fetchone()[0] == 2
        
        # Purge schedules older than 5 days (should delete the old one)
        cutoff_ms = now_ms - (5 * 24 * 60 * 60 * 1000)
        deleted = db._purge_vstp_schedules(cutoff_ms, 1000)
        
        # Verify only 1 schedule was deleted
        assert deleted == 1
        
        # Verify only recent schedule remains
        cursor.execute("SELECT COUNT(*) FROM vstp_schedules")
        assert cursor.fetchone()[0] == 1
        
        cursor.execute("SELECT uid FROM vstp_schedules")
        remaining_uid = cursor.fetchone()[0]
        assert remaining_uid == "RECENT_UID_001"
        
        # Verify location was also deleted
        cursor.execute("SELECT COUNT(*) FROM vstp_schedule_locations")
        assert cursor.fetchone()[0] == 1
        
        cursor.execute("SELECT uid FROM vstp_schedule_locations")
        remaining_loc_uid = cursor.fetchone()[0]
        assert remaining_loc_uid == "RECENT_UID_001"
        
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_batched_deletion():
    """Test that batched deletion works correctly with large datasets."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # Create database without retention
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert 100 old messages
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - (10 * 24 * 60 * 60 * 1000)  # 10 days ago
        
        cursor = db._conn.cursor()
        for i in range(100):
            test_body = {
                "train_id": f"TRAIN_{i:03d}",
                "actual_timestamp": str(old_ms),
                "event_type": "DEPARTURE"
            }
            db.insert_trust_message(test_body)
            # Manually update created_at_ts to old timestamp
            cursor.execute(
                "UPDATE trust_messages SET created_at_ts=? WHERE train_id=?",
                (old_ms, f"TRAIN_{i:03d}")
            )
        db._conn.commit()
        
        # Verify all messages exist
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 100
        
        # Purge with small batch size (10)
        cutoff_ms = now_ms - (5 * 24 * 60 * 60 * 1000)
        deleted = db._purge_trust_messages(cutoff_ms, 10)
        
        # Verify all 100 messages were deleted
        assert deleted == 100
        
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 0
        
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_purge_old_data_method():
    """Test the high-level purge_old_data method."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # Create database with retention settings (but don't start thread)
        db = RailDB(
            db_path,
            enable_mapper=False,
            retain_trust_days=5,
            retain_vstp_days=7,
        )
        # Stop the retention thread immediately
        db.stop_retention()
        
        # Insert old data
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - (10 * 24 * 60 * 60 * 1000)  # 10 days ago
        
        # Insert old trust message
        test_body = {
            "train_id": "OLD_TRAIN",
            "actual_timestamp": str(old_ms),
            "event_type": "DEPARTURE"
        }
        db.insert_trust_message(test_body)
        
        # Manually update created_at_ts to old timestamp
        cursor = db._conn.cursor()
        cursor.execute(
            "UPDATE trust_messages SET created_at_ts=? WHERE train_id=?",
            (old_ms, "OLD_TRAIN")
        )
        db._conn.commit()
        
        # Insert old vstp schedule
        test_vstp = {
            "VSTPCIFMsgV1": {
                "schedule_start_date": "2026-01-01",
                "CIF_train_uid": "OLD_UID",
                "schedule": {"schedule_segment": [{"signalling_id": "2C90"}]}
            }
        }
        db.insert_vstp_schedule(test_vstp)
        
        # Update timestamps to old
        cursor.execute("UPDATE vstp_schedules SET created_at_ts=? WHERE uid=?", (old_ms, "OLD_UID"))
        db._conn.commit()
        
        # Run purge
        result = db.purge_old_data()
        
        # Verify deletion counts
        assert result['trust_messages'] == 1
        assert result['vstp_schedules'] == 1
        
        # Verify data was deleted
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 0
        
        cursor.execute("SELECT COUNT(*) FROM vstp_schedules")
        assert cursor.fetchone()[0] == 0
        
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_retention_with_no_settings():
    """Test that purge_old_data does nothing when retention is not configured."""
    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        # Create database without retention settings
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert data
        test_body = {
            "train_id": "TEST_TRAIN",
            "actual_timestamp": str(int(time.time() * 1000)),
            "event_type": "DEPARTURE"
        }
        db.insert_trust_message(test_body)
        
        # Run purge (should do nothing)
        result = db.purge_old_data()
        
        # Verify no deletions
        assert result['trust_messages'] == 0
        assert result['vstp_schedules'] == 0
        
        # Verify data still exists
        cursor = db._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trust_messages")
        assert cursor.fetchone()[0] == 1
        
        db.close()
    finally:
        Path(db_path).unlink(missing_ok=True)
