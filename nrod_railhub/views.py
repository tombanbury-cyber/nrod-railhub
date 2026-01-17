#!/usr/bin/env python3
"""HumanView rendering and state management for nrod_railhub."""

from __future__ import annotations

import gzip
import json
import logging
import pathlib
import datetime
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    VstpSchedule, ItpsSchedule, TrustState, TdState,
    utc_now_iso, hhmmss_to_hhmm, local_hhmm, clip, ms_to_iso_utc
)
from .resolvers import LocationResolver, SmartResolver

logger = logging.getLogger(__name__)

class HumanView:
    """In-memory caches so we can join VSTP + TRUST + TD into a readable view."""

    def __init__(self, resolver: Optional[LocationResolver] = None, smart: Optional[SmartResolver] = None) -> None:

        self.resolver = resolver
        self.smart = smart

        self.vstp_by_uid_date: Dict[Tuple[str, str], VstpSchedule] = {}
        self.vstp_by_headcode: Dict[str, VstpSchedule] = {}

        # Planned schedules (SCHEDULE feed) for timetable enrichment
        self.sched_by_uid_date: Dict[Tuple[str, str], ItpsSchedule] = {}
        self.sched_by_headcode: Dict[str, ItpsSchedule] = {}

        self.trust_by_train_id: Dict[str, TrustState] = {}
        self.trust_by_uid: Dict[str, TrustState] = {}
        self.trust_by_headcode: Dict[str, TrustState] = {}

        self.td_by_headcode: Dict[tuple[str,str], TdState] = {}

        self.headcode_by_uid: Dict[str, str] = {}

    def match_td_to_schedule(self, td_area: str, headcode: str, *, trace: bool = False) -> tuple[Optional[object], str]:
        """
        Try to find the best matching VSTP/ITPS schedule object for a TD observation.

        Returns (schedule_object_or_None, reason_string).

        Strategy:
         - If TRUST for this headcode has train_uid, try vstp/sched lookup by uid.
         - Else try direct vstp_by_headcode / sched_by_headcode candidates.
         - Use SMART to resolve TD berth -> stanox -> station name and attempt to match that name
           to schedule locations (via resolver.name_for_tiploc).
         - Use simple time proximity (td.last_time_utc vs schedule dep/arr) as a tiebreaker.
        """
        td = self.td_by_headcode.get((td_area or "", (headcode or "").strip()))

        # 1) TRUST -> train_uid -> lookup
        ts = self.trust_by_headcode.get(headcode) or None
        if ts and ts.train_uid:
            uid = ts.train_uid
            for (k_uid, k_date), vs in list(self.vstp_by_uid_date.items()):
                if k_uid == uid:
                    reason = f"matched via TRUST train_uid {uid}"
                    if trace:
                        print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
                    return vs, reason
            for (k_uid, k_date), ss in list(self.sched_by_uid_date.items()):
                if k_uid == uid:
                    reason = f"matched timetable via TRUST train_uid {uid}"
                    if trace:
                        print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
                    return ss, reason

        # 2) Candidate schedules by headcode
        candidates = []
        vs = self.vstp_by_headcode.get(headcode)
        if vs:
            candidates.append(("VSTP", vs))
        ss = self.sched_by_headcode.get(headcode)
        if ss:
            candidates.append(("SCHEDULE", ss))
        if not candidates:
            reason = "no candidate schedules for headcode"
            if trace:
                print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return None, reason

        # 3) SMART -> stanox -> station name matching
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
                                reason = f"matched by SMART stanox {stanox} -> station name"
                                if trace:
                                    print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
                                return cand, reason

        # 4) Time proximity fallback
        import datetime
        try:
            td_time = td.last_time_utc and datetime.datetime.fromisoformat(td.last_time_utc.replace("Z", "+00:00"))
        except Exception:
            td_time = None

        best = None
        best_delta = None
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
                if dep and getattr(cand, "start_date", None):
                    try:
                        sd = getattr(cand, "start_date")
                        planned_dt = datetime.datetime.fromisoformat(sd) if "T" in sd else datetime.datetime.strptime(sd, "%Y-%m-%d")
                        hhmm = dep.strip()
                        if hhmm and ":" in hhmm:
                            h, m = map(int, hhmm.split(":")[:2])
                            planned_dt = planned_dt.replace(hour=h, minute=m, tzinfo=datetime.timezone.utc)
                    except Exception:
                        planned_dt = None
            if td_time and planned_dt:
                delta = abs((td_time - planned_dt).total_seconds())
                if best is None or delta < best_delta:
                    best = cand
                    best_delta = delta
        if best:
            reason = f"matched by time proximity (delta seconds {best_delta})"
            if trace:
                print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
            return best, reason

        reason = "ambiguous — returned first candidate for headcode"
        if trace:
            print(f"[{utc_now_iso()}] TRACE MATCH headcode={headcode} td_area={td_area} reason={reason}")
        return candidates[0][1], reason

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
                itps = ItpsSchedule(
                    uid=uid,
                    signalling_id=signalling_id,
                    start_date=(rec.get("schedule_start_date") or "").strip(),
                    end_date=(rec.get("schedule_end_date") or "").strip(),
                    days_run=days,
                    stp_indicator=stp,
                    locations=locations,
                )

                # If we already have a schedule for this headcode, prefer the one with
                # the "lowest" STP indicator (C < O < P generally).
                if signalling_id:
                    cur = self.sched_by_headcode.get(signalling_id)
                    if not cur or (itps.stp_indicator and cur.stp_indicator and itps.stp_indicator < cur.stp_indicator):
                        self.sched_by_headcode[signalling_id] = itps

                if uid and itps.start_date:
                    key = (uid, itps.start_date)
                    cur2 = self.sched_by_uid_date.get(key)
                    if not cur2 or (itps.stp_indicator and cur2.stp_indicator and itps.stp_indicator < cur2.stp_indicator):
                        self.sched_by_uid_date[key] = itps

                kept += 1

        if not quiet:
            print(f"[{utc_now_iso()}] SCHEDULE: loaded {kept} records (scanned {scanned} JSON lines) for {service_date}")

    def upsert_vstp(self, msg: Dict[str, Any]) -> Optional[VstpSchedule]:
        root = msg.get("VSTPCIFMsgV1") or msg.get("VSTPCIFMsgV1_1") or msg.get("VSTPCIFMsgV1_0")
        if not isinstance(root, dict):
            return None

        sched = root.get("schedule")
        if not isinstance(sched, dict):
            return None

        uid = (sched.get("CIF_train_uid") or "").strip()
        start_date = (sched.get("schedule_start_date") or "").strip()
        end_date = (sched.get("schedule_end_date") or "").strip()
        segments = sched.get("schedule_segment") or []
        if not isinstance(segments, list) or not segments:
            return None

        signalling_id = ""
        locations: List[Tuple[str, str, str]] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            signalling_id = signalling_id or (seg.get("signalling_id") or "").strip()
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
            start_date=start_date,
            end_date=end_date,
            locations=locations,
        )

        if vs.uid and vs.signalling_id:
            self.headcode_by_uid[vs.uid] = vs.signalling_id

        if uid and start_date:
            self.vstp_by_uid_date[(uid, start_date)] = vs
        if signalling_id:
            self.vstp_by_headcode[signalling_id] = vs

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
            state.last_time_utc = ms_to_iso_utc(td_msg["time"])
        else:
            state.last_time_utc = state.last_time_utc or utc_now_iso()

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
        st.toc_id = (body.get("toc_id") or st.toc_id or "").strip()
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
        # If TRUST gives us a headcode, store it
        if headcode:
            self.trust_by_headcode[headcode] = st

        # If TRUST doesn't give a headcode but we have a UID, try to infer from VSTP
        if not headcode and st.train_uid:
            inferred = self.headcode_by_uid.get(st.train_uid)
            if inferred:
                self.trust_by_headcode[inferred] = st


        return st


    def get_td_any(self, headcode: str) -> Optional[TdState]:
        """Return the most recent TdState for a headcode across all areas (fallback)."""
        best = None
        for (area, hc), st in self.td_by_headcode.items():
            if hc != headcode:
                continue
            if not best or (st.last_time_utc and st.last_time_utc > (best.last_time_utc or "")):
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

        # Use match_td_to_schedule when td_area is provided
        vs = None
        if td_area:
            td = self.td_by_headcode.get((td_area, headcode))
            if td:
                sched, reason = self.match_td_to_schedule(td_area, headcode, trace=trace)
                logger.debug(f"render_for_headcode: headcode={headcode} td_area={td_area} reason={reason}")
                if sched and isinstance(sched, VstpSchedule):
                    vs = sched
        
        # Fallback to direct lookup if no td_area or no match
        if not vs:
            vs = self.vstp_by_headcode.get(headcode)

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
            parts.append(
                f"TD: area={td.area_id or '?'} {td.from_berth or '?'}→{td.to_berth or '?'} @ {td.last_time_utc or '?'}"
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
        if td:
            sched, reason = self.match_td_to_schedule(td_area, headcode, trace=trace)
            logger.debug(f"render_for_td: headcode={headcode} td_area={td_area} reason={reason}")

        # If no match via TD, fallback to direct lookup (legacy behavior)
        if not sched:
            sched = self.vstp_by_headcode.get(headcode) or self.sched_by_headcode.get(headcode)

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


    def get_timetable_fields(self, headcode: str) -> Dict[str, Any]:
        """Return planned timetable fields for a headcode from VSTP or ITPS SCHEDULE.

        Returns a dict with keys:
          - source: "VSTP" | "SCHEDULE" | ""
          - uid: train uid if known
          - dep, arr: planned hh:mm strings (may be "")
          - origin, dest: resolved names if possible, else TIPLOC codes (may be "")
        """
        headcode = (headcode or "").strip()
        if not headcode:
            return {"source": "", "uid": "", "dep": "", "arr": "", "origin": "", "dest": ""}

        vs = self.vstp_by_headcode.get(headcode)
        if vs and vs.locations:
            o_code = vs.locations[0][0]
            d_code = vs.locations[-1][0]
            dep = vs.locations[0][2] or vs.locations[0][1]
            arr = vs.locations[-1][1] or vs.locations[-1][2]
            origin = (self.resolver.name_for_tiploc(o_code) if self.resolver else None) or o_code
            dest = (self.resolver.name_for_tiploc(d_code) if self.resolver else None) or d_code
            return {
                "source": "VSTP",
                "uid": vs.uid or "",
                "dep": dep or "",
                "arr": arr or "",
                "origin": origin or "",
                "dest": dest or "",
            }

        itps = self.sched_by_headcode.get(headcode)
        if itps and itps.locations:
            o = itps.locations[0]
            d = itps.locations[-1]
            if isinstance(o, tuple):
                o_tiploc = o[0] if len(o) > 0 else ""
                o_arr = o[1] if len(o) > 1 else ""
                o_dep = o[2] if len(o) > 2 else ""
            else:
                o_tiploc = getattr(o, "tiploc", "") or ""
                o_arr = getattr(o, "arrival", "") or ""
                o_dep = getattr(o, "departure", "") or ""
            if isinstance(d, tuple):
                d_tiploc = d[0] if len(d) > 0 else ""
                d_arr = d[1] if len(d) > 1 else ""
                d_dep = d[2] if len(d) > 2 else ""
            else:
                d_tiploc = getattr(d, "tiploc", "") or ""
                d_arr = getattr(d, "arrival", "") or ""
                d_dep = getattr(d, "departure", "") or ""
            dep = o_dep or o_arr
            arr = d_arr or d_dep
            origin = (self.resolver.name_for_tiploc(o_tiploc) if self.resolver else None) or o_tiploc
            dest = (self.resolver.name_for_tiploc(d_tiploc) if self.resolver else None) or d_tiploc
            return {
                "source": "SCHEDULE",
                "uid": itps.uid or "",
                "dep": dep or "",
                "arr": arr or "",
                "origin": origin or "",
                "dest": dest or "",
            }

        return {"source": "", "uid": "", "dep": "", "arr": "", "origin": "", "dest": ""}

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
        vs = self.vstp_by_headcode.get(headcode)
        ss = self.sched_by_headcode.get(headcode)
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
            td = self.td_by_headcode.get((td_area, headcode)) if td_area else None
            if td:
                # Try SMART (TD+berth -> STANOX/platform), then resolve via CORPUS.
                if self.smart:
                    berth = td.to_berth or td.from_berth
                    hit = self.smart.lookup(td.area_id, berth) if (td.area_id and berth) else None
                    if hit and hit.get("stanox"):
                        stx = hit["stanox"]
                        nm = self.resolver.name_for_stanox(stx) if self.resolver else ""
                        plat = hit.get("platform") or ""
                        if nm:
                            last_seen = f"{nm} ({stx})" + (f" plat {plat}" if plat else "")
                        else:
                            last_seen = stx + (f" plat {plat}" if plat else "")

                # Fallback: show the raw TD area/berth.
                if not last_seen:
                    if td.area_id and td.to_berth:
                        last_seen = f"{td.area_id}:{td.to_berth}"
                    elif td.area_id:
                        last_seen = td.area_id

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


