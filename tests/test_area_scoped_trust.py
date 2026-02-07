#!/usr/bin/env python3
"""Unit tests for area-scoped TRUST indexing functionality."""

from unittest.mock import Mock

from nrod_railhub.views import HumanView
from nrod_railhub.resolvers import LocationResolver, SmartResolver
from nrod_railhub.models import VstpSchedule, TrustState, TdState, iso_to_ms


def test_stanox_for_tiploc():
    """Test LocationResolver.stanox_for_tiploc() method."""
    resolver = LocationResolver()
    
    # Manually populate the tiploc_to_stanox mapping
    resolver.tiploc_to_stanox = {
        "CLPHMJC": "87701",
        "VICTRIC": "87709",
        "MARGAT": "88600",
    }
    
    # Test lookup
    assert resolver.stanox_for_tiploc("CLPHMJC") == "87701"
    assert resolver.stanox_for_tiploc("VICTRIC") == "87709"
    assert resolver.stanox_for_tiploc("clphmjc") == "87701"  # Case-insensitive
    assert resolver.stanox_for_tiploc("  MARGAT  ") == "88600"  # Strips whitespace
    assert resolver.stanox_for_tiploc("UNKNOWN") is None


def test_area_from_stanox():
    """Test HumanView._area_from_stanox() reverse lookup."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Mock SMART berth_map
    smart.berth_map = {
        ("EK", "0152"): {"stanox": "87701", "platform": "3"},
        ("EK", "0153"): {"stanox": "87701", "platform": "4"},
        ("VL", "0100"): {"stanox": "87709", "platform": "1"},
        ("AD", "0200"): {"stanox": "88600"},
    }
    
    # Test reverse lookup
    assert hv._area_from_stanox("87701") == "EK"  # First match
    assert hv._area_from_stanox("87709") == "VL"
    assert hv._area_from_stanox("88600") == "AD"
    assert hv._area_from_stanox("99999") is None  # Not found


def test_area_from_stanox_no_smart():
    """Test _area_from_stanox returns None when SMART is not available."""
    resolver = Mock()
    hv = HumanView(resolver=resolver, smart=None)
    
    # Should return None gracefully
    assert hv._area_from_stanox("87701") is None


def test_schedule_passes_through_area_with_match():
    """Test _schedule_passes_through_area returns True when schedule calls at TD area."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule that calls at Clapham Junction
    vs = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    
    # Create TD state in EK area at berth 0152
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_ms=iso_to_ms("2026-01-17T12:30:00Z") or 0
    )
    
    # Mock SMART to resolve berth to Clapham Junction STANOX
    smart.lookup.return_value = {"stanox": "87701", "platform": "3"}
    
    # Mock resolver to return STANOX for TIPLOC
    resolver.stanox_for_tiploc.return_value = "87701"
    
    # Test - should match
    result = hv._schedule_passes_through_area(vs, "EK", td)
    assert result is True
    smart.lookup.assert_called_once_with("EK", "0152")
    resolver.stanox_for_tiploc.assert_called()


