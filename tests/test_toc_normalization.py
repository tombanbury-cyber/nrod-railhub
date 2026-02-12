#!/usr/bin/env python3
"""Test TOC identifier normalization for TRUST messages."""

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nrod_railhub.resolvers import TOCResolver
from nrod_railhub.database import RailDB
from nrod_railhub.views import HumanView


def test_resolve_toc_code_canonical():
    """Test that canonical 2-character codes are returned as-is."""
    resolver = TOCResolver()
    
    # Test canonical codes
    assert resolver.resolve_toc_code('SW') == 'SW'
    assert resolver.resolve_toc_code('GW') == 'GW'
    assert resolver.resolve_toc_code('XC') == 'XC'
    
    # Test case insensitivity
    assert resolver.resolve_toc_code('sw') == 'SW'
    assert resolver.resolve_toc_code('Gw') == 'GW'
    
    # Test with whitespace
    assert resolver.resolve_toc_code(' SW ') == 'SW'
    assert resolver.resolve_toc_code('  GW  ') == 'GW'


def test_resolve_toc_code_atoc():
    """Test that ATOC codes are already canonical (2-char)."""
    resolver = TOCResolver()
    
    # Test ATOC code mappings (2-char ATOC codes are canonical)
    assert resolver.resolve_toc_code('SW') == 'SW', "SW (ATOC) is canonical"
    assert resolver.resolve_toc_code('GW') == 'GW', "GW (ATOC) is canonical"
    assert resolver.resolve_toc_code('XC') == 'XC', "XC (ATOC) is canonical"
    assert resolver.resolve_toc_code('AW') == 'AW', "AW (ATOC) is canonical"
    assert resolver.resolve_toc_code('TP') == 'TP', "TP (ATOC) is canonical"
    assert resolver.resolve_toc_code('VT') == 'VT', "VT (ATOC) is canonical"
    
    # Test case insensitivity
    assert resolver.resolve_toc_code('sw') == 'SW'
    assert resolver.resolve_toc_code('gw') == 'GW'


def test_resolve_toc_code_business():
    """Test that numeric sector codes are mapped to canonical codes."""
    resolver = TOCResolver()
    
    # Test numeric sector code mappings (from TRUST messages)
    assert resolver.resolve_toc_code('84') == 'SW', "84 (sector_code) should map to SW (South Western Railway)"
    assert resolver.resolve_toc_code('25') == 'GW', "25 (sector_code) should map to GW (Great Western Railway)"
    assert resolver.resolve_toc_code('27') == 'XC', "27 (sector_code) should map to XC (CrossCountry)"
    assert resolver.resolve_toc_code('80') == 'SE', "80 (sector_code) should map to SE (Southeastern)"
    assert resolver.resolve_toc_code('20') == 'TP', "20 (sector_code) should map to TP (TransPennine Express)"
    assert resolver.resolve_toc_code('65') == 'VT', "65 (sector_code) should map to VT (Avanti West Coast)"
    
    # Test whitespace handling
    assert resolver.resolve_toc_code(' 84 ') == 'SW'
    assert resolver.resolve_toc_code('  25  ') == 'GW'


def test_resolve_toc_code_unknown():
    """Test that unknown codes return None."""
    resolver = TOCResolver()
    
    # Test unknown codes
    assert resolver.resolve_toc_code('ZZZ') is None
    assert resolver.resolve_toc_code('999') is None
    assert resolver.resolve_toc_code('UNKNOWN') is None
    
    # Test edge cases
    assert resolver.resolve_toc_code('') is None
    assert resolver.resolve_toc_code('   ') is None
    assert resolver.resolve_toc_code(None) is None


def test_atoc_and_business_indices_built():
    """Test that mapping indices are built correctly."""
    resolver = TOCResolver()
    
    # Check that indices exist and have entries
    assert len(resolver.atoc_to_canonical) > 0, "ATOC mapping should not be empty"
    assert len(resolver.sector_to_canonical) > 0, "Sector code mapping should not be empty"
    
    # Check specific mappings exist (ATOC codes are 2-char and already canonical)
    assert 'SW' in resolver.atoc_to_canonical
    assert 'GW' in resolver.atoc_to_canonical
    assert '84' in resolver.sector_to_canonical  # sector_code for SW
    assert '25' in resolver.sector_to_canonical  # sector_code for GW


