#!/usr/bin/env python3
"""Test double-encoded JSON handling for SMART and CORPUS reference data."""

import json
import os
import sys
import tempfile

# Add import_scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
from nrod_ref_import import read_json_file


def test_double_encoded_smart():
    """Test that double-encoded SMART JSON is properly handled."""
    
    # Create a mock SMART data structure
    smart_data = {
        "BERTHDATA": [
            {
                "TD": "EK",
                "FROMBERTH": "0152",
                "TOBERTH": "0153",
                "STANOX": "87701",
                "STANME": "GILLINGHAM (KENT)",
                "PLATFORM": "1",
                "EVENT": "A",
                "STEPTYPE": "B",
            }
        ]
    }
    
    # Create a double-encoded version (JSON string within JSON)
    double_encoded = json.dumps(json.dumps(smart_data))
    
    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(double_encoded)
        temp_file = f.name
    
    try:
        # Read and parse the double-encoded file
        result = read_json_file(temp_file)
        
        # Verify it was decoded correctly
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "BERTHDATA" in result, "Missing BERTHDATA key"
        assert isinstance(result["BERTHDATA"], list), "BERTHDATA should be a list"
        assert len(result["BERTHDATA"]) == 1, "Should have 1 berth entry"
        
        # Verify the data content
        berth = result["BERTHDATA"][0]
        assert berth["TD"] == "EK"
        assert berth["STANOX"] == "87701"
        assert berth["FROMBERTH"] == "0152"
        
        print("✓ Double-encoded SMART JSON handled correctly")
        
    finally:
        os.unlink(temp_file)


def test_normal_encoded_smart():
    """Test that normally-encoded SMART JSON still works."""
    
    # Create normal (single-encoded) SMART data
    smart_data = {
        "BERTHDATA": [
            {
                "TD": "AD",
                "FROMBERTH": "5021",
                "TOBERTH": "5061",
                "STANOX": "01001",
                "STANME": "ASHFORD",
            }
        ]
    }
    
    # Write as normal JSON
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(smart_data, f)
        temp_file = f.name
    
    try:
        # Read and parse the normal file
        result = read_json_file(temp_file)
        
        # Verify it was decoded correctly
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "BERTHDATA" in result, "Missing BERTHDATA key"
        assert len(result["BERTHDATA"]) == 1, "Should have 1 berth entry"
        
        berth = result["BERTHDATA"][0]
        assert berth["TD"] == "AD"
        
        print("✓ Normal SMART JSON handled correctly")
        
    finally:
        os.unlink(temp_file)


def test_double_encoded_corpus():
    """Test that double-encoded CORPUS JSON is properly handled."""
    
    # Create mock CORPUS data
    corpus_data = {
        "TIPLOCDATA": [
            {
                "TIPLOC": "CLPHMJC",
                "STANOX": "87701",
                "3ALPHA": "CLJ",
                "NLCDESC": "CLAPHAM JUNCTION",
            }
        ]
    }
    
    # Create double-encoded version
    double_encoded = json.dumps(json.dumps(corpus_data))
    
    # Write to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(double_encoded)
        temp_file = f.name
    
    try:
        # Read and parse
        result = read_json_file(temp_file)
        
        # Verify
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "TIPLOCDATA" in result, "Missing TIPLOCDATA key"
        assert len(result["TIPLOCDATA"]) == 1, "Should have 1 tiploc entry"
        
        tiploc = result["TIPLOCDATA"][0]
        assert tiploc["TIPLOC"] == "CLPHMJC"
        assert tiploc["STANOX"] == "87701"
        
        print("✓ Double-encoded CORPUS JSON handled correctly")
        
    finally:
        os.unlink(temp_file)


def test_malformed_double_encoding():
    """Test that malformed double-encoding doesn't crash."""
    
    # Create a string that looks double-encoded but isn't valid JSON inside
    malformed = json.dumps("not valid json {]")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write(malformed)
        temp_file = f.name
    
    try:
        # Should return the string (failed second parse)
        result = read_json_file(temp_file)
        
        # Result should be the string, not parsed further
        assert isinstance(result, str), f"Expected str for malformed data, got {type(result)}"
        assert result == "not valid json {]"
        
        print("✓ Malformed double-encoding handled gracefully")
        
    finally:
        os.unlink(temp_file)


if __name__ == "__main__":
    print("Testing double-encoded JSON handling...")
    print()
    
    test_double_encoded_smart()
    test_normal_encoded_smart()
    test_double_encoded_corpus()
    test_malformed_double_encoding()
    
    print()
    print("All tests passed! ✅")
