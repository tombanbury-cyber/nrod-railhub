#!/usr/bin/env python3
"""Unit tests for HumanView.match_td_to_schedule method."""

import datetime
from unittest.mock import Mock

from nrod_railhub.views import HumanView
from nrod_railhub.models import VstpSchedule, ItpsSchedule, TrustState, TdState


def test_match_via_trust_uid_vstp():
    """Test matching via TRUST train_uid to VSTP schedule."""
    # Create HumanView with mock resolvers
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup VSTP schedule
    vs = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    hv.vstp_by_uid_date[("C12345", "2026-01-17")] = vs
    hv.vstp_by_headcode["2C90"] = vs

    # Setup TRUST state with train_uid
    ts = TrustState(
        train_id="123456789",
        train_uid="C12345",
        activated=True,
    )
    hv.trust_by_headcode["2C90"] = ts

    # Setup TD state
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:30:00Z"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Test matching
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "matched via TRUST train_uid C12345" in reason
    # UID-based matches don't use TIPLOC index, so matched_info is None
    assert matched_info is None


def test_match_via_trust_uid_itps():
    """Test matching via TRUST train_uid to ITPS schedule."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup ITPS schedule
    itps = ItpsSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    hv.sched_by_uid_date[("C12345", "2026-01-17")] = itps
    hv.sched_by_headcode["2C90"] = itps

    # Setup TRUST state
    ts = TrustState(
        train_id="123456789",
        train_uid="C12345",
        activated=True,
    )
    hv.trust_by_headcode["2C90"] = ts

    # Setup TD state
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:30:00Z"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Test matching
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is itps
    assert "matched timetable via TRUST train_uid C12345" in reason
    assert matched_info is None


def test_match_via_smart_stanox():
    """Test matching via SMART berth -> STANOX -> station name."""
    # Create mocks
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup VSTP schedule with locations
    vs1 = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs2 = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("VICTRIC", "15:45", "")]
    )
    hv.vstp_by_headcode["2C90"] = vs1  # First candidate

    # Setup TD state
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:30:00Z"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock SMART lookup to return a STANOX
    smart.lookup.return_value = {"stanox": "87701", "platform": "3"}
    
    # Mock resolver to return station name for STANOX
    resolver.name_for_stanox.return_value = "Clapham Junction"
    
    # Mock resolver to return matching station name for TIPLOC
    def mock_name_for_tiploc(tiploc):
        if tiploc == "CLPHMJC":
            return "Clapham Junction"
        elif tiploc == "VICTRIC":
            return "Victoria"
        elif tiploc == "MARGAT":
            return "Margate"
        return None
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Mock tiploc_to_name for TIPLOC index lookup
    resolver.tiploc_to_name = {
        "CLPHMJC": "Clapham Junction",
        "VICTRIC": "Victoria",
        "MARGAT": "Margate"
    }

    # Test matching
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    # Should match via TIPLOC index now (new behavior)
    assert sched is vs1
    assert "TIPLOC index" in reason or "SMART stanox" in reason
    smart.lookup.assert_called_with("EK", "0152")


def test_match_via_time_proximity():
    """Test matching via time proximity fallback."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup two VSTP schedules with different departure times
    vs1 = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs2 = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("VICTRIC", "15:45", "")]
    )
    hv.vstp_by_headcode["2C90"] = vs1  # First candidate

    # Setup TD state with time closer to vs1
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:29:00+00:00"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock SMART to return None (no match)
    smart.lookup.return_value = None

    # Test matching - should pick vs1 based on time proximity
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs1
    assert "matched by time proximity" in reason
    assert matched_info is None


def test_match_ambiguous_fallback():
    """Test ambiguous fallback when no clear match."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup VSTP schedule
    vs = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    hv.vstp_by_headcode["2C90"] = vs

    # Setup TD state without time
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc=""  # No time available
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock SMART to return None
    smart.lookup.return_value = None

    # Test matching - should return first candidate as ambiguous
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "ambiguous" in reason.lower()
    assert matched_info is None


def test_match_no_candidates():
    """Test when no candidate schedules exist."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup TD state but no schedules
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:30:00Z"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock smart to return None to avoid TIPLOC index path
    smart.lookup.return_value = None

    # Test matching
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is None
    assert "no candidate schedules" in reason
    assert matched_info is None


