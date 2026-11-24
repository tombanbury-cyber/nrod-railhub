#!/usr/bin/env python3
"""HumanView rendering and state management for nrod_railhub."""

from __future__ import annotations

import gzip
import json
import pathlib
import datetime
import threading
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from .models import (
    VstpSchedule, ItpsSchedule, TrustState, TdState,
    utc_now_iso, utc_now_ms, hhmmss_to_hhmm, local_hhmm, clip, ms_to_iso_utc, safe_int
)
from .schedule_matching import (
    planned_datetime,
    resolve_ranked_candidates,
    schedule_runs_on,
    select_active_schedule_versions,
    stp_precedence,
)
from .resolvers import LocationResolver, SmartResolver, TOCResolver
from .logging_config import get_logger

logger = get_logger("views")

# TOC matching boost factor for schedule scoring
# When a schedule's TOC matches the allowed TOCs for a TD area,
# reduce effective time delta by this factor to prefer the correct operator
TOC_MATCH_BOOST_FACTOR = 0.90

# Geographic keyword filtering for TD area route validation
TD_AREA_REGIONS = {
    "EK": {
        "keywords": [
            "MARGATE", "RAMSGATE", "DOVER", "CANTERBURY", "ASHFORD",
            "GILLINGHAM", "FAVERSHAM", "SITTINGBOURNE", "THANET",
            "FOLKESTONE", "BROADSTAIRS", "ROCHESTER", "STROOD",
            "RAINHAM", "GRAVESEND", "EBBSFLEET", "DARTFORD"
        ],
        "allow_london_routes": True,
        "london_keywords": ["LONDON", "VICTORIA", "CHARING", "ST PANCRAS", "KINGS CROSS"]
    }
}

