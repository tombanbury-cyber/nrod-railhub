#!/usr/bin/env python3
"""Tests for CIF schedule database persistence."""

import json
import sqlite3
import tempfile
import os
from datetime import datetime, timezone

from nrod_railhub.database import RailDB


def test_cif_schedule_schema_creation():
    """Test that CIF schedule tables are created correctly."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Create database
        db = RailDB(db_path)
        
        # Verify tables exist
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check cif_schedules table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cif_schedules'")
        assert cursor.fetchone() is not None
        
        # Check cif_schedule_locations table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cif_schedule_locations'")
        assert cursor.fetchone() is not None
        
        # Check indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cif_schedules_uid'")
        assert cursor.fetchone() is not None
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cif_schedules_toc'")
        assert cursor.fetchone() is not None
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cif_loc_tiploc'")
        assert cursor.fetchone() is not None
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_cif_schedule_basic():
    """Test inserting a basic CIF schedule."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, save_raw_json=True)
        
        # Create a sample CIF schedule record
        cif_record = {
            "CIF_train_uid": "C12345",
            "schedule_start_date": "2026-02-11",
            "schedule_end_date": "2026-12-31",
            "schedule_days_runs": "1111100",
            "CIF_stp_indicator": "P",
            "train_status": "P",
            "transaction_type": "Create",
            "applicable_timetable": "Y",
            "schedule_segment": {
                "signalling_id": "2C90",
                "CIF_train_service_code": "12345678",
                "CIF_train_category": "OO",
                "CIF_power_type": "EMU",
                "schedule_location": [
                    {
                        "tiploc_code": "CLPHMJC",
                        "scheduled_departure_time": "0830",
                        "public_departure": "0830",
                        "platform": "1",
                        "CIF_activity": "TB"
                    },
                    {
                        "tiploc_code": "VICTRIC",
                        "scheduled_arrival_time": "0845",
                        "public_arrival": "0845",
                        "platform": "2",
                        "CIF_activity": "TF"
                    }
                ]
            }
        }
        
        # Insert schedule
        db.insert_cif_schedule(cif_record, "SE")
        
        # Verify insertion
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check schedule header
        cursor.execute("SELECT * FROM cif_schedules WHERE uid=?", ("C12345",))
        row = cursor.fetchone()
        assert row is not None
        
        # Verify fields (get column names)
        cursor.execute("PRAGMA table_info(cif_schedules)")
        columns = [col[1] for col in cursor.fetchall()]
        row_dict = dict(zip(columns, row))
        
        assert row_dict['uid'] == 'C12345'
        assert row_dict['schedule_start_date'] == '2026-02-11'
        assert row_dict['toc_code'] == 'SE'
        assert row_dict['CIF_headcode'] == '2C90'
        assert row_dict['CIF_stp_indicator'] == 'P'
        
        # Check schedule locations
        cursor.execute("SELECT * FROM cif_schedule_locations WHERE uid=? ORDER BY location_index", ("C12345",))
        locations = cursor.fetchall()
        assert len(locations) == 2
        
        # Get location column names
        cursor.execute("PRAGMA table_info(cif_schedule_locations)")
        loc_columns = [col[1] for col in cursor.fetchall()]
        
        loc1 = dict(zip(loc_columns, locations[0]))
        loc2 = dict(zip(loc_columns, locations[1]))
        
        assert loc1['tiploc'] == 'CLPHMJC'
        assert loc1['scheduled_departure_time'] == '0830'
        assert loc1['platform'] == '1'
        
        assert loc2['tiploc'] == 'VICTRIC'
        assert loc2['scheduled_arrival_time'] == '0845'
        assert loc2['platform'] == '2'
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_cif_schedule_with_multiple_tocs():
    """Test inserting schedules for multiple TOCs."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, save_raw_json=False)
        
        # Insert schedules for different TOCs
        for toc, uid in [("SE", "C11111"), ("GW", "C22222"), ("SW", "C33333")]:
            cif_record = {
                "CIF_train_uid": uid,
                "schedule_start_date": "2026-02-11",
                "schedule_end_date": "2026-12-31",
                "CIF_stp_indicator": "P",
                "schedule_segment": {
                    "signalling_id": f"{uid[1:]}",
                    "schedule_location": [
                        {"tiploc_code": "START", "scheduled_departure_time": "0800"},
                        {"tiploc_code": "END", "scheduled_arrival_time": "0900"}
                    ]
                }
            }
            db.insert_cif_schedule(cif_record, toc)
        
        # Verify TOC filtering works
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT uid, toc_code FROM cif_schedules ORDER BY uid")
        results = cursor.fetchall()
        
        assert len(results) == 3
        assert results[0] == ('C11111', 'SE')
        assert results[1] == ('C22222', 'GW')
        assert results[2] == ('C33333', 'SW')
        
        # Test querying by TOC
        cursor.execute("SELECT COUNT(*) FROM cif_schedules WHERE toc_code=?", ("SE",))
        assert cursor.fetchone()[0] == 1
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_cif_schedule_replace():
    """Test that inserting same schedule twice replaces the old one."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path)
        
        # Insert schedule first time
        cif_record_v1 = {
            "CIF_train_uid": "C12345",
            "schedule_start_date": "2026-02-11",
            "CIF_stp_indicator": "P",
            "schedule_segment": {
                "signalling_id": "2C90",
                "schedule_location": [
                    {"tiploc_code": "LOC1", "scheduled_departure_time": "0800"}
                ]
            }
        }
        db.insert_cif_schedule(cif_record_v1, "SE")
        
        # Insert updated schedule
        cif_record_v2 = {
            "CIF_train_uid": "C12345",
            "schedule_start_date": "2026-02-11",
            "CIF_stp_indicator": "P",
            "schedule_segment": {
                "signalling_id": "2C90",
                "schedule_location": [
                    {"tiploc_code": "LOC1", "scheduled_departure_time": "0800"},
                    {"tiploc_code": "LOC2", "scheduled_arrival_time": "0900"}
                ]
            }
        }
        db.insert_cif_schedule(cif_record_v2, "SE")
        
        # Verify only one schedule header exists
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM cif_schedules WHERE uid=?", ("C12345",))
        assert cursor.fetchone()[0] == 1
        
        # Verify locations were updated (should have 2 locations now)
        cursor.execute("SELECT COUNT(*) FROM cif_schedule_locations WHERE uid=?", ("C12345",))
        assert cursor.fetchone()[0] == 2
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