def test_schedule_passes_through_area_no_match():
    """Test _schedule_passes_through_area returns False when schedule doesn't call at area."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule that calls at Margate (different STANOX)
    vs = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("VICTRIC", "15:45", "")]
    )
    
    # Create TD state in EK area at berth 0152 (Clapham Junction)
    td = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_ms=iso_to_ms("2026-01-17T12:30:00Z") or 0
    )
    
    # Mock SMART to resolve berth to Clapham Junction STANOX
    smart.lookup.return_value = {"stanox": "87701", "platform": "3"}
    
    # Mock resolver to return different STANOX for Margate
    def mock_stanox(tiploc):
        if tiploc == "MARGAT":
            return "88600"
        elif tiploc == "VICTRIC":
            return "87709"
        return None
    
    resolver.stanox_for_tiploc.side_effect = mock_stanox
    
    # Test - should NOT match (schedule doesn't call at STANOX 87701)
    result = hv._schedule_passes_through_area(vs, "EK", td)
    assert result is False


def test_schedule_passes_through_area_default_true():
    """Test _schedule_passes_through_area defaults to True when validation can't be performed."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    vs = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31")]
    )
    
    # Mock name_for_tiploc to return empty string (no validation possible)
    resolver.name_for_tiploc.return_value = ""
    
    # Test with no TD state - should trigger keyword filtering for EK, but no station names available
    result = hv._schedule_passes_through_area(vs, "EK", None)
    # Since EK area has keyword filtering configured but no station names are available,
    # it should return False (no match found)
    assert result is False
    
    # Test with a non-configured area (no keyword filtering)
    result = hv._schedule_passes_through_area(vs, "XX", None)
    assert result is True  # Should default to True for unconfigured areas
    
    # Test with TD state but SMART lookup fails - should still apply keyword filtering for EK
    td = TdState(descr="2C90", area_id="EK", to_berth="0152")
    smart.lookup.return_value = None
    result = hv._schedule_passes_through_area(vs, "EK", td)
    assert result is False  # EK has keyword filtering, no match found


def test_upsert_trust_populates_area_index():
    """Test that upsert_trust populates the area-scoped TRUST index."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Mock SMART berth_map for area inference
    smart.berth_map = {
        ("EK", "0152"): {"stanox": "87701", "platform": "3"},
    }
    
    # Create TRUST activation message
    trust_msg = {
        "body": {
            "train_id": "123456789",
            "train_uid": "C12345",
            "train_reporting_number": "2C90",
            "msg_type": "0001",  # Activation
            "loc_stanox": "87701",  # At Clapham Junction
            "toc_id": "SW",
        }
    }
    
    # Process message
    result = hv.upsert_trust(trust_msg)
    
    # Verify TRUST state was created
    assert result is not None
    assert result.train_id == "123456789"
    assert result.train_uid == "C12345"
    
    # Verify global index was populated
    assert "2C90" in hv.trust_by_headcode
    assert hv.trust_by_headcode["2C90"].train_id == "123456789"
    
    # Verify area-scoped index was populated
    assert ("EK", "2C90") in hv.trust_by_area_headcode
    assert hv.trust_by_area_headcode[("EK", "2C90")].train_id == "123456789"


def test_area_scoped_trust_match_priority():
    """Test that area-scoped TRUST lookup takes priority over global lookup."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Setup two VSTP schedules with same headcode but different UIDs
    vs_ek = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    vs_ad = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("RAMSGTE", "15:45", "")]
    )
    
    hv.vstp_by_uid_date[("C12345", "2026-01-17")] = vs_ek
    hv.vstp_by_uid_date[("C67890", "2026-01-17")] = vs_ad
    hv.vstp_by_headcode["2C90"] = [vs_ek, vs_ad]
    
    # Setup TRUST states
    ts_ek = TrustState(train_id="111111111", train_uid="C12345", activated=True)
    ts_ad = TrustState(train_id="222222222", train_uid="C67890", activated=True)
    
    # Global index points to one train (ambiguous)
    hv.trust_by_headcode["2C90"] = ts_ek
    
    # Area-scoped index disambiguates
    hv.trust_by_area_headcode[("EK", "2C90")] = ts_ek
    hv.trust_by_area_headcode[("AD", "2C90")] = ts_ad
    
    # Test matching in EK area - should use area-scoped index
    sched, reason, matched_info = hv.match_td_to_schedule("EK", "2C90")
    assert sched is vs_ek
    assert "area-scoped TRUST" in reason
    assert "C12345" in reason
    
    # Test matching in AD area - should use area-scoped index
    sched, reason, matched_info = hv.match_td_to_schedule("AD", "2C90")
    assert sched is vs_ad
    assert "area-scoped TRUST" in reason
    assert "C67890" in reason


