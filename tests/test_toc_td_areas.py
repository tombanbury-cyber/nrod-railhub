#!/usr/bin/env python3
"""Unit tests for TOC-TD area mapping functionality."""

import os
import tempfile

from nrod_railhub.database import RailDB


def test_toc_td_areas_schema_creation():
    """Test that toc_td_areas table is created with correct schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Check table exists
        cursor = db._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='toc_td_areas'")
        result = cursor.fetchone()
        assert result is not None, "toc_td_areas table should exist"
        
        # Check table structure
        cursor.execute("PRAGMA table_info(toc_td_areas)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        assert "id" in columns
        assert "toc_code" in columns
        assert "td_area" in columns
        assert "is_primary" in columns
        assert "source" in columns
        assert "confidence" in columns
        assert "effective_from" in columns
        assert "effective_to" in columns
        assert "created_by" in columns
        assert "created_at_ts" in columns
        assert "notes" in columns
        
        # Check indexes exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='toc_td_areas'")
        indexes = [row[0] for row in cursor.fetchall()]
        assert any("toc_code" in idx for idx in indexes), "Should have index on toc_code"
        assert any("td_area" in idx for idx in indexes), "Should have index on td_area"
        
        print("✓ toc_td_areas table created with correct schema")
        
    finally:
        os.unlink(db_path)


def test_upsert_toc_td_area():
    """Test inserting and updating TOC-TD area mappings."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert a mapping
        db.upsert_toc_td_area(
            toc_code="SW",
            td_area="EK",
            is_primary=True,
            source="test",
            confidence=0.95,
            notes="Test mapping"
        )
        
        # Verify it was inserted
        mappings = db.get_toc_td_areas()
        assert len(mappings) == 1
        assert mappings[0]["toc_code"] == "SW"
        assert mappings[0]["td_area"] == "EK"
        assert mappings[0]["is_primary"] is True
        assert mappings[0]["source"] == "test"
        assert mappings[0]["confidence"] == 0.95
        assert mappings[0]["notes"] == "Test mapping"
        
        # Update the mapping
        db.upsert_toc_td_area(
            toc_code="SW",
            td_area="EK",
            is_primary=False,
            source="test_update",
            notes="Updated mapping"
        )
        
        # Verify it was updated (not duplicated)
        mappings = db.get_toc_td_areas()
        assert len(mappings) == 1
        assert mappings[0]["is_primary"] is False
        assert mappings[0]["source"] == "test_update"
        assert mappings[0]["notes"] == "Updated mapping"
        
        print("✓ upsert_toc_td_area works correctly")
        
    finally:
        os.unlink(db_path)


def test_delete_toc_td_area():
    """Test deleting TOC-TD area mappings."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert two mappings
        db.upsert_toc_td_area(toc_code="SW", td_area="EK")
        db.upsert_toc_td_area(toc_code="SW", td_area="WK")
        
        # Verify both were inserted
        mappings = db.get_toc_td_areas()
        assert len(mappings) == 2
        
        # Delete one mapping
        db.delete_toc_td_area(toc_code="SW", td_area="EK")
        
        # Verify only one remains
        mappings = db.get_toc_td_areas()
        assert len(mappings) == 1
        assert mappings[0]["td_area"] == "WK"
        
        print("✓ delete_toc_td_area works correctly")
        
    finally:
        os.unlink(db_path)


def test_get_td_areas_for_toc():
    """Test retrieving TD areas for a specific TOC."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert mappings for multiple TOCs
        db.upsert_toc_td_area(toc_code="SW", td_area="EK")
        db.upsert_toc_td_area(toc_code="SW", td_area="WK")
        db.upsert_toc_td_area(toc_code="GW", td_area="P1")
        
        # Get TD areas for SW
        sw_areas = db.get_td_areas_for_toc("SW")
        assert len(sw_areas) == 2
        assert all(m["toc_code"] == "SW" for m in sw_areas)
        td_areas = [m["td_area"] for m in sw_areas]
        assert "EK" in td_areas
        assert "WK" in td_areas
        
        # Get TD areas for GW
        gw_areas = db.get_td_areas_for_toc("GW")
        assert len(gw_areas) == 1
        assert gw_areas[0]["toc_code"] == "GW"
        assert gw_areas[0]["td_area"] == "P1"
        
        # Get TD areas for non-existent TOC
        empty_areas = db.get_td_areas_for_toc("XX")
        assert len(empty_areas) == 0
        
        print("✓ get_td_areas_for_toc works correctly")
        
    finally:
        os.unlink(db_path)


def test_toc_td_area_unique_constraint():
    """Test that the UNIQUE constraint on (toc_code, td_area) works."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert a mapping
        db.upsert_toc_td_area(toc_code="SW", td_area="EK", notes="First")
        
        # Insert same mapping with different notes - should update, not duplicate
        db.upsert_toc_td_area(toc_code="SW", td_area="EK", notes="Second")
        
        # Verify only one mapping exists with updated notes
        mappings = db.get_toc_td_areas()
        assert len(mappings) == 1
        assert mappings[0]["notes"] == "Second"
        
        print("✓ UNIQUE constraint on (toc_code, td_area) enforced correctly")
        
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    test_toc_td_areas_schema_creation()
    test_upsert_toc_td_area()
    test_delete_toc_td_area()
    test_get_td_areas_for_toc()
    test_toc_td_area_unique_constraint()
    print("\n✅ All TOC-TD area mapping tests passed!")