def test_match_without_td_state():
    """Test matching when TD state doesn't exist."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup VSTP schedule but no TD state
    vs = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    hv.vstp_by_headcode["2C90"] = vs

    # Test matching without TD state - should still return schedule
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "ambiguous" in reason.lower()
    assert matched_info is None


def test_match_prefers_uid_over_stanox():
    """Test that UID-based matching is preferred over STANOX matching."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup two VSTP schedules
    vs_correct = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs_wrong = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "14:30", "14:31"), ("MARGAT", "15:45", "")]
    )
    
    hv.vstp_by_uid_date[("C12345", "2026-01-17")] = vs_correct
    hv.vstp_by_headcode["2C90"] = vs_wrong

    # Setup TRUST state with UID pointing to vs_correct
    ts = TrustState(
        train_id="123456789",
        train_uid="C12345",
        activated=True,
    )
    hv.trust_by_headcode["2C90"] = ts

    # Setup TD state
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_utc="2026-01-17T12:30:00Z"
    )
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock SMART and resolver to potentially match vs_wrong via STANOX
    smart.lookup.return_value = {"stanox": "87701"}
    resolver.name_for_stanox.return_value = "Clapham Junction"
    resolver.name_for_tiploc.return_value = "Clapham Junction"

    # Test matching - should prefer UID match (vs_correct) over STANOX
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs_correct
    assert "TRUST train_uid C12345" in reason
    # UID-based matching doesn't use TIPLOC index
    assert matched_info is None


def test_tiploc_index_matching_with_reused_headcode():
    """Test TIPLOC index correctly matches schedules when headcode is reused.
    
    This is the core new feature: when two schedules use the same headcode but
    serve different routes, the TIPLOC index should match the one that actually
    calls at the TD-resolved station.
    """
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup two ITPS schedules with same headcode but different routes
    # Schedule 1: King's Cross → Peterborough via Stevenage
    sched_kx_pboro = ItpsSchedule(
        uid="P12345",
        signalling_id="1P42",
        start_date="2026-01-17",
        end_date="2026-01-17",
        days_run="1111111",
        stp_indicator="P",
        locations=[
            ("KNGX", "08:00", "08:05"),  # King's Cross
            ("SVNSTGE", "08:25", "08:26"),  # Stevenage
            ("PBORO", "08:50", "")  # Peterborough
        ]
    )
    
    # Schedule 2: Ramsgate → Victoria via Clapham Junction
    sched_ram_vic = ItpsSchedule(
        uid="R67890",
        signalling_id="1P42",
        start_date="2026-01-17",
        end_date="2026-01-17",
        days_run="1111111",
        stp_indicator="P",
        locations=[
            ("RAMSGTE", "09:00", "09:05"),  # Ramsgate
            ("CLPHMJC", "10:25", "10:26"),  # Clapham Junction
            ("VICTRIC", "10:40", "")  # Victoria
        ]
    )
    
    # Both schedules are valid, first one is stored in headcode index
    hv.sched_by_headcode["1P42"] = sched_kx_pboro
    hv.sched_by_uid_date[("P12345", "2026-01-17")] = sched_kx_pboro
    hv.sched_by_uid_date[("R67890", "2026-01-17")] = sched_ram_vic
    
    # Populate TIPLOC index manually (simulating load_schedule_gz)
    for stop_idx, loc_tuple in enumerate(sched_kx_pboro.locations):
        tiploc = loc_tuple[0].strip().upper()
        planned_hhmm = loc_tuple[2] or loc_tuple[1]
        if tiploc not in hv.schedules_by_tiploc:
            hv.schedules_by_tiploc[tiploc] = []
        hv.schedules_by_tiploc[tiploc].append((sched_kx_pboro, stop_idx, planned_hhmm))
    
    for stop_idx, loc_tuple in enumerate(sched_ram_vic.locations):
        tiploc = loc_tuple[0].strip().upper()
        planned_hhmm = loc_tuple[2] or loc_tuple[1]
        if tiploc not in hv.schedules_by_tiploc:
            hv.schedules_by_tiploc[tiploc] = []
        hv.schedules_by_tiploc[tiploc].append((sched_ram_vic, stop_idx, planned_hhmm))
    
    # Setup TD state: train 1P42 observed at Clapham Junction
    td = TdState(
        descr="1P42",
        area_id="VL",  # Victoria area
        to_berth="0152",
        last_time_utc="2026-01-17T10:25:00+00:00"
    )
    hv.td_by_headcode[("VL", "1P42")] = td
    
    # Mock SMART to resolve berth to Clapham Junction STANOX
    smart.lookup.return_value = {"stanox": "87701", "platform": "3"}
    
    # Mock resolver to return station name for STANOX
    resolver.name_for_stanox.return_value = "Clapham Junction"
    
    # Mock tiploc_to_name for TIPLOC index lookup
    resolver.tiploc_to_name = {
        "KNGX": "London Kings Cross",
        "SVNSTGE": "Stevenage",
        "PBORO": "Peterborough",
        "RAMSGTE": "Ramsgate",
        "CLPHMJC": "Clapham Junction",
        "VICTRIC": "London Victoria"
    }
    
    # Test matching - should select Ramsgate→Victoria schedule via TIPLOC index
    sched, reason, matched_info = hv.match_td_to_schedule("VL", "1P42", trace=True)
    
    # Assertions
    assert sched is sched_ram_vic, f"Should match Ramsgate→Victoria schedule, got UID {getattr(sched, 'uid', None)}"
    assert "TIPLOC index" in reason, f"Should match via TIPLOC index, got: {reason}"
    assert matched_info is not None, "Should return matched_info for TIPLOC matches"
    assert matched_info["matched_tiploc"] == "CLPHMJC"
    assert matched_info["stop_index"] == 1  # Clapham Junction is 2nd stop (index 1)
    assert "10:2" in matched_info["planned_dt"]  # Should be around 10:25