def test_two_trains_same_headcode_different_areas():
    """Integration test: two trains with same headcode in different TD areas.
    
    This is the main use case: ensuring that when headcode 2C90 is reused by
    two different trains in different regions, we correctly match each TD
    observation to the right schedule.
    """
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Setup mock SMART berth_map for both areas
    smart.berth_map = {
        ("EK", "0152"): {"stanox": "87701"},  # Clapham Junction
        ("AD", "0200"): {"stanox": "88600"},  # Margate
    }
    
    # Setup mock resolver
    def mock_stanox_for_tiploc(tiploc):
        mapping = {
            "CLPHMJC": "87701",
            "VICTRIC": "87709",
            "MARGAT": "88600",
            "RAMSGTE": "88601",
        }
        return mapping.get(tiploc.upper())
    
    resolver.stanox_for_tiploc.side_effect = mock_stanox_for_tiploc
    
    # Setup SMART lookup
    def mock_smart_lookup(area, berth):
        if area == "EK" and berth == "0152":
            return {"stanox": "87701", "platform": "3"}
        elif area == "AD" and berth == "0200":
            return {"stanox": "88600"}
        return None
    
    smart.lookup.side_effect = mock_smart_lookup
    
    # Create two schedules with same headcode 2C90
    # Train 1: Clapham Junction → Victoria (EK area)
    vs_london = VstpSchedule(
        uid="C12345",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("CLPHMJC", "12:30", "12:31"), ("VICTRIC", "12:45", "")]
    )
    
    # Train 2: Margate → Ramsgate (AD area)
    vs_kent = VstpSchedule(
        uid="C67890",
        signalling_id="2C90",
        start_date="2026-01-17",
        locations=[("MARGAT", "14:30", "14:31"), ("RAMSGTE", "15:45", "")]
    )
    
    hv.vstp_by_uid_date[("C12345", "2026-01-17")] = vs_london
    hv.vstp_by_uid_date[("C67890", "2026-01-17")] = vs_kent
    hv.vstp_by_headcode["2C90"] = [vs_london, vs_kent]
    
    # Simulate TRUST activations in both areas
    ts_london = TrustState(
        train_id="111111111",
        train_uid="C12345",
        activated=True,
        last_location="87701"
    )
    ts_kent = TrustState(
        train_id="222222222",
        train_uid="C67890",
        activated=True,
        last_location="88600"
    )
    
    hv.trust_by_area_headcode[("EK", "2C90")] = ts_london
    hv.trust_by_area_headcode[("AD", "2C90")] = ts_kent
    
    # Create TD observations in both areas
    td_ek = TdState(
        descr="2C90",
        area_id="EK",
        to_berth="0152",
        last_time_ms=iso_to_ms("2026-01-17T12:30:00Z") or 0
    )
    td_ad = TdState(
        descr="2C90",
        area_id="AD",
        to_berth="0200",
        last_time_ms=iso_to_ms("2026-01-17T14:30:00Z") or 0
    )
    
    hv.td_by_headcode[("EK", "2C90")] = td_ek
    hv.td_by_headcode[("AD", "2C90")] = td_ad
    
    # Test matching in EK area - should match London train
    sched_ek, reason_ek, _ = hv.match_td_to_schedule("EK", "2C90")
    assert sched_ek is vs_london, f"EK area should match London train, got UID {getattr(sched_ek, 'uid', None)}"
    assert "area-scoped" in reason_ek and "C12345" in reason_ek, f"Reason should mention area-scoped and UID C12345: {reason_ek}"
    
    # Test matching in AD area - should match Kent train
    sched_ad, reason_ad, _ = hv.match_td_to_schedule("AD", "2C90")
    assert sched_ad is vs_kent, f"AD area should match Kent train, got UID {getattr(sched_ad, 'uid', None)}"
    assert "area-scoped" in reason_ad and "C67890" in reason_ad, f"Reason should mention area-scoped and UID C67890: {reason_ad}"


