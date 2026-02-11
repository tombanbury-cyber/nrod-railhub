#!/usr/bin/env python3
"""Unit tests for per-TOC schedule downloads and TIPLOC extraction."""

import gzip
import json
import os
import tempfile
from unittest.mock import Mock, patch

from nrod_railhub.resolvers import ScheduleResolver, TOCResolver, LocationResolver


def test_toc_resolver_get_business_code():
    """Test that TOCResolver can get business codes for TOC codes."""
    resolver = TOCResolver()
    
    # Test known TOCs with business codes (2-letter codes)
    assert resolver.get_business_code('SE') == 'HU'  # Southeastern
    assert resolver.get_business_code('SW') == 'HY'  # South Western Railway
    assert resolver.get_business_code('SN') == 'HW'  # Southern
    assert resolver.get_business_code('SR') == 'HA'  # ScotRail
    assert resolver.get_business_code('EM') == 'EM'  # East Midlands Railway
    assert resolver.get_business_code('VT') == 'HF'  # Avanti West Coast
    
    # Test TOC without business code
    assert resolver.get_business_code('EX') is None  # Express Passenger
    assert resolver.get_business_code('GW') is None  # Great Western Railway (no business code known)
    
    # Test non-existent TOC
    assert resolver.get_business_code('ZZZ') is None
    
    # Test case insensitivity
    assert resolver.get_business_code('se') == 'HU'
    assert resolver.get_business_code('sw') == 'HY'


def test_schedule_resolver_download_url_construction():
    """Test that TOC-specific download URLs are constructed correctly."""
    resolver = ScheduleResolver()
    
    # Mock the download method to capture the URL
    with patch.object(resolver, 'download') as mock_download:
        resolver.download_toc_schedule(
            username='test_user',
            password='test_pass',
            toc_code='SE',
            business_code='HU',  # 2-letter business code
            out_gz='/tmp/test.gz',
            update_mode=False,
            quiet=True,
        )
        
        # Verify the download was called with correct schedule_type
        mock_download.assert_called_once()
        call_kwargs = mock_download.call_args[1]
        assert call_kwargs['schedule_type'] == 'CIF_HU_TOC_FULL_DAILY'
        
    # Test UPDATE mode
    with patch.object(resolver, 'download') as mock_download:
        resolver.download_toc_schedule(
            username='test_user',
            password='test_pass',
            toc_code='SW',
            business_code='HY',  # South Western Railway
            out_gz='/tmp/test.gz',
            update_mode=True,
            quiet=True,
        )
        
        call_kwargs = mock_download.call_args[1]
        assert call_kwargs['schedule_type'] == 'CIF_HY_TOC_UPDATE_DAILY'


def test_extract_tiploc_data_from_schedule():
    """Test TIPLOC data extraction from schedule files."""
    resolver = ScheduleResolver()
    
    # Create a mock schedule file with TIPLOC data
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json.gz', delete=False) as f:
        temp_path = f.name
        with gzip.open(f, 'wt', encoding='utf-8') as gz:
            # Write TIPLOC records (various formats to test robustness)
            tiploc1 = {
                "TiplocV1": {
                    "tiploc_code": "CLPHMJC",
                    "nlc_description": "Clapham Junction",
                    "stanox": "87701",
                    "three_alpha": "CLJ"
                }
            }
            tiploc2 = {
                "TiplocV1": {
                    "tiploc_code": "VICTRIC",
                    "tps_description": "London Victoria",
                    "stanox": "87600",
                    "crs_code": "VIC"
                }
            }
            # Alternative format
            tiploc3 = {
                "tiploc_code": "WATERLO",
                "nlc_description": "London Waterloo",
                "stanox": "87650",
                "three_alpha": "WAT"
            }
            # Schedule record (should stop extraction)
            schedule = {
                "JsonScheduleV1": {
                    "CIF_train_uid": "C12345",
                    "signalling_id": "2C90"
                }
            }
            
            gz.write(json.dumps(tiploc1) + '\n')
            gz.write(json.dumps(tiploc2) + '\n')
            gz.write(json.dumps(tiploc3) + '\n')
            gz.write(json.dumps(schedule) + '\n')
    
    try:
        # Extract TIPLOC data
        tiploc_records = resolver.extract_tiploc_data(temp_path, quiet=True)
        
        # Verify extraction
        assert len(tiploc_records) == 3
        
        # Check first TIPLOC (TiplocV1 format with nlc_description)
        assert tiploc_records[0]['tiploc'] == 'CLPHMJC'
        assert tiploc_records[0]['name'] == 'Clapham Junction'
        assert tiploc_records[0]['stanox'] == '87701'
        assert tiploc_records[0]['crs'] == 'CLJ'
        
        # Check second TIPLOC (TiplocV1 format with tps_description)
        assert tiploc_records[1]['tiploc'] == 'VICTRIC'
        assert tiploc_records[1]['name'] == 'London Victoria'
        assert tiploc_records[1]['stanox'] == '87600'
        assert tiploc_records[1]['crs'] == 'VIC'
        
        # Check third TIPLOC (direct format)
        assert tiploc_records[2]['tiploc'] == 'WATERLO'
        assert tiploc_records[2]['name'] == 'London Waterloo'
        assert tiploc_records[2]['stanox'] == '87650'
        assert tiploc_records[2]['crs'] == 'WAT'
        
    finally:
        os.unlink(temp_path)