class HumanView:
    """In-memory caches so we can join VSTP + TRUST + TD into a readable view."""

    def __init__(self, resolver: Optional[LocationResolver] = None, smart: Optional[SmartResolver] = None, 
                 toc_resolver: Optional[TOCResolver] = None) -> None:

        self.resolver = resolver
        self.smart = smart
        self.toc_resolver = toc_resolver

        self.vstp_by_uid_date: Dict[Tuple[str, str], VstpSchedule] = {}
        self.vstp_by_headcode: Dict[str, List[VstpSchedule]] = {}

        # Planned schedules (SCHEDULE feed) for timetable enrichment
        self.sched_by_uid_date: Dict[Tuple[str, str], ItpsSchedule] = {}
        self.sched_by_headcode: Dict[str, List[ItpsSchedule]] = {}

        # TIPLOC → schedule index for accurate TD matching
        # Maps normalized TIPLOC to list of (schedule_object, stop_index, planned_hhmm)
        self.schedules_by_tiploc: Dict[str, List[Tuple[Any, int, str]]] = {}

        self.trust_by_train_id: Dict[str, TrustState] = {}
        self.trust_by_uid: Dict[str, TrustState] = {}
        self.trust_by_headcode: Dict[str, TrustState] = {}

        self.td_by_headcode: Dict[tuple[str,str], TdState] = {}

        self.headcode_by_uid: Dict[str, str] = {}

        # Area-scoped TRUST index for handling headcode reuse across regions
        self.trust_by_area_headcode: Dict[Tuple[str, str], TrustState] = {}
        self.td_allowed_tocs_cache: Dict[str, set[str]] = {}
        self.td_allowed_tocs_cache_lock = threading.Lock()

    @staticmethod
    def _normalize_tiploc(tiploc: str) -> str:
        """Normalize TIPLOC for consistent indexing (upper-case, stripped)."""
        return (tiploc or "").strip().upper()

    def _area_from_stanox(self, stanox: str) -> Optional[str]:
        """Infer TD area from STANOX using SMART reverse lookup.
        
        Iterates through SMART berth_map to find the area where this STANOX appears.
        Returns the first matching area, or None if not found.
        
        Note: This performs a linear scan through berth_map. For large datasets,
        consider building a reverse STANOX→area index if this becomes a bottleneck.
        """
        if not self.smart:
            return None
        # Iterate through SMART berth_map to find area for this STANOX
        for (area, berth), data in self.smart.berth_map.items():
            if data.get('stanox') == stanox:
                return area
        return None

    def _schedule_passes_through_area(
        self, 
        schedule: Union[VstpSchedule, ItpsSchedule], 
        td_area: str, 
        td_state: Optional[TdState]
    ) -> bool:
        """Check if schedule route intersects with TD area using two-stage validation.
        
        Stage 1: Precise STANOX matching (if berth resolution available via SMART)
        Stage 2: Geographic keyword filtering (if area has configured regions)
        
        This helps filter out schedules that share the same headcode but serve different routes.
        
        Args:
            schedule: VstpSchedule or ItpsSchedule object
            td_area: TD area code (e.g., "EK")
            td_state: Current TD state for the train (may be None)
            
        Returns:
            True if schedule route passes through the TD area, False otherwise.
            Returns True only when neither STANOX validation nor keyword filtering 
            can be performed (i.e., no TD state/berth data AND area not configured 
            in TD_AREA_REGIONS).
        """
        # Stage 1: Precise STANOX matching (existing logic)
        if td_state and self.smart and self.resolver:
            berth = td_state.to_berth or td_state.from_berth
            if berth:
                hit = self.smart.lookup(td_area, berth)
                if hit and hit.get('stanox'):
                    stanox = str(hit['stanox'])
                    # Check if any schedule location matches this STANOX
                    locations = getattr(schedule, 'locations', []) or []
                    if locations:  # Only validate if we have locations
                        for loc in locations:
                            tiploc = loc[0] if isinstance(loc, tuple) else getattr(loc, 'tiploc', '')
                            if tiploc:
                                loc_stanox = self.resolver.stanox_for_tiploc(tiploc)
                                if loc_stanox == stanox:
                                    return True
                        # We resolved the berth, have locations, but found no match
                        return False
        
        # Stage 2: Geographic keyword filtering (NEW)
        if td_area in TD_AREA_REGIONS and self.resolver:
            area_config = TD_AREA_REGIONS[td_area]
            keywords = area_config.get('keywords', [])
            allow_london = area_config.get('allow_london_routes', False)
            london_keywords = area_config.get('london_keywords', [])
            
            locations = getattr(schedule, 'locations', []) or []
            if locations:
                # Check each location's station name against keywords
                for loc in locations:
                    tiploc = loc[0] if isinstance(loc, tuple) else getattr(loc, 'tiploc', '')
                    if tiploc:
                        station_name = self.resolver.name_for_tiploc(tiploc)
                        if station_name:
                            station_upper = station_name.upper()
                            # Check for regional keywords
                            for keyword in keywords:
                                if keyword in station_upper:
                                    return True
                            # Check for London keywords if allowed
                            if allow_london:
                                for keyword in london_keywords:
                                    if keyword in station_upper:
                                        return True
                # No keyword match found - reject schedule
                return False
        
        # Default: assume match (can't validate without data)
        return True

    def _build_station_to_tiplocs_index(self) -> Dict[str, List[str]]:
        """Build a reverse index: station name (lowercase) → list of TIPLOCs.
        
        This is used to efficiently find TIPLOCs for a given station name during
        TIPLOC-index matching without iterating through all TIPLOCs.
        """
        if not self.resolver:
            return {}
        
        station_to_tiplocs: Dict[str, List[str]] = {}
        for tiploc, name in self.resolver.tiploc_to_name.items():
            key = name.strip().lower()
            if key not in station_to_tiplocs:
                station_to_tiplocs[key] = []
            station_to_tiplocs[key].append(tiploc)
        return station_to_tiplocs

    @staticmethod
    def _schedule_identity(schedule: Any) -> tuple[str, str, str, str]:
        return (
            type(schedule).__name__,
            str(getattr(schedule, "uid", "") or ""),
            str(getattr(schedule, "start_date", "") or ""),
            str(getattr(schedule, "stp_indicator", "") or "P"),
        )

    def _active_schedules(self, schedules: List[Any], service_date: Optional[datetime.date]) -> List[Any]:
        candidates = [
            schedule for schedule in schedules
            if service_date is None or schedule_runs_on(schedule, service_date)
        ]
        return select_active_schedule_versions(candidates)

    def _headcode_schedules(
        self, headcode: str, service_date: Optional[datetime.date]
    ) -> tuple[List[Any], List[Any]]:
        vstp = self._active_schedules(self.vstp_by_headcode.get(headcode, []), service_date)
        itps = self._active_schedules(self.sched_by_headcode.get(headcode, []), service_date)
        vstp_uids = {getattr(schedule, "uid", "") for schedule in vstp}
        itps = [schedule for schedule in itps if getattr(schedule, "uid", "") not in vstp_uids]
        return vstp, itps

    @staticmethod
    def _match_info(
        schedule: Any,
        *,
        source: str,
        evidence: List[str],
        confidence: str,
        service_date: Optional[datetime.date] = None,
        **details: Any,
    ) -> Dict[str, Any]:
        return {
            "status": "matched",
            "source": source,
            "uid": str(getattr(schedule, "uid", "") or ""),
            "service_date": service_date.isoformat() if service_date else "",
            "schedule_start_date": str(getattr(schedule, "start_date", "") or ""),
            "evidence": evidence,
            "confidence": confidence,
            **details,
        }

    def _trust_for_td(
        self, td_area: str, headcode: str, td: Optional[TdState]
    ) -> Optional[TrustState]:
        area_state = self.trust_by_area_headcode.get((td_area, headcode))
        candidates = [
            state for state in self.trust_by_train_id.values()
            if state.headcode == headcode and state.train_uid
        ]
        if area_state and area_state.train_uid and all(
            state.train_id != area_state.train_id for state in candidates
        ):
            candidates.append(area_state)
        if not candidates:
            legacy_state = self.trust_by_headcode.get(headcode)
            if (
                legacy_state and td_area and legacy_state.last_location and self.smart
                and self._area_from_stanox(legacy_state.last_location) not in (None, td_area)
            ):
                return None
            return legacy_state

        if area_state and area_state.train_uid:
            area_candidates = [
                state for state in candidates
                if state.train_id == area_state.train_id
                or (
                    state.last_location and self.smart
                    and self._area_from_stanox(state.last_location) == td_area
                )
            ]
            candidates = area_candidates or [area_state]
        elif td_area and self.smart:
            area_candidates = [
                state for state in candidates
                if state.last_location
                and self._area_from_stanox(state.last_location) == td_area
            ]
            if area_candidates:
                candidates = area_candidates
            else:
                candidates = [
                    state for state in candidates
                    if not state.last_location
                    or self._area_from_stanox(state.last_location) is None
                ]

        td_time = td.last_time_ms if td and td.last_time_ms else None
        if td_time:
            td_date = datetime.datetime.fromtimestamp(
                td_time / 1000, tz=timezone.utc
            ).date()
            same_day = []
            undated = []
            for state in candidates:
                try:
                    trust_dt = datetime.datetime.fromisoformat(
                        state.last_event_time.replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    undated.append(state)
                    continue
                if trust_dt.date() == td_date:
                    same_day.append(state)
            candidates = same_day or undated

        def trust_delta(state: TrustState) -> float:
            if not td_time:
                return float("-inf")
            try:
                trust_dt = datetime.datetime.fromisoformat(
                    state.last_event_time.replace("Z", "+00:00")
                )
                return -abs(td_time - int(trust_dt.timestamp() * 1000))
            except (TypeError, ValueError):
                return float("-inf")

        match = resolve_ranked_candidates(
            candidates,
            rank=lambda state: (trust_delta(state),),
            identity=lambda state: state.train_uid,
        )
        return match.candidate

    def match_td_to_schedule(self, td_area: str, headcode: str, *, trace: bool = False, allowed_tocs: Optional[set[str]] = None) -> tuple[Optional[object], str, Optional[Dict[str, Any]]]:
        """
        Try to find the best matching VSTP/ITPS schedule object for a TD observation.

        Returns (schedule_object_or_None, reason_string, matched_info_or_None). An
        ambiguous result has no schedule and includes the competing UIDs in matched_info.
        
        matched_info is a dict with keys:
          - status, source, uid, service_date, evidence, confidence
          - matched_tiploc, stop_index, planned_dt when location evidence was used

        Strategy:
         - If TRUST for this headcode has train_uid, try vstp/sched lookup by uid.
         - Use TIPLOC index: resolve TD berth → stanox → station name → TIPLOC(s),
           then query schedules_by_tiploc for candidates that call at that station.
         - Rank matches by: UID match, validity, and time delta from TD observation.
         - Fallback to headcode candidates with time proximity if no TIPLOC match.
         - If allowed_tocs is provided (or loaded from cache), filter candidates by TOC
           and boost scores for matching TOCs.
        """
        # Check for allowed TOCs from cache if not explicitly provided
        if allowed_tocs is None:
            cache = getattr(self, 'td_allowed_tocs_cache', {})
            cache_lock = getattr(self, 'td_allowed_tocs_cache_lock', None)
            if cache_lock:
                with cache_lock:
                    cached_tocs = cache.get(td_area) if (cache and td_area) else None
            else:
                cached_tocs = cache.get(td_area) if (cache and td_area) else None
            if cached_tocs:
                allowed_tocs = cached_tocs
                if allowed_tocs:
                    # Ensure allowed_tocs is a set (converting a set to set is a no-op)
                    allowed_tocs = set(allowed_tocs)
        
        td = self.td_by_headcode.get((td_area or "", (headcode or "").strip()))
        service_date = None
        if td and td.last_time_ms:
            try:
                service_date = datetime.datetime.fromtimestamp(
                    td.last_time_ms / 1000, tz=timezone.utc
                ).date()
            except (OverflowError, OSError, ValueError):
                service_date = None

        ts_area = self.trust_by_area_headcode.get((td_area, headcode))
        uid_trust = self._trust_for_td(td_area, headcode, td)
        trust_area_match = bool(ts_area)
        if uid_trust and td_area and uid_trust.last_location and self.smart:
            trust_area_match = self._area_from_stanox(uid_trust.last_location) == td_area

        if uid_trust and uid_trust.train_uid:
            uid = uid_trust.train_uid.strip()
            uid_vstp = [
                schedule for (key_uid, _), schedule in self.vstp_by_uid_date.items()
                if key_uid == uid
            ]
            uid_vstp.extend(
                schedule for schedule in self.vstp_by_headcode.get(headcode, [])
                if getattr(schedule, "uid", "") == uid
            )
            uid_itps = [
                schedule for (key_uid, _), schedule in self.sched_by_uid_date.items()
                if key_uid == uid
            ]
            uid_itps.extend(
                schedule for schedule in self.sched_by_headcode.get(headcode, [])
                if getattr(schedule, "uid", "") == uid
            )
            uid_vstp = list({id(schedule): schedule for schedule in uid_vstp}.values())
            uid_itps = list({id(schedule): schedule for schedule in uid_itps}.values())
            uid_vstp = self._active_schedules(uid_vstp, service_date)
            uid_itps = self._active_schedules(uid_itps, service_date)
            uid_schedules = uid_vstp or uid_itps
            uid_schedules = [
                schedule for schedule in uid_schedules
                if not getattr(schedule, "signalling_id", "")
                or getattr(schedule, "signalling_id", "") == headcode
            ]
            uid_match = resolve_ranked_candidates(
                uid_schedules,
                rank=lambda schedule: (
                    -stp_precedence(schedule),
                    str(getattr(schedule, "start_date", "") or ""),
                ),
                identity=self._schedule_identity,
            )
            if uid_match.status == "matched" and uid_match.candidate is not None:
                schedule = uid_match.candidate
                source = "VSTP" if schedule in uid_vstp else "SCHEDULE"
                prefix = "matched via" if source == "VSTP" else "matched timetable via"
                reason = f"{prefix} {'area-scoped ' if trust_area_match else ''}TRUST train_uid {uid}"
                if trust_area_match:
                    reason += f" (area={td_area})"
                info = self._match_info(
                    schedule,
                    source=source,
                    evidence=["TRUST train_id", "TRUST train_uid", "service date"],
                    confidence="high",
                    service_date=service_date,
                    trust_train_id=uid_trust.train_id,
                )
                if trace:
                    logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
                return schedule, reason, info
            status = uid_match.status
            reason = (
                f"ambiguous schedules for TRUST train_uid {uid}"
                if status == "ambiguous"
                else f"no active schedule for TRUST train_uid {uid}"
            )
            info = {
                "status": status,
                "source": "TRUST",
                "uid": uid,
                "service_date": service_date.isoformat() if service_date else "",
                "trust_train_id": uid_trust.train_id,
                "candidate_uids": sorted({
                    str(getattr(candidate, "uid", "") or "?")
                    for candidate in uid_match.alternatives
                }),
                "evidence": ["TRUST train_id", "TRUST train_uid", "service date"],
                "confidence": "unresolved" if status == "ambiguous" else "none",
            }
            if trace:
                logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return None, reason, info

        # 1) TRUST -> train_uid -> lookup (fallback to global if no area match)
        ts = uid_trust

        # 2) TIPLOC-index based matching (new approach)
        if td and self.smart and self.resolver:
            berth = td.to_berth or td.from_berth or ""
            hit = self.smart.lookup(td.area_id or "", berth) if (td.area_id and berth) else None
            if hit and hit.get("stanox"):
                stanox = str(hit["stanox"])
                stanox_name = self.resolver.name_for_stanox(stanox)
                if stanox_name:
                    # Build reverse index for efficient lookup
                    station_to_tiplocs = self._build_station_to_tiplocs_index()
                    candidate_tiplocs = station_to_tiplocs.get(stanox_name.strip().lower(), [])
                    active_vstp, active_itps = self._headcode_schedules(headcode, service_date)
                    active_schedule_ids = {id(schedule) for schedule in active_vstp + active_itps}
                    
                    # Query schedules_by_tiploc for each candidate TIPLOC
                    tiploc_candidates = []
                    tiploc_candidates_skipped_toc = []
                    for tiploc in candidate_tiplocs:
                        if tiploc in self.schedules_by_tiploc:
                            for sched_obj, stop_idx, planned_hhmm in self.schedules_by_tiploc[tiploc]:
                                # Filter by headcode - only consider schedules with matching headcode
                                sched_headcode = getattr(sched_obj, "signalling_id", "") or ""
                                if sched_headcode == headcode and id(sched_obj) in active_schedule_ids:
                                    # Check TOC filtering if allowed_tocs is provided
                                    if allowed_tocs:
                                        sched_toc = getattr(sched_obj, 'toc_code', None) or getattr(sched_obj, 'toc', None)
                                        if sched_toc and sched_toc not in allowed_tocs:
                                            logger.debug(f"Skipping TIPLOC candidate: toc={sched_toc} not in allowed_tocs={allowed_tocs} (headcode={headcode} uid={getattr(sched_obj, 'uid', '?')})")
                                            tiploc_candidates_skipped_toc.append((sched_obj, stop_idx, planned_hhmm, tiploc))
                                            continue
                                    tiploc_candidates.append((sched_obj, stop_idx, planned_hhmm, tiploc))
                    
                    # If no candidates after TOC filtering, fall back to relaxed mode
                    fallback_reason_suffix = ""
                    if not tiploc_candidates and allowed_tocs and tiploc_candidates_skipped_toc:
                        logger.debug(f"No TIPLOC candidates with mapped TOC for headcode={headcode} area={td_area}, using relaxed fallback")
                        tiploc_candidates = tiploc_candidates_skipped_toc
                        fallback_reason_suffix = " (relaxed: no mapped TOC candidates)"
                    
                    if tiploc_candidates:
                        td_time = None
                        try:
                            if td.last_time_ms:
                                td_time = datetime.datetime.fromtimestamp(
                                    td.last_time_ms / 1000, tz=timezone.utc
                                )
                        except (OverflowError, OSError, ValueError):
                            td_time = None

                        scored_candidates = []
                        for sched_obj, stop_idx, planned_hhmm, tiploc in tiploc_candidates:
                            planned_dt = planned_datetime(
                                sched_obj, planned_hhmm, service_date=service_date
                            )

                            uid_match = bool(
                                ts and ts.train_uid
                                and getattr(sched_obj, "uid", "") == ts.train_uid
                            )
                            candidate_toc = (
                                getattr(sched_obj, "toc_code", None)
                                or getattr(sched_obj, "toc", None)
                            )
                            toc_match = bool(allowed_tocs and candidate_toc in allowed_tocs)
                            delta = (
                                abs((td_time - planned_dt).total_seconds())
                                if td_time and planned_dt else None
                            )
                            if delta is not None and toc_match:
                                delta *= TOC_MATCH_BOOST_FACTOR
                            score = (
                                int(uid_match),
                                -delta if delta is not None else float("-inf"),
                            )
                            scored_candidates.append({
                                "schedule": sched_obj,
                                "score": score,
                                "stop_index": stop_idx,
                                "planned_dt": planned_dt.isoformat() if planned_dt else "",
                                "tiploc": tiploc,
                                "delta": delta,
                                "toc_match": toc_match,
                            })

                        match = resolve_ranked_candidates(
                            scored_candidates,
                            rank=lambda candidate: candidate["score"],
                            identity=lambda candidate: self._schedule_identity(candidate["schedule"]),
                        )
                        if match.status == "ambiguous":
                            candidate_uids = sorted({
                                str(getattr(item["schedule"], "uid", "") or "?")
                                for item in match.alternatives
                            })
                            reason = f"ambiguous TIPLOC schedule match for headcode {headcode}"
                            info = {
                                "status": "ambiguous",
                                "source": "TIPLOC",
                                "candidate_uids": candidate_uids,
                                "service_date": service_date.isoformat() if service_date else "",
                                "evidence": ["TD location", "service date"],
                                "confidence": "unresolved",
                            }
                            if trace:
                                logger.debug(
                                    f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}"
                                )
                            return None, reason + fallback_reason_suffix, info

                        if match.candidate is not None:
                            selected = match.candidate
                            schedule = selected["schedule"]
                            reason = (
                                f"matched via TIPLOC index: tiploc={selected['tiploc']} "
                                f"stanox={stanox} stop_idx={selected['stop_index']}"
                            )
                            if selected["delta"] is not None:
                                reason += f" delta={selected['delta']:.0f}s"
                            if selected["toc_match"]:
                                reason += " (TOC boost)"
                            reason += fallback_reason_suffix
                            evidence = ["TD location", "service date"]
                            confidence = "medium"
                            if ts and ts.train_uid and getattr(schedule, "uid", "") == ts.train_uid:
                                evidence.append("TRUST train_uid")
                                confidence = "high"
                            if selected["delta"] is not None:
                                evidence.append("scheduled stop time")
                            info = self._match_info(
                                schedule,
                                source="SCHEDULE" if isinstance(schedule, ItpsSchedule) else "VSTP",
                                evidence=evidence,
                                confidence=confidence,
                                service_date=service_date,
                                matched_tiploc=selected["tiploc"],
                                stop_index=selected["stop_index"],
                                planned_dt=selected["planned_dt"],
                                toc_match=selected["toc_match"],
                            )
                            if trace:
                                logger.debug(
                                    f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}"
                                )
                            logger.debug(f"TIPLOC match: {reason}")
                            return schedule, reason, info

        # 3) Fallback: Candidate schedules by headcode (with area-route validation)
        vstp_list, itps_list = self._headcode_schedules(headcode, service_date)
        candidates = []
        vs_list = vstp_list
        for vs in vs_list:
            if self._schedule_passes_through_area(vs, td_area, td):
                candidates.append(("VSTP", vs))
        ss_list = itps_list
        for ss in ss_list:
            if self._schedule_passes_through_area(ss, td_area, td):
                candidates.append(("SCHEDULE", ss))
        
        # Add diagnostic logging when multiple candidates exist
        if trace or len(candidates) > 1:
            logger.debug(f"MATCH DEBUG: headcode={headcode} td_area={td_area}")
            logger.debug(f"  Found {len(candidates)} candidate schedules after area filtering")
            for typ, cand in candidates:
                locations = getattr(cand, 'locations', []) or []
                origin = locations[0][0] if locations else "?"
                dest = locations[-1][0] if locations else "?"
                uid = getattr(cand, 'uid', '?')
                logger.debug(f"    {typ}: {origin} → {dest} (UID={uid})")
        
        if not candidates:
            reason = "no candidate schedules for headcode"
            if trace:
                logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return None, reason, None

        # 4) Legacy SMART stanox matching (scan candidate locations)
        smart_matches = set()
        if td and self.smart:
            berth = td.to_berth or td.from_berth or ""
            hit = self.smart.lookup(td.area_id or "", berth) if (td.area_id and berth) else None
            if hit and hit.get("stanox"):
                stanox = str(hit["stanox"])
                stanox_name = self.resolver.name_for_stanox(stanox) if self.resolver else None
                if stanox_name:
                    for typ, cand in candidates:
                        locs = getattr(cand, "locations", []) or []
                        for loc in locs:
                            tiploc = loc[0] if isinstance(loc, tuple) else getattr(loc, "tiploc", "") or ""
                            if not tiploc:
                                continue
                            cand_name = self.resolver.name_for_tiploc(tiploc) if self.resolver else None
                            if cand_name and cand_name.strip().lower() == stanox_name.strip().lower():
                                smart_matches.add(self._schedule_identity(cand))

        # 5) Time proximity fallback
        td_time = None
        try:
            if td and td.last_time_ms:
                td_time = datetime.datetime.fromtimestamp(
                    td.last_time_ms / 1000, tz=timezone.utc
                )
        except (OverflowError, OSError, ValueError):
            td_time = None

        ranked_candidates = []
        for typ, cand in candidates:
            planned_dt = None
            locs = getattr(cand, "locations", []) or []
            if locs:
                first = locs[0]
                dep = None
                if isinstance(first, tuple):
                    dep = first[2] if len(first) > 2 else None
                else:
                    dep = getattr(first, "departure", None) or getattr(first, "dep", None)
                if dep:
                    planned_dt = planned_datetime(cand, dep, service_date=service_date)
            delta = (
                abs((td_time - planned_dt).total_seconds())
                if td_time and planned_dt else None
            )
            toc = getattr(cand, "toc_code", None) or getattr(cand, "toc", None)
            toc_match = bool(allowed_tocs and toc in allowed_tocs)
            if delta is not None and toc_match:
                delta *= TOC_MATCH_BOOST_FACTOR
            uid_match = bool(ts and ts.train_uid and getattr(cand, "uid", "") == ts.train_uid)
            ranked_candidates.append({
                "source": typ,
                "schedule": cand,
                "delta": delta,
                "planned_dt": planned_dt.isoformat() if planned_dt else "",
                "toc_match": toc_match,
                "smart_match": self._schedule_identity(cand) in smart_matches,
                "score": (
                    int(uid_match),
                    int(self._schedule_identity(cand) in smart_matches),
                    -delta if delta is not None else float("-inf"),
                ),
            })

        match = resolve_ranked_candidates(
            ranked_candidates,
            rank=lambda candidate: candidate["score"],
            identity=lambda candidate: self._schedule_identity(candidate["schedule"]),
        )
        if match.status == "ambiguous":
            candidate_uids = sorted({
                str(getattr(item["schedule"], "uid", "") or "?")
                for item in match.alternatives
            })
            reason = f"ambiguous schedule match for headcode {headcode}"
            info = {
                "status": "ambiguous",
                "source": "VSTP/SCHEDULE",
                "candidate_uids": candidate_uids,
                "service_date": service_date.isoformat() if service_date else "",
                "evidence": ["headcode", "service date", "TD area"],
                "confidence": "unresolved",
            }
            if trace:
                logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return None, reason, info

        if match.candidate is not None:
            selected = match.candidate
            schedule = selected["schedule"]
            if selected["smart_match"]:
                reason = "matched by SMART stanox -> station name"
                if selected["delta"] is not None:
                    reason += f" with time proximity (delta seconds {selected['delta']:.0f})"
            elif selected["delta"] is not None:
                reason = f"matched by time proximity (delta seconds {selected['delta']:.0f})"
            else:
                reason = "matched unique loaded schedule candidate for headcode"
            evidence = ["headcode"]
            if service_date:
                evidence.append("service date")
            if td_area:
                evidence.append("TD area route")
            if selected["smart_match"]:
                evidence.append("SMART location")
            if selected["delta"] is not None:
                evidence.append("scheduled departure time")
            if ts and ts.train_uid and getattr(schedule, "uid", "") == ts.train_uid:
                evidence.append("TRUST train_uid")
            confidence = (
                "high" if "TRUST train_uid" in evidence
                else "medium" if "SMART location" in evidence or "scheduled departure time" in evidence
                else "low"
            )
            info = self._match_info(
                schedule,
                source=selected["source"],
                evidence=evidence,
                confidence=confidence,
                service_date=service_date,
                planned_dt=selected["planned_dt"],
                toc_match=selected["toc_match"],
            )
            if selected["toc_match"]:
                reason += " (TOC boost)"
            if trace:
                logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return schedule, reason, info

        reason = "no candidate schedules for headcode"
        if trace:
            logger.debug(f"TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
        return None, reason, None

    def load_schedule_gz(
        self,
        gz_path: str,
        *,
        service_date: Optional[str] = None,
        headcode_filter: Optional[str] = None,
        uid_filter: Optional[str] = None,
        quiet: bool = False,
    ) -> None:
        """Load a gzip'd JSON schedule extract into lightweight indices.

        We only keep the schedules that are *valid on* service_date (default: today, UTC),
        and optionally only those matching headcode_filter / uid_filter.
        
        Also builds a TIPLOC→schedule index (schedules_by_tiploc) for accurate TD matching.
        """
        import gzip
        import json
        import pathlib
        import datetime

        path = pathlib.Path(gz_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(str(path))

        if not service_date:
            service_date = datetime.now(timezone.utc).date().isoformat()

        svc_dt = datetime.date.fromisoformat(service_date)
        dow = svc_dt.weekday()  # Mon=0
        
        # Clear existing TIPLOC index to avoid stale entries
        self.schedules_by_tiploc.clear()

        def valid_on(rec: Dict[str, Any]) -> bool:
            sd = (rec.get("schedule_start_date") or "").strip()
            ed = (rec.get("schedule_end_date") or "").strip()
            if not sd or not ed:
                return False
            try:
                sdd = datetime.date.fromisoformat(sd)
                edd = datetime.date.fromisoformat(ed)
            except Exception:
                return False
            if svc_dt < sdd or svc_dt > edd:
                return False
            days = (rec.get("schedule_days_runs") or "").strip()
            if len(days) == 7 and all(c in "01" for c in days):
                try:
                    return days[dow] == "1"
                except Exception:
                    return True
            return True

        def pick_time(loc: Dict[str, Any], kind: str) -> str:
            # kind: "arr" or "dep"
            if kind == "arr":
                v = loc.get("public_arrival") or loc.get("arrival") or loc.get("pass")
            else:
                v = loc.get("public_departure") or loc.get("departure") or loc.get("pass")
            return hhmmss_to_hhmm(v or "")

        kept = 0
        scanned = 0
        with gzip.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                scanned += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                rec = obj.get("JsonScheduleV1")
                if not isinstance(rec, dict):
                    continue
                if (rec.get("transaction_type") or "").lower() == "delete":
                    continue

                uid = (rec.get("CIF_train_uid") or "").strip()
                if uid_filter and uid and uid != uid_filter:
                    continue

                if not valid_on(rec):
                    continue

                seg = rec.get("schedule_segment") or {}
                if not isinstance(seg, dict):
                    continue
                signalling_id = (seg.get("signalling_id") or "").strip()
                if headcode_filter and signalling_id and signalling_id != headcode_filter:
                    continue

                locs = seg.get("schedule_location") or []
                if not isinstance(locs, list) or not locs:
                    continue

                locations: List[Tuple[str, str, str]] = []
                for loc in locs:
                    if not isinstance(loc, dict):
                        continue
                    tiploc = (loc.get("tiploc_code") or "").strip()
                    if not tiploc:
                        continue
                    arr = pick_time(loc, "arr")
                    dep = pick_time(loc, "dep")
                    locations.append((tiploc, arr, dep))

                if not uid and not signalling_id:
                    continue

                stp = (rec.get("CIF_stp_indicator") or "").strip()
                days = (rec.get("schedule_days_runs") or "").strip()
                train_category = (seg.get("CIF_train_category") or rec.get("CIF_train_category") or "").strip()
                power_type = (seg.get("CIF_power_type") or rec.get("CIF_power_type") or "").strip()
                itps = ItpsSchedule(
                    uid=uid,
                    signalling_id=signalling_id,
                    train_category=train_category,
                    power_type=power_type,
                    start_date=(rec.get("schedule_start_date") or "").strip(),
                    end_date=(rec.get("schedule_end_date") or "").strip(),
                    days_run=days,
                    stp_indicator=stp,
                    locations=locations,
                )

                # Store all schedules with this headcode (multiple services may share the same headcode)
                if signalling_id:
                    if signalling_id not in self.sched_by_headcode:
                        self.sched_by_headcode[signalling_id] = []
                    self.sched_by_headcode[signalling_id].append(itps)

                if uid and itps.start_date:
                    key = (uid, itps.start_date)
                    cur2 = self.sched_by_uid_date.get(key)
                    if not cur2 or (itps.stp_indicator and cur2.stp_indicator and itps.stp_indicator < cur2.stp_indicator):
                        self.sched_by_uid_date[key] = itps

                # Build TIPLOC index: for each location in the schedule, add an entry
                for stop_idx, loc_tuple in enumerate(itps.locations):
                    tiploc = self._normalize_tiploc(loc_tuple[0])
                    if not tiploc:
                        continue
                    # Use departure or arrival time as planned_hhmm
                    planned_hhmm = loc_tuple[2] or loc_tuple[1]  # dep or arr
                    if tiploc not in self.schedules_by_tiploc:
                        self.schedules_by_tiploc[tiploc] = []
                    # Add entry: (schedule_object, stop_index, planned_hhmm)
                    self.schedules_by_tiploc[tiploc].append((itps, stop_idx, planned_hhmm))

                kept += 1

        if not quiet:
            logger.info(f"SCHEDULE: loaded {kept} records (scanned {scanned} JSON lines) for {service_date}")

    def upsert_vstp(self, msg: Dict[str, Any]) -> Optional[VstpSchedule]:
        root = msg.get("VSTPCIFMsgV1") or msg.get("VSTPCIFMsgV1_1") or msg.get("VSTPCIFMsgV1_0")
        if not isinstance(root, dict):
            return None

        sched = root.get("schedule")
        if not isinstance(sched, dict):
            return None

        # Fields may live inside the "schedule" sub-dict or at the VSTPCIFMsgV1 root level;
        # try schedule first, then fall back to root.
        uid = (sched.get("CIF_train_uid") or root.get("CIF_train_uid") or "").strip()
        start_date = (sched.get("schedule_start_date") or root.get("schedule_start_date") or "").strip()
        end_date = (sched.get("schedule_end_date") or root.get("schedule_end_date") or "").strip()
        segments = sched.get("schedule_segment") or []
        if isinstance(segments, dict):
            segments = [segments]
        if not isinstance(segments, list) or not segments:
            return None

        signalling_id = ""
        train_category = ""
        power_type = ""
        locations: List[Tuple[str, str, str]] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            signalling_id = signalling_id or (seg.get("signalling_id") or "").strip()
            train_category = train_category or (seg.get("CIF_train_category") or "").strip()
            power_type = power_type or (seg.get("CIF_power_type") or "").strip()
            locs = seg.get("schedule_location") or []
            if not isinstance(locs, list):
                continue
            for loc in locs:
                if not isinstance(loc, dict):
                    continue

                tiploc = None
                loc_obj = loc.get("location")
                if isinstance(loc_obj, dict):
                    tiploc_obj = loc_obj.get("tiploc")
                    if isinstance(tiploc_obj, dict):
                        tiploc = tiploc_obj.get("tiploc_id")

                tiploc_s = (tiploc or "").strip()
                arr = hhmmss_to_hhmm(loc.get("scheduled_arrival_time", ""))
                dep = hhmmss_to_hhmm(loc.get("scheduled_departure_time", ""))
                if tiploc_s:
                    locations.append((tiploc_s, arr, dep))

        if not uid and not signalling_id:
            return None

        vs = VstpSchedule(
            uid=uid,
            signalling_id=signalling_id,
            train_category=train_category,
            power_type=power_type,
            start_date=start_date,
            end_date=end_date,
            locations=locations,
        )

        if vs.uid and vs.signalling_id:
            self.headcode_by_uid[vs.uid] = vs.signalling_id

        if uid and start_date:
            self.vstp_by_uid_date[(uid, start_date)] = vs
        if signalling_id:
            if signalling_id not in self.vstp_by_headcode:
                self.vstp_by_headcode[signalling_id] = []
            self.vstp_by_headcode[signalling_id].append(vs)

        # Build TIPLOC index for VSTP schedules
        for stop_idx, loc_tuple in enumerate(vs.locations):
            tiploc = self._normalize_tiploc(loc_tuple[0])
            if not tiploc:
                continue
            # Use departure or arrival time as planned_hhmm
            planned_hhmm = loc_tuple[2] or loc_tuple[1]  # dep or arr
            if tiploc not in self.schedules_by_tiploc:
                self.schedules_by_tiploc[tiploc] = []
            # Add entry: (schedule_object, stop_index, planned_hhmm)
            self.schedules_by_tiploc[tiploc].append((vs, stop_idx, planned_hhmm))

        return vs

    def upsert_td(self, td_msg: Dict[str, Any]) -> Optional[TdState]:
        if not isinstance(td_msg, dict):
            return None

        descr = (td_msg.get("descr") or "").strip()
        if not descr:
            return None

        area_id = (td_msg.get("area_id") or "").strip()
        key = (area_id, descr)
        state = self.td_by_headcode.get(key) or TdState(descr=descr)
        state.area_id = area_id or (state.area_id or "")
        state.from_berth = (td_msg.get("from") or state.from_berth or "").strip()
        state.to_berth = (td_msg.get("to") or state.to_berth or "").strip()

        if "time" in td_msg:
            # td_msg["time"] is already in milliseconds
            time_ms = safe_int(td_msg["time"])
            if time_ms:
                state.last_time_ms = time_ms
            else:
                state.last_time_ms = state.last_time_ms or utc_now_ms()
        else:
            state.last_time_ms = state.last_time_ms or utc_now_ms()

        self.td_by_headcode[key] = state
        return state

    def upsert_trust(self, trust_msg: Dict[str, Any]) -> Optional[TrustState]:
        if not isinstance(trust_msg, dict):
            return None
        body = trust_msg.get("body")
        if not isinstance(body, dict):
            return None

        train_id = (body.get("train_id") or "").strip()
        if not train_id:
            return None

        st = self.trust_by_train_id.get(train_id) or TrustState(train_id=train_id)

        st.train_uid = (body.get("train_uid") or body.get("CIF_train_uid") or st.train_uid or "").strip()
        
        # Normalize TOC identifier using resolver
        raw_toc_id = (body.get("toc_id") or st.toc_id or "").strip()
        if raw_toc_id and self.toc_resolver:
            # Try to resolve to canonical 2-character code
            canonical_toc = self.toc_resolver.resolve_toc_code(raw_toc_id)
            st.toc_id = canonical_toc if canonical_toc else raw_toc_id
        else:
            st.toc_id = raw_toc_id
        
        st.schedule_source = (body.get("schedule_source") or st.schedule_source or "").strip()

        msg_type = (body.get("msg_type") or "").strip()
        if msg_type == "0001":
            st.activated = True
            st.cancelled = False
        elif msg_type == "0002":
            st.cancelled = True
        elif msg_type == "0005":
            st.cancelled = False

        st.last_location = (body.get("loc_stanox") or body.get("stanox") or st.last_location or "").strip()

        if "event_timestamp" in body:
            st.last_event_time = ms_to_iso_utc(body["event_timestamp"])
        elif "actual_timestamp" in body:
            st.last_event_time = ms_to_iso_utc(body["actual_timestamp"])
        else:
            st.last_event_time = st.last_event_time or utc_now_iso()

        if "late_running" in body:
            st.last_delay_min = safe_int(body.get("late_running"))
        elif "delay" in body:
            st.last_delay_min = safe_int(body.get("delay"))

        self.trust_by_train_id[train_id] = st
        if st.train_uid:
            self.trust_by_uid[st.train_uid] = st

        headcode = (body.get("train_reporting_number") or body.get("reporting_number") or "").strip()
        if headcode:
            st.headcode = headcode
        else:
            headcode = st.headcode
        # If TRUST gives us a headcode, store it
        if headcode:
            self.trust_by_headcode[headcode] = st

        # If TRUST doesn't give a headcode but we have a UID, try to infer from VSTP
        if not headcode and st.train_uid:
            inferred = self.headcode_by_uid.get(st.train_uid)
            if inferred:
                self.trust_by_headcode[inferred] = st
                # Update headcode for use in area-scoped indexing below
                headcode = inferred
                st.headcode = inferred

        # Infer TD area from location STANOX and populate area-scoped index
        if st.last_location and headcode and self.smart:
            inferred_area = self._area_from_stanox(st.last_location)
            if inferred_area:
                self.trust_by_area_headcode[(inferred_area, headcode)] = st
                logger.debug(
                    f"TRUST area-scoped: train_id={train_id} headcode={headcode} "
                    f"stanox={st.last_location} -> area={inferred_area}"
                )

        return st


    def get_td_any(self, headcode: str) -> Optional[TdState]:
        """Return the most recent TdState for a headcode across all areas (fallback)."""
        best = None
        for (area, hc), st in self.td_by_headcode.items():
            if hc != headcode:
                continue
            if not best or (st.last_time_ms and st.last_time_ms > (best.last_time_ms or 0)):
                best = st
        return best


    def decode_last_location(self, td_area: str, headcode: str) -> Dict[str, Any]:
        """Decode the latest TD berth for (td_area, headcode) into a location dict.

        Returns keys: berth, stanox, name, platform, raw.
        """
        td_area = (td_area or '').strip()
        headcode = (headcode or '').strip()
        td = self.td_by_headcode.get((td_area, headcode))
        berth = None
        if td:
            berth = (td.to_berth or td.from_berth or '').strip() or None
        if not berth:
            return {"berth": None, "stanox": None, "name": None, "platform": None, "raw": f"{td_area}:?"}

        # Text berths: 4 letters, special/fringe
        if len(berth) == 4 and berth.isalpha():
            return {"berth": berth, "stanox": None, "name": None, "platform": None, "raw": f"{td_area} text-berth {berth}"}

        info = self.smart.lookup(td_area, str(berth)) if self.smart else None
        if not info:
            return {"berth": berth, "stanox": None, "name": None, "platform": None, "raw": f"{td_area}:{berth}"}

        stanox = info.get("stanox") or info.get("STANOX") or info.get("Stanox")
        platform = info.get("platform") or info.get("plat") or info.get("Platform")
        name = self.resolver.name_for_stanox(str(stanox)) if (stanox and self.resolver) else None

        raw = f"{name} ({stanox})" + (f" plat {platform}" if platform else "") if (name and stanox) else f"{td_area}:{berth}"
        return {
            "berth": berth,
            "stanox": str(stanox) if stanox is not None else None,
            "name": name,
            "platform": str(platform) if platform is not None else None,
            "raw": raw,
        }

    def render_for_headcode(self, headcode: str, td_area: str | None = None, *, width: int = 120, trace: bool = False) -> str:
        parts: List[str] = [f"[{utc_now_iso()}] {headcode}"]

        vs = None
        if td_area:
            td = self.td_by_headcode.get((td_area, headcode))
            if td:
                sched, reason, matched_info = self.match_td_to_schedule(td_area, headcode, trace=trace)
                logger.debug(f"render_for_headcode: headcode={headcode} td_area={td_area} reason={reason}")
                if sched and isinstance(sched, VstpSchedule):
                    vs = sched
        if not vs and not self.td_by_headcode.get((td_area or "", headcode)):
            candidates = self._active_schedules(
                self.vstp_by_headcode.get(headcode, []),
                datetime.datetime.now(timezone.utc).date(),
            )
            unique = resolve_ranked_candidates(
                candidates,
                rank=lambda _candidate: (0,),
                identity=self._schedule_identity,
            )
            vs = unique.candidate

        if vs:
            # decorate origin/dest with names if we have them
            if vs.locations and self.resolver:
                o_code = vs.locations[0][0]
                d_code = vs.locations[-1][0]
                o_name = self.resolver.name_for_tiploc(o_code)
                d_name = self.resolver.name_for_tiploc(d_code)

                origin = f"{o_name} ({o_code})" if o_name else o_code
                dest = f"{d_name} ({d_code})" if d_name else d_code

                dep = vs.locations[0][2] or vs.locations[0][1]
                arr = vs.locations[-1][1] or vs.locations[-1][2]
                parts.append(f"VSTP: {headcode}  {origin} {dep or ''} → {dest} {arr or ''} ({vs.start_date})")
            else:
                parts.append(f"VSTP: {vs.summary()}")

        td = self.td_by_headcode.get((td_area, headcode)) if td_area else None
        if td and (td.from_berth or td.to_berth or td.area_id):
            td_time_iso = ms_to_iso_utc(td.last_time_ms) if td.last_time_ms else "?"
            parts.append(
                f"TD: area={td.area_id or '?'} {td.from_berth or '?'}→{td.to_berth or '?'} @ {td_time_iso}"
            )

        ts = self.trust_by_headcode.get(headcode)
        if ts:
            parts.append(self._render_trust(ts))
        else:
            if vs and vs.uid and vs.uid in self.trust_by_uid:
                parts.append(self._render_trust(self.trust_by_uid.get(vs.uid)))

        line = " | ".join(parts)

        # Keep console output tidy.

        if width and width > 0:

            line = line[:width]

        return line


    def render_for_td(self, td_area: str, headcode: str, *, width: int = 120, trace: bool = False) -> str:
        """Render a single-line, human-friendly view for a TD train (area+headcode)."""
        # Timetable enrichment using match_td_to_schedule
        dep = arr = None
        origin = dest = None

        # Use the new matching helper to select the best schedule
        td = self.td_by_headcode.get((td_area, headcode))
        sched = None
        reason = ""
        matched_info = None
        if td:
            sched, reason, matched_info = self.match_td_to_schedule(td_area, headcode, trace=trace)
            logger.debug(f"render_for_td: headcode={headcode} td_area={td_area} reason={reason}")

        # Extract schedule details
        if sched and getattr(sched, "locations", None):
            locs = sched.locations
            if locs:
                o = locs[0]
                d = locs[-1]
                # Handle both tuple and object location formats
                if isinstance(o, tuple):
                    o_tiploc = o[0] if len(o) > 0 else ''
                    o_arr = o[1] if len(o) > 1 else ''
                    o_dep = o[2] if len(o) > 2 else ''
                else:
                    o_tiploc = getattr(o, 'tiploc', '')
                    o_arr = getattr(o, 'arrival', '')
                    o_dep = getattr(o, 'departure', '')
                if isinstance(d, tuple):
                    d_tiploc = d[0] if len(d) > 0 else ''
                    d_arr = d[1] if len(d) > 1 else ''
                    d_dep = d[2] if len(d) > 2 else ''
                else:
                    d_tiploc = getattr(d, 'tiploc', '')
                    d_arr = getattr(d, 'arrival', '')
                    d_dep = getattr(d, 'departure', '')
                dep = o_dep or o_arr
                arr = d_arr or d_dep
                origin = (self.resolver.name_for_tiploc(o_tiploc) if self.resolver else None) or o_tiploc
                dest = (self.resolver.name_for_tiploc(d_tiploc) if self.resolver else None) or d_tiploc

        dep_s = dep or "??:??"
        arr_s = arr or "??:??"
        origin_s = origin or "?"
        dest_s = dest or "?"

        # TD last position (SMART + CORPUS decode if available)
        td = self.td_by_headcode.get((td_area, headcode))
        last_str = f"{td_area}:?"
        if td:
            berth = td.to_berth or td.from_berth or "?"
            if isinstance(berth, str) and len(berth) == 4 and berth.isalpha():
                last_str = f"{td_area} text-berth {berth}"
            else:
                info = self.smart.lookup(td_area, str(berth)) if self.smart else None
                if info:
                    stanox = info.get("stanox") or info.get("STANOX") or info.get("Stanox")
                    plat = info.get("platform") or info.get("plat") or info.get("Platform")
                    name = self.resolver.name_for_stanox(str(stanox)) if (stanox and self.resolver) else None
                    if name:
                        last_str = f"{name} ({stanox})" + (f" plat {plat}" if plat else "")
                    else:
                        last_str = f"{td_area}:{berth}"
                else:
                    last_str = f"{td_area}:{berth}"

        line = f"{dep_s}→{arr_s}  {origin_s} → {dest_s}"
        if width and width > 10 and len(line) > width:
            line = line[:width]
        return f"{line}\n      Last: {last_str}"
        
        
    def _render_trust(self, trust):
        """
        Render TRUST state safely.
        This is intentionally defensive: TRUST may be missing or partial.
        """
        if not trust:
            return ""

        parts = []

        try:
            if trust.get("event_type"):
                parts.append(trust["event_type"])
            if trust.get("loc_name"):
                parts.append(trust["loc_name"])
            if trust.get("platform"):
                parts.append(f"plat {trust['platform']}")
            if trust.get("actual_ts"):
                parts.append(trust["actual_ts"])
        except Exception:
            return ""

        if not parts:
            return ""

        return "TRUST: " + " ".join(parts)


    def get_timetable_fields(self, headcode: str, *, td_area: Optional[str] = None) -> Dict[str, Any]:
        """Return planned timetable fields for a headcode from VSTP or ITPS SCHEDULE.

        Returns a dict with keys:
          - source: "VSTP" | "SCHEDULE" | ""
          - uid: train uid if known
          - dep, arr: planned hh:mm strings (may be "")
          - origin, dest: resolved names if possible, else TIPLOC codes (may be "")
          - category, power_type: train classification metadata if available
          - match_status, match_reason, match_info: provenance and ambiguity details
        """
        headcode = (headcode or "").strip()
        empty = {
            "source": "", "uid": "", "dep": "", "arr": "", "origin": "", "dest": "",
            "category": "", "power_type": "", "match_status": "unmatched",
            "match_reason": "", "match_info": None,
        }
        if not headcode:
            return empty

        if td_area:
            schedule, reason, match_info = self.match_td_to_schedule(td_area, headcode)
            if schedule is None:
                empty["match_status"] = (match_info or {}).get("status", "unmatched")
                empty["match_reason"] = reason
                empty["match_info"] = match_info
                return empty
        else:
            service_date = datetime.datetime.now(timezone.utc).date()
            candidates = self._headcode_schedules(
                headcode, service_date
            )
            all_candidates = candidates[0] + candidates[1]
            match = resolve_ranked_candidates(
                all_candidates,
                rank=lambda _candidate: (0,),
                identity=self._schedule_identity,
            )
            if match.status != "matched" or match.candidate is None:
                empty["match_status"] = match.status
                empty["match_reason"] = f"{match.status} schedule candidates for headcode {headcode}"
                empty["match_info"] = {
                    "status": match.status,
                    "service_date": service_date.isoformat(),
                    "candidate_uids": sorted({
                        str(getattr(candidate, "uid", "") or "?")
                        for candidate in match.alternatives
                    }),
                    "evidence": ["headcode", "service date"],
                    "confidence": "unresolved" if match.status == "ambiguous" else "none",
                }
                return empty
            schedule = match.candidate
            reason = "matched unique loaded schedule candidate for headcode"
            match_info = self._match_info(
                schedule,
                source="VSTP" if isinstance(schedule, VstpSchedule) else "SCHEDULE",
                evidence=["headcode", "service date"],
                confidence="low",
                service_date=service_date,
            )

        locations = getattr(schedule, "locations", []) or []
        if not locations:
            return {
                **empty,
                "match_status": "matched",
                "match_reason": reason,
                "match_info": match_info,
                "source": "VSTP" if isinstance(schedule, VstpSchedule) else "SCHEDULE",
                "uid": getattr(schedule, "uid", "") or "",
                "category": getattr(schedule, "train_category", "") or "",
                "power_type": getattr(schedule, "power_type", "") or "",
            }

        def location_fields(location: Any) -> tuple[str, str, str]:
            if isinstance(location, tuple):
                return (
                    location[0] if len(location) > 0 else "",
                    location[1] if len(location) > 1 else "",
                    location[2] if len(location) > 2 else "",
                )
            return (
                getattr(location, "tiploc", "") or "",
                getattr(location, "arrival", "") or "",
                getattr(location, "departure", "") or "",
            )

        origin_code, origin_arr, origin_dep = location_fields(locations[0])
        dest_code, dest_arr, dest_dep = location_fields(locations[-1])
        origin = (self.resolver.name_for_tiploc(origin_code) if self.resolver else None) or origin_code
        dest = (self.resolver.name_for_tiploc(dest_code) if self.resolver else None) or dest_code
        return {
            "source": "VSTP" if isinstance(schedule, VstpSchedule) else "SCHEDULE",
            "uid": getattr(schedule, "uid", "") or "",
            "dep": origin_dep or origin_arr or "",
            "arr": dest_arr or dest_dep or "",
            "origin": origin or "",
            "dest": dest or "",
            "category": getattr(schedule, "train_category", "") or "",
            "power_type": getattr(schedule, "power_type", "") or "",
            "match_status": "matched",
            "match_reason": reason,
            "match_info": match_info,
        }

    def render_for_uid(self, uid: str) -> str:
        parts: List[str] = [f"[{utc_now_iso()}] uid={uid}"]

        vs_any = [v for (u, _d), v in self.vstp_by_uid_date.items() if u == uid]
        if vs_any:
            vs_any.sort(key=lambda v: v.start_date)
            parts.append(f"VSTP: {vs_any[-1].summary()}")

        ts = self.trust_by_uid.get(uid)
        if ts:
            parts.append(self._render_trust(ts))

        return " | ".join(parts)

    def _render_trust(self, ts: TrustState) -> str:
        flags = []
        if ts.activated:
            flags.append("activated")
        if ts.cancelled:
            flags.append("CANCELLED")
        flag_s = ",".join(flags) if flags else "update"
        delay = f"{ts.last_delay_min:+d}m" if ts.last_delay_min is not None else ""

        loc = ts.last_location or "?"
        if self.resolver and loc.isdigit():
            nm = self.resolver.name_for_stanox(loc)
            if nm:
                loc = f"{nm} ({loc})"

        return f"TRUST: train_id={ts.train_id} uid={ts.train_uid or '?'} {flag_s} loc={loc} time={ts.last_event_time or '?'} {delay}".strip()

    def pretty_for_headcode(self, headcode: str, width: int = 96) -> str:
        """
        Render a departure-board style 1–2 line summary for a headcode using:
          - VSTP: origin/dest + planned dep/arr
          - TRUST: delay + last STANOX (resolved)
          - TD: last area/berth (fallback if no TRUST location)
        """
        vs_list = self.vstp_by_headcode.get(headcode, [])
        vs = vs_list[0] if vs_list else None
        ss_list = self.sched_by_headcode.get(headcode, [])
        ss = ss_list[0] if ss_list else None
        ts = self.trust_by_headcode.get(headcode)

        # If TRUST doesn't have the headcode directly but we have a VSTP UID, try that.
        if not ts and (vs or ss) and ((vs.uid if vs else ss.uid)):
            uid = vs.uid if vs else ss.uid
            ts = self.trust_by_uid.get(uid)

        # --- Build origin/dest + planned times ---
        origin_code = dest_code = ""
        dep = arr = ""
        src = vs or ss
        if src and src.locations:
            origin_code = src.locations[0][0]
            dest_code = src.locations[-1][0]
            dep = src.locations[0][2] or src.locations[0][1]  # prefer dep, else arr
            arr = src.locations[-1][1] or src.locations[-1][2]  # prefer arr, else dep

        origin_name = ""
        dest_name = ""
        if self.resolver:
            if origin_code:
                origin_name = self.resolver.name_for_tiploc(origin_code)
            if dest_code:
                dest_name = self.resolver.name_for_tiploc(dest_code)

        origin = origin_name or origin_code or "?"
        dest = dest_name or dest_code or "?"

        # --- Status bits (delay/cancel/last seen) ---
        cancelled = bool(ts and ts.cancelled)
        activated = bool(ts and ts.activated)

        delay_txt = ""
        if ts and ts.last_delay_min is not None:
            if ts.last_delay_min == 0:
                delay_txt = "On time"
            else:
                sign = "+" if ts.last_delay_min > 0 else ""
                delay_txt = f"{sign}{ts.last_delay_min}m"

        # Last seen / TD fallback
        last_seen = ""
        if ts and ts.last_location:
            loc = ts.last_location
            if self.resolver and loc.isdigit():
                nm = self.resolver.name_for_stanox(loc)
                last_seen = f"{nm} ({loc})" if nm else loc
            else:
                last_seen = loc

        if not last_seen:
            pass  # No TD area available in this method; TD fallback omitted.

        # --- Compose lines ---
        t = local_hhmm()
        time_part = f"{dep or '??:??'}→{arr or '??:??'}"

        status = []
        if cancelled:
            status.append("CANCELLED")
        elif activated:
            status.append("ACTIVE")

        if delay_txt:
            status.append(delay_txt)

        status_part = "  ".join(status) if status else ""

        # Main line: "15:18  2C90  12:51→13:58  Woking → London Waterloo   +3m"
        left = f"{t}  {headcode:<4}  {time_part:<11}  {origin} → {dest}"
        line1 = left
        if status_part:
            # pad to align status right-ish while respecting width
            spacer = "  "
            line1 = f"{left}{spacer}{status_part}"

        line1 = clip(line1, width)

        # Second line: "       Last: Clapham Junction (12345)"
        line2 = ""
        if last_seen:
            line2 = clip(f"      Last: {last_seen}", width)

        return line1 if not line2 else f"{line1}\n{line2}"