def test_ek_rejects_manchester_route():
    """Test that EK area rejects Manchester route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: Manchester Victoria → Leeds
    vs_manchester = VstpSchedule(
        uid="C99999",
        signalling_id="1J24",
        start_date="2026-01-17",
        locations=[("MNCRPIC", "10:00", "10:01"), ("LEEDS", "11:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "MNCRPIC": "Manchester Piccadilly",
            "LEEDS": "Leeds"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should reject (no Kent or London keywords)
    result = hv._schedule_passes_through_area(vs_manchester, "EK", None)
    assert result is False, "EK area should reject Manchester route"


def test_ek_rejects_newcastle_route():
    """Test that EK area rejects Newcastle route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: Newcastle → Middlesbrough
    vs_newcastle = VstpSchedule(
        uid="C88888",
        signalling_id="2W32",
        start_date="2026-01-17",
        locations=[("NWCSTLE", "09:00", "09:01"), ("MDLSBRO", "10:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "NWCSTLE": "Newcastle",
            "MDLSBRO": "Middlesbrough"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should reject (no Kent or London keywords)
    result = hv._schedule_passes_through_area(vs_newcastle, "EK", None)
    assert result is False, "EK area should reject Newcastle route"


def test_ek_rejects_wales_route():
    """Test that EK area rejects Wales route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: Aberystwyth → Shrewsbury
    vs_wales = VstpSchedule(
        uid="C77777",
        signalling_id="1S20",
        start_date="2026-01-17",
        locations=[("ABRYSTW", "08:00", "08:01"), ("SHRWSBY", "10:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "ABRYSTW": "Aberystwyth",
            "SHRWSBY": "Shrewsbury"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should reject (no Kent or London keywords)
    result = hv._schedule_passes_through_area(vs_wales, "EK", None)
    assert result is False, "EK area should reject Wales route"


def test_ek_accepts_kent_route():
    """Test that EK area accepts Kent route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: Canterbury → Margate
    vs_kent = VstpSchedule(
        uid="C66666",
        signalling_id="2K90",
        start_date="2026-01-17",
        locations=[("CTRBURY", "12:00", "12:01"), ("MARGAT", "12:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "CTRBURY": "Canterbury East",
            "MARGAT": "Margate"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should accept (has Kent keywords)
    result = hv._schedule_passes_through_area(vs_kent, "EK", None)
    assert result is True, "EK area should accept Kent route"


def test_ek_accepts_london_to_kent_route():
    """Test that EK area accepts London to Kent route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: London Victoria → Dover
    vs_london_kent = VstpSchedule(
        uid="C55555",
        signalling_id="2V90",
        start_date="2026-01-17",
        locations=[("VICTRIC", "10:00", "10:01"), ("DOVERP", "11:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "VICTRIC": "London Victoria",
            "DOVERP": "Dover Priory"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should accept (has London and Kent keywords)
    result = hv._schedule_passes_through_area(vs_london_kent, "EK", None)
    assert result is True, "EK area should accept London to Kent route"


def test_ek_accepts_london_only_route():
    """Test that EK area accepts London-only route via geographic keyword filtering."""
    resolver = Mock()
    smart = Mock()
    hv = HumanView(resolver=resolver, smart=smart)
    
    # Create schedule: London Victoria → London St Pancras
    vs_london_only = VstpSchedule(
        uid="C44444",
        signalling_id="2L90",
        start_date="2026-01-17",
        locations=[("VICTRIC", "09:00", "09:01"), ("STPANCI", "09:30", "")]
    )
    
    # Mock resolver to return station names
    def mock_name_for_tiploc(tiploc):
        names = {
            "VICTRIC": "London Victoria",
            "STPANCI": "London St Pancras International"
        }
        return names.get(tiploc.upper(), "")
    
    resolver.name_for_tiploc.side_effect = mock_name_for_tiploc
    
    # Test - should accept (has London keywords and allow_london_routes is True)
    result = hv._schedule_passes_through_area(vs_london_only, "EK", None)
    assert result is True, "EK area should accept London-only route when allow_london_routes is True"
