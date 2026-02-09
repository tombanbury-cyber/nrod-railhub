#!/usr/bin/env python3
"""Unit tests for save_raw_json database setting."""

import json
import os
import sqlite3
import tempfile

from nrod_railhub.database import RailDB


def test_save_raw_json_enabled():
    """Test that raw JSON is saved when save_raw_json=True."""
    # Create temporary database with save_raw_json=True (default)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False, save_raw_json=True)
        
        # Insert TRUST state with raw JSON
        raw_data = {"train_id": "123456", "event": "arrival", "location": "VICTRIC"}
        db.upsert_trust(
            train_id="123456",
            headcode="2C90",
            uid="C12345",
            toc_id="GW",
            last_event_time="2026-01-17 12:30:00",
            last_location="VICTRIC",
            last_delay_min=0,
            raw=raw_data
        )
        
        # Verify raw JSON was saved
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM trust_state WHERE train_id='123456'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None
        assert result[0] is not None
        saved_data = json.loads(result[0])
        assert saved_data == raw_data
        
        print("✓ Test passed: Raw JSON saved when enabled")
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_save_raw_json_disabled():
    """Test that raw JSON is NOT saved when save_raw_json=False."""
    # Create temporary database with save_raw_json=False
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False, save_raw_json=False)
        
        # Insert TRUST state with raw JSON
        raw_data = {"train_id": "654321", "event": "departure", "location": "CLPHMJC"}
        db.upsert_trust(
            train_id="654321",
            headcode="1A23",
            uid="C54321",
            toc_id="SW",
            last_event_time="2026-01-17 13:30:00",
            last_location="CLPHMJC",
            last_delay_min=5,
            raw=raw_data
        )
        
        # Verify raw JSON was NOT saved (should be NULL)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM trust_state WHERE train_id='654321'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None
        assert result[0] is None  # raw_json should be NULL
        
        print("✓ Test passed: Raw JSON NOT saved when disabled")
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_vstp_save_raw_json_enabled():
    """Test that VSTP raw JSON is saved when save_raw_json=True."""
    # Create temporary database with save_raw_json=True
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False, save_raw_json=True)
        
        # Insert VSTP state with raw JSON
        raw_data = {"uid": "C98765", "schedule": "test"}
        db.upsert_vstp(
            uid="C98765",
            headcode="5Z99",
            start_date="2026-01-17",
            end_date="2026-01-17",
            raw=raw_data
        )
        
        # Verify raw JSON was saved
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM vstp_state WHERE uid='C98765'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None
        assert result[0] is not None
        saved_data = json.loads(result[0])
        assert saved_data == raw_data
        
        print("✓ Test passed: VSTP raw JSON saved when enabled")
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_vstp_save_raw_json_disabled():
    """Test that VSTP raw JSON is NOT saved when save_raw_json=False."""
    # Create temporary database with save_raw_json=False
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False, save_raw_json=False)
        
        # Insert VSTP state with raw JSON
        raw_data = {"uid": "C11111", "schedule": "test2"}
        db.upsert_vstp(
            uid="C11111",
            headcode="8X88",
            start_date="2026-01-17",
            end_date="2026-01-17",
            raw=raw_data
        )
        
        # Verify raw JSON was NOT saved (should be NULL)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM vstp_state WHERE uid='C11111'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None
        assert result[0] is None  # raw_json should be NULL
        
        print("✓ Test passed: VSTP raw JSON NOT saved when disabled")
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_trust_message_save_raw_json_disabled():
    """Test that TRUST message raw JSON is NOT saved when save_raw_json=False."""
    # Create temporary database with save_raw_json=False
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False, save_raw_json=False)
        
        # Insert TRUST message with unique train_id and timestamp
        trust_body = {
            "train_id": "999888777",
            "actual_timestamp": "1705498800000",
            "event_type": "ARRIVAL",
            "reporting_stanox": "87701"
        }
        db.insert_trust_message(trust_body)
        
        # Verify raw JSON was NOT saved (should be NULL)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM trust_messages WHERE train_id='999888777' AND actual_timestamp_ms=1705498800000")
        result = cursor.fetchone()
        conn.close()
        
        # The row should exist, but raw_json should be NULL
        assert result is not None, "Row should be inserted"
        assert result[0] is None, f"raw_json should be NULL, got: {result[0]}"
        
        print("✓ Test passed: TRUST message raw JSON NOT saved when disabled")
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    test_save_raw_json_enabled()
    test_save_raw_json_disabled()
    test_vstp_save_raw_json_enabled()
    test_vstp_save_raw_json_disabled()
    test_trust_message_save_raw_json_disabled()
    print("\n✓ All tests passed!")