def test_tiploc_index_populated_by_load_schedule():
    """Test that load_schedule_gz populates the TIPLOC index correctly."""
    import tempfile
    import gzip
    import json
    from datetime import datetime, timezone
    
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create a minimal schedule JSON
    schedule_data = {
        "JsonScheduleV1": {
            "transaction_type": "Create",
            "CIF_train_uid": "TEST123",
            "schedule_start_date": "2026-01-17",
            "schedule_end_date": "2026-01-18",
            "schedule_days_runs": "1111111",
            "CIF_stp_indicator": "P",
            "schedule_segment": {
                "signalling_id": "2C90",
                "schedule_location": [
                    {
                        "tiploc_code": "CLPHMJC",
                        "public_departure": "123100"  # Use public_departure in HHMM format
                    },
                    {
                        "tiploc_code": "VICTRIC",
                        "public_arrival": "124500"  # Use public_arrival in HHMM format
                    }
                ]
            }
        }
    }
    
    # Write to temporary gzip file
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.json.gz', delete=False) as f:
        temp_path = f.name
        with gzip.open(f, 'wt', encoding='utf-8') as gz:
            gz.write(json.dumps(schedule_data) + '\n')
    
    try:
        # Load the schedule
        hv.load_schedule_gz(temp_path, service_date="2026-01-17", quiet=True)
        
        # Verify TIPLOC index was populated
        assert "CLPHMJC" in hv.schedules_by_tiploc, "CLPHMJC should be in TIPLOC index"
        assert "VICTRIC" in hv.schedules_by_tiploc, "VICTRIC should be in TIPLOC index"
        
        # Check CLPHMJC entry
        clphmjc_entries = hv.schedules_by_tiploc["CLPHMJC"]
        assert len(clphmjc_entries) >= 1, "Should have at least one entry for CLPHMJC"
        sched_obj, stop_idx, planned_hhmm = clphmjc_entries[0]
        assert stop_idx == 0, "CLPHMJC should be first stop (index 0)"
        assert planned_hhmm == "12:31", "Should use departure time 12:31"
        
        # Check VICTRIC entry
        victric_entries = hv.schedules_by_tiploc["VICTRIC"]
        assert len(victric_entries) >= 1, "Should have at least one entry for VICTRIC"
        sched_obj, stop_idx, planned_hhmm = victric_entries[0]
        assert stop_idx == 1, "VICTRIC should be second stop (index 1)"
        assert planned_hhmm == "12:45", "Should use arrival time 12:45"
        
    finally:
        import os
        os.unlink(temp_path)