def test_extract_tiploc_data_stops_at_schedule():
    """Test that TIPLOC extraction stops when schedule records start."""
    resolver = ScheduleResolver()
    
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json.gz', delete=False) as f:
        temp_path = f.name
        with gzip.open(f, 'wt', encoding='utf-8') as gz:
            # Write 3 TIPLOC records
            for i in range(3):
                tiploc = {
                    "TiplocV1": {
                        "tiploc_code": f"TIP{i:03d}",
                        "nlc_description": f"Station {i}",
                        "stanox": f"8770{i}",
                    }
                }
                gz.write(json.dumps(tiploc) + '\n')
            
            # Write a schedule record
            schedule = {"JsonScheduleV1": {"CIF_train_uid": "C12345"}}
            gz.write(json.dumps(schedule) + '\n')
            
            # Write more TIPLOC records (should not be extracted)
            for i in range(3, 6):
                tiploc = {
                    "TiplocV1": {
                        "tiploc_code": f"TIP{i:03d}",
                        "nlc_description": f"Station {i}",
                    }
                }
                gz.write(json.dumps(tiploc) + '\n')
    
    try:
        tiploc_records = resolver.extract_tiploc_data(temp_path, quiet=True)
        # Should only extract the first 3 TIPLOCs before the schedule record
        assert len(tiploc_records) == 3
        assert tiploc_records[0]['tiploc'] == 'TIP000'
        assert tiploc_records[2]['tiploc'] == 'TIP002'
    finally:
        os.unlink(temp_path)


def test_location_resolver_add_tiploc_data():
    """Test adding TIPLOC data to LocationResolver."""
    resolver = LocationResolver()
    
    # Initially empty
    assert resolver.name_for_tiploc('CLPHMJC') == ''
    
    # Add TIPLOC data
    tiploc_records = [
        {
            'tiploc': 'CLPHMJC',
            'name': 'Clapham Junction',
            'stanox': '87701',
            'crs': 'CLJ'
        },
        {
            'tiploc': 'VICTRIC',
            'name': 'London Victoria',
            'stanox': '87600',
            'crs': 'VIC'
        },
    ]
    
    added = resolver.add_tiploc_data(tiploc_records, quiet=True)
    assert added == 2
    
    # Verify data was added
    assert resolver.name_for_tiploc('CLPHMJC') == 'Clapham Junction'
    assert resolver.name_for_tiploc('VICTRIC') == 'London Victoria'
    assert resolver.name_for_stanox('87701') == 'Clapham Junction'
    assert resolver.name_for_crs('CLJ') == 'Clapham Junction'
    assert resolver.stanox_for_tiploc('CLPHMJC') == '87701'
    
    # Adding duplicate should not increase count
    added = resolver.add_tiploc_data(tiploc_records, quiet=True)
    assert added == 0


def test_download_multiple_toc_schedules():
    """Test downloading multiple TOC schedules."""
    resolver = ScheduleResolver()
    toc_resolver = TOCResolver()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock the download_toc_schedule method
        with patch.object(resolver, 'download_toc_schedule') as mock_download:
            def side_effect(*args, **kwargs):
                # Create a dummy file
                out_gz = kwargs.get('out_gz')
                with gzip.open(out_gz, 'wt') as f:
                    f.write('{}')
            
            mock_download.side_effect = side_effect
            
            # Download schedules for multiple TOCs
            downloaded = resolver.download_multiple_toc_schedules(
                username='test',
                password='test',
                toc_filter=['SE', 'SW'],  # Changed from GW to SW since SW has business code HY
                toc_resolver=toc_resolver,
                cache_dir=tmpdir,
                update_mode=False,
                quiet=True,
            )
            
            # Verify two downloads
            assert len(downloaded) == 2
            assert downloaded[0][0] == 'SE'
            assert downloaded[1][0] == 'SW'
            
            # Verify files exist
            assert os.path.exists(downloaded[0][1])
            assert os.path.exists(downloaded[1][1])
            
            # Verify download was called twice
            assert mock_download.call_count == 2


def test_download_multiple_toc_schedules_skips_invalid():
    """Test that download_multiple_toc_schedules skips TOCs without business codes."""
    resolver = ScheduleResolver()
    toc_resolver = TOCResolver()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(resolver, 'download_toc_schedule') as mock_download:
            # Try to download with a mix of valid and invalid TOCs
            downloaded = resolver.download_multiple_toc_schedules(
                username='test',
                password='test',
                toc_filter=['SE', 'INVALID', 'EX'],  # EX has no business_code
                toc_resolver=toc_resolver,
                cache_dir=tmpdir,
                quiet=True,
            )
            
            # Should only download SE (INVALID doesn't exist, EX has no business_code)
            assert len(downloaded) == 1
            assert downloaded[0][0] == 'SE'
            assert mock_download.call_count == 1


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