def test_cif_schedule_retention():
    """Test CIF schedule retention/cleanup."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # Create database with 1-day retention
        db = RailDB(db_path, retain_cif_days=1)
        
        # Insert test schedules
        for i in range(5):
            cif_record = {
                "CIF_train_uid": f"C1234{i}",
                "schedule_start_date": "2026-02-11",
                "CIF_stp_indicator": "P",
                "schedule_segment": {
                    "signalling_id": f"2C9{i}",
                    "schedule_location": [
                        {"tiploc_code": "START"}
                    ]
                }
            }
            db.insert_cif_schedule(cif_record, "SE")
        
        # Verify schedules were inserted
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cif_schedules")
        assert cursor.fetchone()[0] == 5
        cursor.execute("SELECT COUNT(*) FROM cif_schedule_locations")
        assert cursor.fetchone()[0] == 5
        
        # Manually set timestamps to 2 days ago to trigger retention
        import time
        two_days_ago_ms = int((time.time() - (2 * 24 * 60 * 60)) * 1000)
        cursor.execute("UPDATE cif_schedules SET created_at_ts=?", (two_days_ago_ms,))
        cursor.execute("UPDATE cif_schedule_locations SET created_at_ts=?", (two_days_ago_ms,))
        conn.commit()
        conn.close()
        
        # Run purge (with 1-day retention and 2-day-old records, all should be deleted)
        deleted = db.purge_old_data()
        
        # Verify schedules were purged
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cif_schedules")
        schedules_remaining = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM cif_schedule_locations")
        locations_remaining = cursor.fetchone()[0]
        conn.close()
        
        # With 1-day retention and 2-day-old data, all should be deleted
        assert schedules_remaining == 0
        assert locations_remaining == 0
        assert deleted['cif_schedules'] == 5
        
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_cif_schedule_without_raw_json():
    """Test that save_raw_json=False works correctly."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, save_raw_json=False)
        
        cif_record = {
            "CIF_train_uid": "C12345",
            "schedule_start_date": "2026-02-11",
            "CIF_stp_indicator": "P",
            "schedule_segment": {
                "signalling_id": "2C90",
                "schedule_location": [
                    {"tiploc_code": "START"}
                ]
            }
        }
        db.insert_cif_schedule(cif_record, "SE")
        
        # Verify raw_json is NULL
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM cif_schedules WHERE uid=?", ("C12345",))
        raw_json = cursor.fetchone()[0]
        assert raw_json is None
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_cif_schedule_missing_required_fields():
    """Test that schedules without required fields are skipped."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path)
        
        # Missing UID
        cif_record_no_uid = {
            "schedule_start_date": "2026-02-11",
            "schedule_segment": {"signalling_id": "2C90"}
        }
        db.insert_cif_schedule(cif_record_no_uid, "SE")
        
        # Missing start date
        cif_record_no_date = {
            "CIF_train_uid": "C12345",
            "schedule_segment": {"signalling_id": "2C90"}
        }
        db.insert_cif_schedule(cif_record_no_date, "SE")
        
        # Verify nothing was inserted
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cif_schedules")
        assert cursor.fetchone()[0] == 0
        
        conn.close()
        db.close()
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