def test_populate_database_with_atoc_and_business():
    """Test that database is populated with ATOC and business codes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        resolver = TOCResolver()
        count = resolver.populate_database(db, quiet=True)
        
        assert count > 0, "Should have inserted TOC entries"
        
        # Verify ATOC and business codes in database
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check South Western Railway
        cursor.execute("SELECT * FROM toc_reference WHERE toc_code='SW'")
        row = cursor.fetchone()
        assert row is not None
        assert row['atoc_code'] == 'SW', f"Expected ATOC code 'SW', got {row['atoc_code']}"
        assert row['business_code'] == 'HY', f"Expected business code 'HY', got {row['business_code']}"
        assert row['sector_code'] == '84', f"Expected sector code '84', got {row['sector_code']}"
        
        # Check Great Western Railway
        cursor.execute("SELECT * FROM toc_reference WHERE toc_code='GW'")
        row = cursor.fetchone()
        assert row is not None
        assert row['atoc_code'] == 'GW', f"Expected ATOC code 'GW', got {row['atoc_code']}"
        assert row['business_code'] == 'EF', f"Expected business code 'EF', got {row['business_code']}"
        assert row['sector_code'] == '25', f"Expected sector code '25', got {row['sector_code']}"
        
        # Check CrossCountry
        cursor.execute("SELECT * FROM toc_reference WHERE toc_code='XC'")
        row = cursor.fetchone()
        assert row is not None
        assert row['atoc_code'] == 'XC', f"Expected ATOC code 'XC', got {row['atoc_code']}"
        assert row['business_code'] == 'EH', f"Expected business code 'EH', got {row['business_code']}"
        assert row['sector_code'] == '27', f"Expected sector code '27', got {row['sector_code']}"
        
        conn.close()


def test_get_canonical_toc_code_database():
    """Test database lookup for canonical TOC codes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate database with TOC data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Test canonical code lookup (ATOC codes are already canonical)
        assert db.get_canonical_toc_code('SW') == 'SW'
        assert db.get_canonical_toc_code('GW') == 'GW'
        
        # Test ATOC code lookup (2-char, same as canonical)
        assert db.get_canonical_toc_code('SW') == 'SW'
        assert db.get_canonical_toc_code('GW') == 'GW'
        assert db.get_canonical_toc_code('XC') == 'XC'
        
        # Test sector code lookup (numeric codes from TRUST messages)
        assert db.get_canonical_toc_code('84') == 'SW'
        assert db.get_canonical_toc_code('25') == 'GW'
        assert db.get_canonical_toc_code('27') == 'XC'
        
        # Test case insensitivity
        assert db.get_canonical_toc_code('sw') == 'SW'
        assert db.get_canonical_toc_code('gw') == 'GW'
        
        # Test unknown codes
        assert db.get_canonical_toc_code('ZZZ') is None
        assert db.get_canonical_toc_code('999') is None


