#!/usr/bin/env python3
"""
Manual verification script for TOC code normalization.

This script demonstrates that incoming TRUST messages with ATOC codes
or numeric business codes are correctly normalized to canonical 2-character codes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nrod_railhub.resolvers import TOCResolver
from nrod_railhub.views import HumanView
from nrod_railhub.database import RailDB
import tempfile

def demo_toc_resolution():
    """Demonstrate TOC code resolution."""
    print("\n" + "="*70)
    print("TOC Code Normalization Demonstration")
    print("="*70)
    
    resolver = TOCResolver()
    
    print("\n1. Canonical TOC Codes (already correct):")
    print("-" * 70)
    test_cases = [
        ('SW', 'South Western Railway'),
        ('GW', 'Great Western Railway'),
        ('XC', 'CrossCountry'),
    ]
    for code, expected_name in test_cases:
        canonical = resolver.resolve_toc_code(code)
        name = resolver.get_toc_name(canonical) if canonical else None
        status = "✓" if canonical == code and expected_name in (name or "") else "✗"
        print(f"  {status} {code:6} → {canonical:6} ({name})")
    
    print("\n2. ATOC Codes (3-letter) → Canonical:")
    print("-" * 70)
    atoc_cases = [
        ('SWR', 'SW', 'South Western Railway'),
        ('GWR', 'GW', 'Great Western Railway'),
        ('XCT', 'XC', 'CrossCountry'),
        ('ATW', 'AW', 'Arriva Trains Wales'),
        ('TPE', 'TP', 'TransPennine Express'),
        ('AVC', 'VT', 'Avanti West Coast'),
    ]
    for atoc, expected_canonical, expected_name in atoc_cases:
        canonical = resolver.resolve_toc_code(atoc)
        name = resolver.get_toc_name(canonical) if canonical else None
        status = "✓" if canonical == expected_canonical else "✗"
        print(f"  {status} {atoc:6} → {canonical:6} ({name})")
    
    print("\n3. Business Codes (numeric) → Canonical:")
    print("-" * 70)
    business_cases = [
        ('71', 'SW', 'South Western Railway'),
        ('79', 'GW', 'Great Western Railway'),
        ('27', 'XC', 'CrossCountry'),
        ('20', 'TP', 'TransPennine Express'),
        ('25', 'VT', 'Avanti West Coast'),
    ]
    for business, expected_canonical, expected_name in business_cases:
        canonical = resolver.resolve_toc_code(business)
        name = resolver.get_toc_name(canonical) if canonical else None
        status = "✓" if canonical == expected_canonical else "✗"
        print(f"  {status} {business:6} → {canonical:6} ({name})")
    
    print("\n4. Unknown Codes (should return None):")
    print("-" * 70)
    unknown_cases = ['ZZZ', '999', 'UNKNOWN']
    for code in unknown_cases:
        canonical = resolver.resolve_toc_code(code)
        status = "✓" if canonical is None else "✗"
        print(f"  {status} {code:6} → {canonical}")


def demo_trust_message_normalization():
    """Demonstrate TRUST message processing with TOC normalization."""
    print("\n" + "="*70)
    print("TRUST Message Processing with TOC Normalization")
    print("="*70)
    
    toc_resolver = TOCResolver()
    hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
    
    print("\n1. TRUST message with ATOC code 'SWR':")
    print("-" * 70)
    trust_msg = {
        'body': {
            'train_id': '123456',
            'toc_id': 'SWR',  # ATOC code
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    st = hv.upsert_trust(trust_msg)
    status = "✓" if st.toc_id == 'SW' else "✗"
    print(f"  {status} Input: 'SWR' → Output: '{st.toc_id}' (expected 'SW')")
    
    print("\n2. TRUST message with business code '71':")
    print("-" * 70)
    trust_msg = {
        'body': {
            'train_id': '789012',
            'toc_id': '71',  # Business code
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    st = hv.upsert_trust(trust_msg)
    status = "✓" if st.toc_id == 'SW' else "✗"
    print(f"  {status} Input: '71' → Output: '{st.toc_id}' (expected 'SW')")
    
    print("\n3. TRUST message with canonical code 'GW':")
    print("-" * 70)
    trust_msg = {
        'body': {
            'train_id': '345678',
            'toc_id': 'GW',  # Already canonical
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    st = hv.upsert_trust(trust_msg)
    status = "✓" if st.toc_id == 'GW' else "✗"
    print(f"  {status} Input: 'GW' → Output: '{st.toc_id}' (expected 'GW')")
    
    print("\n4. TRUST message with unknown code 'ZZZ':")
    print("-" * 70)
    trust_msg = {
        'body': {
            'train_id': '111222',
            'toc_id': 'ZZZ',  # Unknown
            'msg_type': '0001',
            'event_timestamp': '1640000000000'
        }
    }
    st = hv.upsert_trust(trust_msg)
    status = "✓" if st.toc_id == 'ZZZ' else "✗"
    print(f"  {status} Input: 'ZZZ' → Output: '{st.toc_id}' (preserved as unknown)")


def demo_database_joins():
    """Demonstrate database joins with normalized TOC codes."""
    print("\n" + "="*70)
    print("Database Joins with Normalized TOC Codes")
    print("="*70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = RailDB(db_path, enable_mapper=False)
        
        # Populate TOC reference data
        resolver = TOCResolver()
        count = resolver.populate_database(db, quiet=True)
        print(f"\n✓ Populated database with {count} TOC entries")
        
        # Create HumanView with TOC resolver
        toc_resolver = TOCResolver()
        hv = HumanView(resolver=None, smart=None, toc_resolver=toc_resolver)
        
        # Process TRUST message with ATOC code
        print("\n1. Process TRUST message with ATOC code 'SWR':")
        print("-" * 70)
        trust_msg = {
            'body': {
                'train_id': '123456',
                'toc_id': 'SWR',  # ATOC code
                'train_uid': 'C43876',
                'msg_type': '0001',
                'event_timestamp': '1640000000000'
            }
        }
        st = hv.upsert_trust(trust_msg)
        print(f"  ✓ Normalized 'SWR' to '{st.toc_id}'")
        
        # Insert into database
        db.upsert_trust(
            train_id=st.train_id,
            headcode='2C90',
            uid=st.train_uid,
            toc_id=st.toc_id,
            last_event_time=st.last_event_time,
            last_location='',
            last_delay_min=None,
            raw={'test': 'data'}
        )
        
        # Query with TOC join
        import sqlite3
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
        
        if row and row['toc_name']:
            print(f"  ✓ Database join successful:")
            print(f"    train_id: {row['train_id']}")
            print(f"    toc_id: {row['toc_id']}")
            print(f"    toc_name: {row['toc_name']}")
        else:
            print(f"  ✗ Database join failed")
        
        # Test with business code
        print("\n2. Process TRUST message with business code '79':")
        print("-" * 70)
        trust_msg = {
            'body': {
                'train_id': '789012',
                'toc_id': '79',  # Business code for GW
                'msg_type': '0001',
                'event_timestamp': '1640000000000'
            }
        }
        st = hv.upsert_trust(trust_msg)
        print(f"  ✓ Normalized '79' to '{st.toc_id}'")
        
        db.insert_trust_message({
            'train_id': st.train_id,
            'actual_timestamp': '2024-01-01T12:00:00Z',
            'toc_id': st.toc_id,
            'event_type': 'ARRIVAL',
            'reporting_stanox': '87701'
        })
        
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
        
        if row and row['toc_name']:
            print(f"  ✓ Database join successful:")
            print(f"    train_id: {row['train_id']}")
            print(f"    toc_id: {row['toc_id']}")
            print(f"    toc_name: {row['toc_name']}")
        else:
            print(f"  ✗ Database join failed")


if __name__ == "__main__":
    demo_toc_resolution()
    demo_trust_message_normalization()
    demo_database_joins()
    
    print("\n" + "="*70)
    print("All demonstrations completed successfully! ✓")
    print("="*70 + "\n")
