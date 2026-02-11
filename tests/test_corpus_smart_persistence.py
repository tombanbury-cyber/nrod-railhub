#!/usr/bin/env python3
"""Unit tests for CORPUS and SMART reference data persistence to database."""

import json
import os
import sqlite3
import tempfile

from nrod_railhub.database import RailDB
from nrod_railhub.resolvers import LocationResolver, SmartResolver


def test_corpus_schema_creation():
    """Test that corpus_locations table is created with correct schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Check table exists
        cursor = db._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='corpus_locations'")
        result = cursor.fetchone()
        assert result is not None, "corpus_locations table should exist"
        
        # Check table structure
        cursor.execute("PRAGMA table_info(corpus_locations)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        assert "tiploc" in columns
        assert "stanox" in columns
        assert "crs" in columns
        assert "nlc" in columns
        assert "name" in columns
        assert "raw_json" in columns
        assert "updated_at_utc" in columns
        
        print("✓ corpus_locations table created with correct schema")
        
    finally:
        os.unlink(db_path)


def test_smart_schema_creation():
    """Test that smart_berths table is created with correct schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Check table exists
        cursor = db._conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='smart_berths'")
        result = cursor.fetchone()
        assert result is not None, "smart_berths table should exist"
        
        # Check table structure
        cursor.execute("PRAGMA table_info(smart_berths)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        assert "td_area" in columns
        assert "berth" in columns
        assert "stanox" in columns
        assert "platform" in columns
        assert "event" in columns
        assert "stanme" in columns
        assert "raw_json" in columns
        assert "updated_at_utc" in columns
        
        print("✓ smart_berths table created with correct schema")
        
    finally:
        os.unlink(db_path)


def test_corpus_data_persistence():
    """Test that CORPUS data can be inserted and queried."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Sample CORPUS data
        corpus_data = [
            {
                "TIPLOC": "CLPHMJC",
                "STANOX": "87701",
                "3ALPHA": "CLJ",
                "NLC": "1234",
                "NLCDESC": "CLAPHAM JUNCTION"
            },
            {
                "TIPLOC": "VICTRIC",
                "STANOX": "87700",
                "3ALPHA": "VIC",
                "NLCDESC": "LONDON VICTORIA"
            },
            {
                "TIPLOC": "TEST",
                "STANOX": "",  # Empty stanox
                "3ALPHA": "",  # Empty CRS
                "NLCDESC": "TEST LOCATION"
            }
        ]
        
        # Populate database
        count = db.populate_corpus_data(corpus_data)
        assert count == 3, f"Expected 3 records inserted, got {count}"
        
        # Query by TIPLOC
        result = db.get_corpus_location(tiploc="CLPHMJC")
        assert result is not None
        assert result["tiploc"] == "CLPHMJC"
        assert result["stanox"] == "87701"
        assert result["crs"] == "CLJ"
        assert result["name"] == "CLAPHAM JUNCTION"
        
        # Query by STANOX
        result = db.get_corpus_location(stanox="87700")
        assert result is not None
        assert result["tiploc"] == "VICTRIC"
        assert result["name"] == "LONDON VICTORIA"
        
        # Query by CRS
        result = db.get_corpus_location(crs="VIC")
        assert result is not None
        assert result["tiploc"] == "VICTRIC"
        
        # Query non-existent location
        result = db.get_corpus_location(tiploc="NOTEXIST")
        assert result is None
        
        print("✓ CORPUS data persistence and retrieval works correctly")
        
    finally:
        os.unlink(db_path)


def test_corpus_data_upsert():
    """Test that CORPUS data can be updated (upsert behavior)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert initial data
        corpus_data_v1 = [
            {
                "TIPLOC": "CLPHMJC",
                "STANOX": "87701",
                "3ALPHA": "CLJ",
                "NLCDESC": "CLAPHAM JN"  # Old name
            }
        ]
        count = db.populate_corpus_data(corpus_data_v1)
        assert count == 1
        
        # Update with new data
        corpus_data_v2 = [
            {
                "TIPLOC": "CLPHMJC",
                "STANOX": "87701",
                "3ALPHA": "CLJ",
                "NLCDESC": "CLAPHAM JUNCTION"  # New name
            }
        ]
        count = db.populate_corpus_data(corpus_data_v2)
        assert count == 1
        
        # Verify update
        result = db.get_corpus_location(tiploc="CLPHMJC")
        assert result["name"] == "CLAPHAM JUNCTION"
        
        # Verify only one record exists
        cursor = db._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM corpus_locations WHERE tiploc='CLPHMJC'")
        count = cursor.fetchone()[0]
        assert count == 1, "Should have exactly one record after upsert"
        
        print("✓ CORPUS data upsert works correctly")
        
    finally:
        os.unlink(db_path)


def test_smart_data_persistence():
    """Test that SMART data can be inserted and queried."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Sample SMART data
        smart_data = [
            {
                "TD": "EK",
                "FROMBERTH": "0152",
                "TOBERTH": "0153",
                "STANOX": "87701",
                "STANME": "GILLINGHAM (KENT)",
                "PLATFORM": "1",
                "EVENT": "A",
                "STEPTYPE": "B"
            },
            {
                "TD": "AD",
                "FROMBERTH": "5021",
                "TOBERTH": "5061",
                "STANOX": "01001",
                "STANME": "ASHFORD",
                "PLATFORM": "2",
                "EVENT": "D"
            }
        ]
        
        # Populate database (each record creates 2 berth mappings: FROM and TO)
        count = db.populate_smart_data(smart_data)
        assert count == 4, f"Expected 4 berth records (2 per SMART row), got {count}"
        
        # Query berth data
        result = db.get_smart_berth("EK", "0152")
        assert result is not None
        assert result["stanox"] == "87701"
        assert result["platform"] == "1"
        assert result["event"] == "A"
        assert result["stanme"] == "GILLINGHAM (KENT)"
        
        result = db.get_smart_berth("EK", "0153")
        assert result is not None
        assert result["stanox"] == "87701"
        
        result = db.get_smart_berth("AD", "5021")
        assert result is not None
        assert result["stanme"] == "ASHFORD"
        
        # Query non-existent berth
        result = db.get_smart_berth("XX", "9999")
        assert result is None
        
        print("✓ SMART data persistence and retrieval works correctly")
        
    finally:
        os.unlink(db_path)


def test_smart_data_case_insensitive():
    """Test that SMART queries are case-insensitive."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert with uppercase
        smart_data = [
            {
                "TD": "EK",
                "FROMBERTH": "0152",
                "STANOX": "87701",
                "STANME": "TEST"
            }
        ]
        db.populate_smart_data(smart_data)
        
        # Query with lowercase should work
        result = db.get_smart_berth("ek", "0152")
        assert result is not None
        assert result["stanox"] == "87701"
        
        # Query with mixed case should work
        result = db.get_smart_berth("Ek", "0152")
        assert result is not None
        
        print("✓ SMART queries are case-insensitive")
        
    finally:
        os.unlink(db_path)


def test_resolver_corpus_integration():
    """Test that LocationResolver persists CORPUS data when db_path is provided."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_corpus:
        corpus_file = tmp_corpus.name
    
    try:
        # Create sample CORPUS file
        corpus_data = {
            "TIPLOCDATA": [
                {
                    "TIPLOC": "CLPHMJC",
                    "STANOX": "87701",
                    "3ALPHA": "CLJ",
                    "NLCDESC": "CLAPHAM JUNCTION"
                },
                {
                    "TIPLOC": "VICTRIC",
                    "STANOX": "87700",
                    "3ALPHA": "VIC",
                    "NLCDESC": "LONDON VICTORIA"
                }
            ]
        }
        
        with open(corpus_file, 'w') as f:
            json.dump(corpus_data, f)
        
        # Create resolver with db_path
        resolver = LocationResolver(db_path=db_path)
        resolver._load_corpus_file(corpus_file, quiet=True)
        
        # Verify in-memory data
        assert resolver.name_for_tiploc("CLPHMJC") == "CLAPHAM JUNCTION"
        assert resolver.name_for_crs("VIC") == "LONDON VICTORIA"
        
        # Verify database persistence
        db = RailDB(db_path, enable_mapper=False)
        result = db.get_corpus_location(tiploc="CLPHMJC")
        assert result is not None
        assert result["name"] == "CLAPHAM JUNCTION"
        
        result = db.get_corpus_location(crs="VIC")
        assert result is not None
        assert result["name"] == "LONDON VICTORIA"
        
        print("✓ LocationResolver persists CORPUS data to database")
        
    finally:
        os.unlink(db_path)
        os.unlink(corpus_file)


def test_resolver_corpus_without_db():
    """Test that LocationResolver works without db_path (backward compatibility)."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_corpus:
        corpus_file = tmp_corpus.name
    
    try:
        # Create sample CORPUS file
        corpus_data = {
            "TIPLOCDATA": [
                {
                    "TIPLOC": "CLPHMJC",
                    "STANOX": "87701",
                    "NLCDESC": "CLAPHAM JUNCTION"
                }
            ]
        }
        
        with open(corpus_file, 'w') as f:
            json.dump(corpus_data, f)
        
        # Create resolver WITHOUT db_path
        resolver = LocationResolver()
        resolver._load_corpus_file(corpus_file, quiet=True)
        
        # Verify in-memory data still works
        assert resolver.name_for_tiploc("CLPHMJC") == "CLAPHAM JUNCTION"
        
        print("✓ LocationResolver works without database (backward compatible)")
        
    finally:
        os.unlink(corpus_file)


def test_resolver_smart_integration():
    """Test that SmartResolver persists SMART data when db_path is provided."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_smart:
        smart_file = tmp_smart.name
    
    try:
        # Create sample SMART file
        smart_data = {
            "BERTHDATA": [
                {
                    "TD": "EK",
                    "FROMBERTH": "0152",
                    "TOBERTH": "0153",
                    "STANOX": "87701",
                    "STANME": "GILLINGHAM (KENT)",
                    "PLATFORM": "1",
                    "EVENT": "A"
                }
            ]
        }
        
        with open(smart_file, 'w') as f:
            json.dump(smart_data, f)
        
        # Create resolver with db_path
        smart = SmartResolver(db_path=db_path)
        smart._load_smart_file(smart_file, quiet=True)
        
        # Verify in-memory data
        result = smart.lookup("EK", "0152")
        assert result is not None
        assert result["stanox"] == "87701"
        
        # Verify database persistence
        db = RailDB(db_path, enable_mapper=False)
        result = db.get_smart_berth("EK", "0152")
        assert result is not None
        assert result["stanme"] == "GILLINGHAM (KENT)"
        
        print("✓ SmartResolver persists SMART data to database")
        
    finally:
        os.unlink(db_path)
        os.unlink(smart_file)


def test_corpus_double_encoding_with_persistence():
    """Test that double-encoded CORPUS JSON is persisted correctly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_corpus:
        corpus_file = tmp_corpus.name
    
    try:
        # Create double-encoded CORPUS data
        corpus_data = {
            "TIPLOCDATA": [
                {
                    "TIPLOC": "CLPHMJC",
                    "STANOX": "87701",
                    "NLCDESC": "CLAPHAM JUNCTION"
                }
            ]
        }
        double_encoded = json.dumps(json.dumps(corpus_data))
        
        with open(corpus_file, 'w') as f:
            f.write(double_encoded)
        
        # Create resolver with db_path
        resolver = LocationResolver(db_path=db_path)
        resolver._load_corpus_file(corpus_file, quiet=True)
        
        # Verify in-memory data
        assert resolver.name_for_tiploc("CLPHMJC") == "CLAPHAM JUNCTION"
        
        # Verify database persistence
        db = RailDB(db_path, enable_mapper=False)
        result = db.get_corpus_location(tiploc="CLPHMJC")
        assert result is not None
        assert result["name"] == "CLAPHAM JUNCTION"
        
        print("✓ Double-encoded CORPUS JSON persisted correctly")
        
    finally:
        os.unlink(db_path)
        os.unlink(corpus_file)


def test_smart_double_encoding_with_persistence():
    """Test that double-encoded SMART JSON is persisted correctly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_smart:
        smart_file = tmp_smart.name
    
    try:
        # Create double-encoded SMART data
        smart_data = {
            "BERTHDATA": [
                {
                    "TD": "EK",
                    "FROMBERTH": "0152",
                    "STANOX": "87701",
                    "STANME": "TEST"
                }
            ]
        }
        double_encoded = json.dumps(json.dumps(smart_data))
        
        with open(smart_file, 'w') as f:
            f.write(double_encoded)
        
        # Create resolver with db_path
        smart = SmartResolver(db_path=db_path)
        smart._load_smart_file(smart_file, quiet=True)
        
        # Verify in-memory data
        result = smart.lookup("EK", "0152")
        assert result is not None
        assert result["stanox"] == "87701"
        
        # Verify database persistence
        db = RailDB(db_path, enable_mapper=False)
        result = db.get_smart_berth("EK", "0152")
        assert result is not None
        assert result["stanme"] == "TEST"
        
        print("✓ Double-encoded SMART JSON persisted correctly")
        
    finally:
        os.unlink(db_path)
        os.unlink(smart_file)


if __name__ == "__main__":
    print("Testing CORPUS and SMART reference data persistence...")
    print()
    
    # Schema tests
    test_corpus_schema_creation()
    test_smart_schema_creation()
    
    # CORPUS persistence tests
    test_corpus_data_persistence()
    test_corpus_data_upsert()
    
    # SMART persistence tests
    test_smart_data_persistence()
    test_smart_data_case_insensitive()
    
    # Resolver integration tests
    test_resolver_corpus_integration()
    test_resolver_corpus_without_db()
    test_resolver_smart_integration()
    
    # Double encoding tests
    test_corpus_double_encoding_with_persistence()
    test_smart_double_encoding_with_persistence()
    
    print()
    print("All tests passed! ✅")