def test_trust_message_with_atoc_code():
    """Test that TRUST messages with ATOC codes are normalized."""
    resolver = TOCResolver()
    toc_resolver = TOCResolver()
    hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
    
    # Mock TRUST message with ATOC code (2-char)
    trust_msg = {
        'body': {
            'train_id': '123456',
            'toc_id': 'SW',  # ATOC code for South Western Railway (2-char)
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    
    st = hv.upsert_trust(trust_msg)
    
    assert st is not None
    assert st.toc_id == 'SW', f"Expected canonical code 'SW', got {st.toc_id}"


def test_trust_message_with_business_code():
    """Test that TRUST messages with numeric sector codes are normalized."""
    toc_resolver = TOCResolver()
    hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
    
    # Mock TRUST message with numeric sector code
    trust_msg = {
        'body': {
            'train_id': '789012',
            'toc_id': '84',  # Sector code for South Western Railway
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    
    st = hv.upsert_trust(trust_msg)
    
    assert st is not None
    assert st.toc_id == 'SW', f"Expected canonical code 'SW', got {st.toc_id}"


def test_trust_message_with_canonical_code():
    """Test that TRUST messages with canonical codes are unchanged."""
    toc_resolver = TOCResolver()
    hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
    
    # Mock TRUST message with canonical code
    trust_msg = {
        'body': {
            'train_id': '345678',
            'toc_id': 'GW',  # Already canonical
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    
    st = hv.upsert_trust(trust_msg)
    
    assert st is not None
    assert st.toc_id == 'GW', f"Expected canonical code 'GW', got {st.toc_id}"


def test_trust_message_with_unknown_code():
    """Test that TRUST messages with unknown codes fall back to raw value."""
    toc_resolver = TOCResolver()
    hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
    
    # Mock TRUST message with unknown code
    trust_msg = {
        'body': {
            'train_id': '111222',
            'toc_id': 'ZZZ',  # Unknown code
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    
    st = hv.upsert_trust(trust_msg)
    
    assert st is not None
    assert st.toc_id == 'ZZZ', f"Should preserve unknown code 'ZZZ', got {st.toc_id}"


def test_trust_message_without_resolver():
    """Test that TRUST messages work without TOC resolver."""
    hv = HumanView(resolver=None, smart=None, toc_resolver=None)
    
    # Mock TRUST message with ATOC code but no resolver
    trust_msg = {
        'body': {
            'train_id': '999888',
            'toc_id': 'SW',  # 2-char ATOC code
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    
    st = hv.upsert_trust(trust_msg)
    
    assert st is not None
    # Without resolver, should preserve raw value
    assert st.toc_id == 'SW', f"Without resolver, should preserve 'SW', got {st.toc_id}"


def test_trust_state_toc_join_with_atoc_code():
    """Test that TRUST state with ATOC code joins correctly to toc_reference."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Create HumanView with TOC resolver
        toc_resolver = TOCResolver()
        hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
        
        # Process TRUST message with ATOC code (2-char)
        trust_msg = {
            'body': {
                'train_id': '123456',
                'toc_id': 'SW',  # ATOC code (2-char, already canonical)
                'train_uid': 'C43876',
                'msg_type': '0001',
                'event_timestamp': '1640000000000'
            }
        }
        
        st = hv.upsert_trust(trust_msg)
        assert st.toc_id == 'SW', "TOC should be SW"
        
        # Insert into database
        db.upsert_trust(
            train_id=st.train_id,
            headcode='2C90',
            uid=st.train_uid,
            toc_id=st.toc_id,  # Should be 'SW'
            last_event_time=st.last_event_time,
            last_location='',
            last_delay_min=None,
            raw={'test': 'data'}
        )
        
        # Query with TOC join
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ts.train_id, ts.toc_id, tr.toc_name
            FROM trust_state ts
            LEFT JOIN toc_reference tr ON ts.toc_id = tr.toc_code
            WHERE ts.train_id = '123456'
        """)
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None, "Should find TRUST state"
        assert row['toc_id'] == 'SW', "Should have canonical TOC code"
        assert row['toc_name'] is not None, "Should have TOC name from join"
        assert 'South Western' in row['toc_name'], f"Expected 'South Western' in name, got {row['toc_name']}"


def test_trust_message_toc_join_with_business_code():
    """Test that TRUST message with sector code joins correctly to toc_reference."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC data
        resolver = TOCResolver()
        resolver.populate_database(db, quiet=True)
        
        # Create HumanView with TOC resolver
        toc_resolver = TOCResolver()
        hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
        
        # Process TRUST message with sector code (numeric)
        trust_msg = {
            'body': {
                'train_id': '789012',
                'toc_id': '25',  # Sector code for Great Western
                'msg_type': '0001',
                'event_timestamp': '1640000000000'
            }
        }
        
        st = hv.upsert_trust(trust_msg)
        assert st.toc_id == 'GW', "TOC should be normalized to GW"
        
        # Insert TRUST message into database
        db.insert_trust_message({
            'train_id': st.train_id,
            'actual_timestamp': '2024-01-01T12:00:00Z',
            'toc_id': st.toc_id,  # Should be normalized to 'GW'
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        })
        
        # Query with TOC join
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tm.train_id, tm.toc_id, tr.toc_name
            FROM trust_messages tm
            LEFT JOIN toc_reference tr ON tm.toc_id = tr.toc_code
            WHERE tm.train_id = '789012'
        """)
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None, "Should find TRUST message"
        assert row['toc_id'] == 'GW', "Should have canonical TOC code"
        assert row['toc_name'] is not None, "Should have TOC name from join"
        assert 'Great Western' in row['toc_name'], f"Expected 'Great Western' in name, got {row['toc_name']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
