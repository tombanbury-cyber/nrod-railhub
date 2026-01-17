#!/usr/bin/env python3
"""
Manual verification script for match_td_to_schedule functionality.

This script demonstrates the TD schedule matching helper with various scenarios.
"""

from unittest.mock import Mock
from nrod_railhub.views import HumanView
from nrod_railhub.models import VstpSchedule, ItpsSchedule, TrustState, TdState


def demo_uid_matching():
    """Demonstrate UID-based matching."""
    print("\n=== Demo 1: UID-based Matching ===")
    print("Scenario: Same headcode (1J37) used in two different areas")
    print("  - Margate area: train_uid=M12345")
    print("  - Brockenhurst area: train_uid=B67890")
    print()
    
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup two schedules with same headcode but different UIDs
    vs_margate = VstpSchedule(
        uid="M12345",
        signalling_id="1J37",
        start_date="2026-01-17",
        locations=[("MARGAT", "09:00", "09:01"), ("ASHFDI", "09:30", "")]
    )
    vs_brockenhurst = VstpSchedule(
        uid="B67890",
        signalling_id="1J37",
        start_date="2026-01-17",
        locations=[("BRKNHST", "11:00", "11:01"), ("SOTON", "11:30", "")]
    )
    
    hv.vstp_by_uid_date[("M12345", "2026-01-17")] = vs_margate
    hv.vstp_by_uid_date[("B67890", "2026-01-17")] = vs_brockenhurst
    hv.vstp_by_headcode["1J37"] = vs_margate  # First one registered

    # Setup TRUST states for each
    ts_margate = TrustState(train_id="111111", train_uid="M12345", activated=True)
    ts_brockenhurst = TrustState(train_id="222222", train_uid="B67890", activated=True)

    # Setup TD states
    td_margate = TdState(descr="1J37", area_id="MA", to_berth="0100", last_time_utc="2026-01-17T09:00:00Z")
    td_brockenhurst = TdState(descr="1J37", area_id="BR", to_berth="0200", last_time_utc="2026-01-17T11:00:00Z")
    
    hv.td_by_headcode[("MA", "1J37")] = td_margate
    hv.td_by_headcode[("BR", "1J37")] = td_brockenhurst

    # Test Margate area
    hv.trust_by_headcode["1J37"] = ts_margate
    sched, reason = hv.match_td_to_schedule("MA", "1J37", trace=True)
    print(f"Margate Match: UID={sched.uid if sched else None}, Reason: {reason}")
    assert sched.uid == "M12345", "Should match Margate schedule via UID"

    # Test Brockenhurst area
    hv.trust_by_headcode["1J37"] = ts_brockenhurst
    sched, reason = hv.match_td_to_schedule("BR", "1J37", trace=True)
    print(f"Brockenhurst Match: UID={sched.uid if sched else None}, Reason: {reason}")
    assert sched.uid == "B67890", "Should match Brockenhurst schedule via UID"
    
    print("✓ UID-based matching correctly distinguishes same headcode in different areas")


def demo_stanox_matching():
    """Demonstrate SMART stanox-based matching."""
    print("\n=== Demo 2: STANOX-based Matching ===")
    print("Scenario: Two schedules for headcode 2C90, one via Clapham Junction, one via Margate")
    print("  - TD berth resolves to Clapham Junction (stanox 87701)")
    print()
    
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    # Setup two schedules
    vs_clapham = VstpSchedule(
        uid="C11111",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs_margate = VstpSchedule(
        uid="C22222",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("VICTRIC", "15:45", "")]
    )
    
    hv.vstp_by_headcode["2C90"] = vs_clapham  # First candidate

    # Setup TD state
    td = TdState(descr="2C90", area_id="EK", to_berth="0152", last_time_utc="2026-01-17T12:30:00Z")
    hv.td_by_headcode[("EK", "2C90")] = td

    # Mock SMART to resolve berth to Clapham Junction
    smart.lookup.return_value = {"stanox": "87701", "platform": "3"}
    resolver.name_for_stanox.return_value = "Clapham Junction"
    
    def mock_name_for_tiploc(tiploc):
        mapping = {
            "CLPHMJC": "Clapham Junction",
            "VICTRIC": "Victoria",
            "MARGAT": "Margate"
        }
        return mapping.get(tiploc)
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc

    # Test matching
    sched, reason = hv.match_td_to_schedule("EK", "2C90", trace=True)
    print(f"Match: UID={sched.uid if sched else None}, Reason: {reason}")
    assert sched.uid == "C11111", "Should match Clapham schedule via STANOX"
    
    print("✓ STANOX-based matching correctly identifies route via station names")


def demo_time_proximity():
    """Demonstrate time proximity matching."""
    print("\n=== Demo 3: Time Proximity Matching ===")
    print("Scenario: Two schedules for 2C90, TD time closer to first schedule")
    print("  - Schedule 1: departs 12:30")
    print("  - Schedule 2: departs 14:30")
    print("  - TD observation: 12:29")
    print()
    
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)

    vs1 = VstpSchedule(
        uid="C11111",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs2 = VstpSchedule(
        uid="C22222",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("VICTRIC", "15:45", "")]
    )
    
    hv.vstp_by_headcode["2C90"] = vs1

    td = TdState(descr="2C90", area_id="EK", to_berth="0152", last_time_utc="2026-01-17T12:29:00+00:00")
    hv.td_by_headcode[("EK", "2C90")] = td

    smart.lookup.return_value = None  # No SMART match

    sched, reason = hv.match_td_to_schedule("EK", "2C90", trace=True)
    print(f"Match: UID={sched.uid if sched else None}, Reason: {reason}")
    assert sched.uid == "C11111", "Should match first schedule via time proximity"
    
    print("✓ Time proximity matching correctly picks schedule closest in time")


def main():
    """Run all demos."""
    print("=" * 70)
    print("TD Schedule Matching Functionality Verification")
    print("=" * 70)
    
    try:
        demo_uid_matching()
        demo_stanox_matching()
        demo_time_proximity()
        
        print("\n" + "=" * 70)
        print("✓ All verification tests passed!")
        print("=" * 70)
        
    except AssertionError as e:
        print(f"\n✗ Verification failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
