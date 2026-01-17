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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "matched via TRUST train_uid C12345" in reason


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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is itps
    assert "matched timetable via TRUST train_uid C12345" in reason


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

    # Test matching
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs1
    assert "matched by SMART stanox 87701 -> station name" in reason
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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs1
    assert "matched by time proximity" in reason


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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "ambiguous" in reason.lower()


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

    # Test matching
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is None
    assert "no candidate schedules" in reason


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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs
    assert "ambiguous" in reason.lower()


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
    sched, reason = hv.match_td_to_schedule("EK", "2C90")
    
    assert sched is vs_correct
    assert "TRUST train_uid C12345" in reason
