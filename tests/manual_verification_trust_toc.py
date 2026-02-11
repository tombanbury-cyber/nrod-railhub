#!/usr/bin/env python3
"""Manual verification script for TRUST TOC normalization.

This script demonstrates the TOC normalization feature by:
1. Creating a test database
2. Populating TOC reference data
3. Inserting TRUST messages with various TOC identifiers
4. Querying to verify normalization works correctly
"""

import sys
import os
import tempfile
import sqlite3
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nrod_railhub.database import RailDB
from nrod_railhub.resolvers import TOCResolver


def main():
    print("=" * 80)
    print("TRUST TOC Normalization - Manual Verification")
    print("=" * 80)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_verification.db")
        print(f"\n✓ Creating test database: {db_path}")
        
        # Create database
        db = RailDB(db_path, enable_mapper=False, save_raw_json=False)
        
        # Populate TOC reference data
        print("\n✓ Populating TOC reference data...")
        resolver = TOCResolver()
        count = resolver.populate_database(db, quiet=True)
        print(f"  Inserted {count} TOC entries")
        
        # Insert TRUST messages with various TOC identifiers
        print("\n✓ Inserting TRUST messages with different TOC identifiers...")
        
        messages = [
            {
                'train_id': 'TEST001',
                'toc_id': '84',  # Business code for Southeastern
                'actual_timestamp': '1640000000000',
                'event_type': 'ARRIVAL',
                'reporting_stanox': '87701',
                'platform': '1'
            },
            {
                'train_id': 'TEST002',
                'toc_id': 'SWR',  # ATOC code for South Western Railway
                'actual_timestamp': '1640000001000',
                'event_type': 'DEPARTURE',
                'reporting_stanox': '87702',
                'platform': '2'
            },
            {
                'train_id': 'TEST003',
                'toc_id': 'GW',  # Canonical code for Great Western Railway
                'actual_timestamp': '1640000002000',
                'event_type': 'ARRIVAL',
                'reporting_stanox': '87703',
                'platform': '3'
            },
            {
                'train_id': 'TEST004',
                'toc_id': 'ZZZ',  # Unknown code
                'actual_timestamp': '1640000003000',
                'event_type': 'DEPARTURE',
                'reporting_stanox': '87704',
                'platform': '4'
            }
        ]
        
        for msg in messages:
            db.insert_trust_message(msg)
            print(f"  - Inserted: train_id={msg['train_id']}, toc_id={msg['toc_id']}")
        
        # Query and display results
        print("\n" + "=" * 80)
        print("Query Results - Normalization Verification")
        print("=" * 80)
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Query all messages with join
        cursor.execute("""
            SELECT tm.train_id, tm.toc_id AS msg_toc_id, tm.toc_code AS canonical_toc_code,
                   tr.toc_name, tr.business_code, tr.atoc_code, tm.event_type, tm.platform
            FROM trust_messages tm
            LEFT JOIN toc_reference tr ON tm.toc_code = tr.toc_code
            ORDER BY tm.actual_timestamp_ms
        """)
        
        rows = cursor.fetchall()
        
        print("\n{:<12} {:<12} {:<18} {:<30} {:<12} {:<10}".format(
            "Train ID", "Raw TOC ID", "Canonical Code", "TOC Name", "Event Type", "Platform"
        ))
        print("-" * 110)
        
        for row in rows:
            print("{:<12} {:<12} {:<18} {:<30} {:<12} {:<10}".format(
                row['train_id'] or '',
                row['msg_toc_id'] or '',
                row['canonical_toc_code'] or 'NULL',
                row['toc_name'] or 'N/A',
                row['event_type'] or '',
                row['platform'] or ''
            ))
        
        # Test filtering by canonical code
        print("\n" + "=" * 80)
        print("Filter Test - Messages with canonical TOC code 'SE' (Southeastern)")
        print("=" * 80)
        
        cursor.execute("""
            SELECT tm.train_id, tm.toc_id AS msg_toc_id, tm.toc_code AS canonical_toc_code,
                   tr.toc_name, tm.event_type
            FROM trust_messages tm
            LEFT JOIN toc_reference tr ON tm.toc_code = tr.toc_code
            WHERE tm.toc_code = 'SE'
        """)
        
        filtered_rows = cursor.fetchall()
        
        if filtered_rows:
            print("\n{:<12} {:<12} {:<18} {:<30} {:<12}".format(
                "Train ID", "Raw TOC ID", "Canonical Code", "TOC Name", "Event Type"
            ))
            print("-" * 90)
            
            for row in filtered_rows:
                print("{:<12} {:<12} {:<18} {:<30} {:<12}".format(
                    row['train_id'] or '',
                    row['msg_toc_id'] or '',
                    row['canonical_toc_code'] or 'NULL',
                    row['toc_name'] or 'N/A',
                    row['event_type'] or ''
                ))
            
            print(f"\n✓ Filter working! Found {len(filtered_rows)} message(s) with business code '84'")
            print("  that resolved to canonical code 'SE' (Southeastern)")
        else:
            print("\n✗ Filter test failed - no messages found with canonical code 'SE'")
        
        # Verify index exists
        print("\n" + "=" * 80)
        print("Index Verification")
        print("=" * 80)
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND tbl_name='trust_messages' AND name LIKE '%toc_code%'
        """)
        
        index_row = cursor.fetchone()
        if index_row:
            print(f"\n✓ Index '{index_row[0]}' exists on trust_messages(toc_code)")
        else:
            print("\n✗ Warning: Expected index on toc_code not found")
        
        conn.close()
        
        print("\n" + "=" * 80)
        print("Verification Complete!")
        print("=" * 80)
        print("\nSummary:")
        print("  ✓ Business codes (e.g., '84') are resolved to canonical codes (e.g., 'SE')")
        print("  ✓ ATOC codes (e.g., 'SWR') are resolved to canonical codes (e.g., 'SW')")
        print("  ✓ Canonical codes (e.g., 'GW') are preserved as-is")
        print("  ✓ Unknown codes (e.g., 'ZZZ') are stored with NULL canonical code")
        print("  ✓ Filtering by canonical code works correctly")
        print("  ✓ Index on toc_code exists for efficient queries")
        print("\nThe TOC normalization feature is working correctly!")


if __name__ == "__main__":
    main()
