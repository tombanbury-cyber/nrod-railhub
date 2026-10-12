"""Station-to-station corridor explorer for the Berth / Signal (Signal Mappings) page.

The corridor is built from several sources with *different* evidential value, and
the result keeps them separate so the UI never over-claims:

* CORPUS (``corpus_locations``)      - resolves station names / CRS / TIPLOC / STANOX.
* CIF   (``cif_schedule_locations``) - ordered *timetable* calls/passes. This identifies
  which services run between the two stations and in which travel direction. It says
  nothing about physical berth order, track connectivity or signal placement.
* SMART (``smart_berths``)           - reference data tagging berths with a STANOX
  (i.e. a station/location). Berth -> location association only.
* TD    (``td_berth_movements``, ``td_state``) - *observed* berth steps and live trains.
* Signals: ``physical_signal_mappings`` (reviewed / inferred), ``td_sclass_movement_scores``
  (inferred from correlation) and ``topology_edges`` (reconstructed hypotheses).

Up/Down is only labelled when a known orientation hint exists for the station pair
(see ``KNOWN_DIRECTION_HINTS``); otherwise the two travel directions are shown as
"A -> B" / "B -> A" without assuming which one is towards London.
"""

from __future__ import annotations

import datetime as _dt
import html
import sqlite3
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

from .db_backend import DB_ERRORS

# (from CRS, to CRS) -> direction of travel from first to second ("down" = away from London).
# This is curated reference knowledge, not derived from the database.
KNOWN_DIRECTION_HINTS: Dict[tuple, str] = {
    ("FAV", "RAM"): "down",
}

MAX_SCHEDULES = 300
_CHUNK = 400
_STP_PRECEDENCE = {"C": 0, "O": 1, "N": 2, "P": 3}

DIRECTION_LABELS = {
    "down": "Down (away from London)",
    "up": "Up (towards London)",
}


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    table_exists = getattr(conn, "table_exists", None)
    if table_exists is not None:
        return table_exists(name)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _chunks(items: List[Any], size: int = _CHUNK) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _marks(n: int) -> str:
    return ",".join("?" * n)


# ---------------------------------------------------------------------------
# Station resolution
# ---------------------------------------------------------------------------

def _group_corpus_rows(rows: List[Any]) -> List[dict]:
    """Group CORPUS rows into stations keyed by CRS (or name when there is no CRS)."""
    groups: Dict[str, dict] = {}
    for r in rows:
        tiploc, stanox, crs, name = r[0], r[1], r[2], r[3]
        key = (crs or "").strip().upper() or f"name:{(name or '').strip().upper()}"
        g = groups.setdefault(
            key, {"key": key, "name": name, "crs": (crs or None), "tiplocs": [], "stanoxes": []}
        )
        if tiploc and tiploc not in g["tiplocs"]:
            g["tiplocs"].append(tiploc)
        if stanox and stanox not in g["stanoxes"]:
            g["stanoxes"].append(stanox)
    return list(groups.values())


def resolve_station(conn: sqlite3.Connection, text: str) -> dict:
    """Resolve user text (name, CRS, TIPLOC or STANOX) to candidate stations.

    Returns ``{"status": "ok"|"ambiguous"|"unknown"|"empty", "station": dict|None,
    "candidates": [dict, ...]}``.
    """
    text = (text or "").strip()
    if not text:
        return {"status": "empty", "station": None, "candidates": []}
    upper = text.upper()
    cols = "tiploc, stanox, crs, name"
    rows: List[Any] = []
    if _has_table(conn, "corpus_locations"):
        # Exact code match first (CRS / TIPLOC / STANOX), then exact name, then substring.
        rows = conn.execute(
            f"SELECT {cols} FROM corpus_locations "
            "WHERE tiploc IS NOT NULL AND (crs=? OR tiploc=? OR stanox=?) LIMIT 200",
            (upper, upper, text),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                f"SELECT {cols} FROM corpus_locations "
                "WHERE tiploc IS NOT NULL AND UPPER(name)=? LIMIT 200",
                (upper,),
            ).fetchall()
        if not rows:
            escaped = upper.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                f"SELECT {cols} FROM corpus_locations "
                "WHERE tiploc IS NOT NULL AND UPPER(name) LIKE ? ESCAPE '\\' LIMIT 200",
                (f"%{escaped}%",),
            ).fetchall()
    groups = _group_corpus_rows(rows)
    if not groups:
        # Without reference data a bare TIPLOC can still be matched against CIF directly.
        if len(upper) <= 7 and upper.isalnum() and _has_table(conn, "cif_schedule_locations"):
            hit = conn.execute(
                "SELECT 1 FROM cif_schedule_locations WHERE tiploc=? LIMIT 1", (upper,)
            ).fetchone()
            if hit:
                station = {"key": upper, "name": None, "crs": None, "tiplocs": [upper], "stanoxes": []}
                return {"status": "ok", "station": station, "candidates": [station]}
        return {"status": "unknown", "station": None, "candidates": []}
    if len(groups) == 1:
        return {"status": "ok", "station": groups[0], "candidates": groups}
    return {"status": "ambiguous", "station": None, "candidates": groups[:20]}


# ---------------------------------------------------------------------------
# CIF schedules
# ---------------------------------------------------------------------------

def _runs_on(row: Any, on_date: Optional[_dt.date]) -> bool:
    if on_date is None:
        return True
    iso = on_date.isoformat()
    start, end, days = row["schedule_start_date"], row["schedule_end_date"], row["schedule_days_runs"]
    if start and start > iso:
        return False
    if end and end < iso:
        return False
    if days and len(days) == 7 and days[on_date.weekday()] != "1":
        return False
    return True


def _best_time(loc: Any) -> tuple:
    for col, kind in (
        ("public_departure_time", "call"), ("public_arrival_time", "call"),
        ("scheduled_departure_time", "call"), ("scheduled_arrival_time", "call"),
        ("scheduled_pass_time", "pass"),
    ):
        if loc[col]:
            return loc[col], kind
    return None, None


def find_corridor_schedules(
    conn: sqlite3.Connection, tiplocs_a: List[str], tiplocs_b: List[str],
    on_date: Optional[_dt.date] = None,
) -> List[dict]:
    """Return CIF schedules visiting both endpoints, each tagged with travel direction.

    ``direction`` is ``"a_to_b"`` when A precedes B in the ordered CIF locations.
    """
    if not tiplocs_a or not tiplocs_b or not _has_table(conn, "cif_schedule_locations") \
            or not _has_table(conn, "cif_schedules"):
        return []
    sql = (
        "SELECT s.uid, s.schedule_start_date, s.schedule_end_date, s.schedule_days_runs, "
        "s.CIF_stp_indicator, s.transaction_type, s.CIF_headcode, s.toc_code, "
        "la.segment_index AS seg, la.location_index AS ia, lb.location_index AS ib "
        "FROM cif_schedule_locations la "
        "JOIN cif_schedule_locations lb ON lb.uid=la.uid AND lb.schedule_start_date=la.schedule_start_date "
        "AND lb.segment_index=la.segment_index "
        "JOIN cif_schedules s ON s.uid=la.uid AND s.schedule_start_date=la.schedule_start_date "
        f"WHERE la.tiploc IN ({_marks(len(tiplocs_a))}) AND lb.tiploc IN ({_marks(len(tiplocs_b))}) "
        "AND la.location_index<>lb.location_index"
    )
    rows = conn.execute(sql, list(tiplocs_a) + list(tiplocs_b)).fetchall()
    # One endpoint pairing per schedule variant: the closest pair.
    best: Dict[tuple, Any] = {}
    for r in rows:
        k = (r["uid"], r["schedule_start_date"], r["CIF_stp_indicator"])
        if k not in best or abs(r["ib"] - r["ia"]) < abs(best[k]["ib"] - best[k]["ia"]):
            best[k] = r
    # Apply validity and STP precedence (C cancels, O overlays, P is base) per UID.
    by_uid: Dict[str, Any] = {}
    for r in best.values():
        if (r["transaction_type"] or "").strip().lower() == "delete" or not _runs_on(r, on_date):
            continue
        cur = by_uid.get(r["uid"])
        rank = _STP_PRECEDENCE.get((r["CIF_stp_indicator"] or "P").upper(), 3)
        if cur is None or rank < _STP_PRECEDENCE.get((cur["CIF_stp_indicator"] or "P").upper(), 3):
            by_uid[r["uid"]] = r
    out = []
    for r in by_uid.values():
        if (r["CIF_stp_indicator"] or "").upper() == "C":
            continue
        out.append({
            "uid": r["uid"], "schedule_start_date": r["schedule_start_date"],
            "stp": r["CIF_stp_indicator"], "headcode": (r["CIF_headcode"] or "").strip().upper() or None,
            "toc_code": r["toc_code"], "segment_index": r["seg"],
            "idx_a": r["ia"], "idx_b": r["ib"],
            "direction": "a_to_b" if r["ia"] < r["ib"] else "b_to_a",
        })
    out.sort(key=lambda s: (s["headcode"] or "", s["uid"]))
    return out[:MAX_SCHEDULES]


def _load_schedule_detail(conn: sqlite3.Connection, sched: dict) -> List[Any]:
    return conn.execute(
        "SELECT location_index, tiploc, scheduled_pass_time, scheduled_departure_time, "
        "scheduled_arrival_time, public_departure_time, public_arrival_time, platform "
        "FROM cif_schedule_locations WHERE uid=? AND schedule_start_date=? AND segment_index=? "
        "ORDER BY location_index",
        (sched["uid"], sched["schedule_start_date"], sched["segment_index"]),
    ).fetchall()


def _tiploc_info(conn: sqlite3.Connection, tiplocs: List[str]) -> Dict[str, dict]:
    info: Dict[str, dict] = {}
    if not tiplocs or not _has_table(conn, "corpus_locations"):
        return info
    for chunk in _chunks(sorted(set(tiplocs))):
        for r in conn.execute(
            f"SELECT tiploc, stanox, crs, name FROM corpus_locations WHERE tiploc IN ({_marks(len(chunk))})",
            chunk,
        ):
            cur = info.setdefault(r["tiploc"], {"name": r["name"], "crs": r["crs"], "stanox": r["stanox"]})
            for field, val in (("name", r["name"]), ("crs", r["crs"]), ("stanox", r["stanox"])):
                if not cur.get(field) and val:
                    cur[field] = val
    return info


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_corridor(
    conn: sqlite3.Connection, from_text: str, to_text: str,
    on_date: Optional[_dt.date] = None,
) -> dict:
    """Assemble the corridor between two stations from CIF, SMART, TD and signal evidence."""
    result: Dict[str, Any] = {
        "from_text": from_text, "to_text": to_text, "date": on_date.isoformat() if on_date else None,
        "stations": {}, "problems": [], "ambiguous": {}, "orientation": None,
        "views": {}, "locations": [], "berths": [], "topology": [], "gaps": [],
    }
    res_a, res_b = resolve_station(conn, from_text), resolve_station(conn, to_text)
    for side, text, res in (("a", from_text, res_a), ("b", to_text, res_b)):
        if res["status"] == "ok":
            result["stations"][side] = res["station"]
        elif res["status"] == "ambiguous":
            result["ambiguous"][side] = res["candidates"]
            result["problems"].append(f"'{text}' matches several stations - please choose one.")
        elif res["status"] == "unknown":
            result["problems"].append(
                f"Station '{text}' was not found in CORPUS reference data or CIF locations."
            )
        else:
            result["problems"].append("Both a From and a To station are required.")
    if result["problems"]:
        return result
    sa, sb = result["stations"]["a"], result["stations"]["b"]
    if sa["key"] == sb["key"]:
        result["problems"].append("From and To resolve to the same station.")
        return result

    # Orientation: only labelled Up/Down when curated direction metadata exists.
    hint = KNOWN_DIRECTION_HINTS.get(((sa["crs"] or ""), (sb["crs"] or "")))
    rev = KNOWN_DIRECTION_HINTS.get(((sb["crs"] or ""), (sa["crs"] or "")))
    a_to_b = hint or ({"down": "up", "up": "down"}[rev] if rev else None)
    if a_to_b:
        b_to_a = "up" if a_to_b == "down" else "down"
        keys = {"a_to_b": a_to_b, "b_to_a": b_to_a}
        result["orientation"] = {
            "known": True, "keys": keys,
            "note": "Up = towards London, Down = away from London. Orientation comes from a curated "
                    "station-pair hint, not from the timetable or TD data.",
        }
    else:
        keys = {"a_to_b": "a_to_b", "b_to_a": "b_to_a"}
        result["orientation"] = {
            "known": False, "keys": keys,
            "note": "No Up/Down orientation metadata is known for this station pair, so directions are "
                    "shown by travel direction only. Which is Up (towards London) is not assumed.",
        }
    view_label = {
        keys["a_to_b"]: DIRECTION_LABELS.get(keys["a_to_b"], f"{sa['name'] or sa['key']} \u2192 {sb['name'] or sb['key']}"),
        keys["b_to_a"]: DIRECTION_LABELS.get(keys["b_to_a"], f"{sb['name'] or sb['key']} \u2192 {sa['name'] or sa['key']}"),
    }
    for dkey in (keys["a_to_b"], keys["b_to_a"]):
        result["views"][dkey] = {
            "key": dkey, "label": view_label[dkey], "trains": [], "movements": [],
            "travel": "a_to_b" if dkey == keys["a_to_b"] else "b_to_a",
        }

    # --- CIF ---------------------------------------------------------------
    try:
        schedules = find_corridor_schedules(conn, sa["tiplocs"], sb["tiplocs"], on_date)
    except DB_ERRORS as e:
        schedules = []
        result["gaps"].append(f"CIF schedule data could not be read: {e}")
    if not schedules:
        result["gaps"].append(
            "No CIF schedules calling at/passing both stations were found"
            + (f" for {on_date.isoformat()}" if on_date else "")
            + ". Download CIF schedules (see docs) to populate timetable context."
        )

    endpoint_tiplocs = set(sa["tiplocs"]) | set(sb["tiplocs"])
    loc_counts: Dict[str, int] = {}
    seq_best: List[str] = []
    details: Dict[tuple, List[Any]] = {}
    for sched in schedules:
        try:
            locs = _load_schedule_detail(conn, sched)
        except DB_ERRORS:
            continue
        details[(sched["uid"], sched["schedule_start_date"])] = locs
        lo, hi = sorted((sched["idx_a"], sched["idx_b"]))
        between = [l for l in locs if lo <= l["location_index"] <= hi and l["tiploc"]]
        seq = [l["tiploc"] for l in between]
        if sched["direction"] == "b_to_a":
            seq.reverse()
        if len(seq) > len(seq_best):
            seq_best = seq
        for t in set(seq):
            loc_counts[t] = loc_counts.get(t, 0) + 1
    ordered_tiplocs = list(seq_best) + [t for t in loc_counts if t not in seq_best]
    info = _tiploc_info(conn, ordered_tiplocs + [
        l["tiploc"] for locs in details.values() for l in (locs[:1] + locs[-1:]) if l["tiploc"]
    ])
    for t in ordered_tiplocs:
        i = info.get(t, {})
        result["locations"].append({
            "tiploc": t, "name": i.get("name"), "crs": i.get("crs"), "stanox": i.get("stanox"),
            "is_endpoint": t in endpoint_tiplocs, "schedule_count": loc_counts.get(t, 0),
        })

    headcode_dirs: Dict[str, set] = {}
    trains_by_view: Dict[str, List[dict]] = {k: [] for k in result["views"]}
    for sched in schedules:
        locs = details.get((sched["uid"], sched["schedule_start_date"]))
        if not locs:
            continue
        la = next((l for l in locs if l["location_index"] == sched["idx_a"]), None)
        lb = next((l for l in locs if l["location_index"] == sched["idx_b"]), None)
        ta, kinda = _best_time(la) if la else (None, None)
        tb, kindb = _best_time(lb) if lb else (None, None)
        dkey = keys[sched["direction"]]
        first, last = locs[0]["tiploc"], locs[-1]["tiploc"]
        if sched["headcode"]:
            headcode_dirs.setdefault(sched["headcode"], set()).add(dkey)
        trains_by_view[dkey].append({
            "headcode": sched["headcode"], "uid": sched["uid"], "toc_code": sched["toc_code"],
            "stp": sched["stp"],
            "origin": (info.get(first, {}).get("name") or first),
            "destination": (info.get(last, {}).get("name") or last),
            "time_a": ta, "kind_a": kinda, "time_b": tb, "kind_b": kindb,
            "live": [],
        })

    # --- Corridor berths (SMART reference) ---------------------------------
    stanoxes = sorted({
        s for s in (
            list(sa["stanoxes"]) + list(sb["stanoxes"])
            + [l["stanox"] for l in result["locations"] if l["stanox"]]
        ) if s
    })
    berths: List[dict] = []
    if stanoxes and _has_table(conn, "smart_berths"):
        try:
            for chunk in _chunks(stanoxes):
                for r in conn.execute(
                    "SELECT td_area, berth, stanox, platform, stanme FROM smart_berths "
                    f"WHERE stanox IN ({_marks(len(chunk))}) ORDER BY td_area, berth",
                    chunk,
                ):
                    berths.append({
                        "td_area": r["td_area"], "berth": r["berth"], "stanox": r["stanox"],
                        "platform": r["platform"], "station": r["stanme"],
                    })
        except DB_ERRORS as e:
            result["gaps"].append(f"SMART berth data could not be read: {e}")
    # SMART rows can repeat a berth for several steps; keep one row per area/berth.
    uniq: Dict[tuple, dict] = {}
    for b in berths:
        uniq.setdefault((b["td_area"], b["berth"]), b)
    berths = list(uniq.values())
    result["berths"] = berths
    if not berths:
        result["gaps"].append(
            "No SMART berth reference data tags berths to the corridor's stations (STANOX), so no "
            "corridor berths can be identified. CIF alone cannot determine berths."
        )
    berth_station = {(b["td_area"], b["berth"]): b for b in berths}
    areas = sorted({b["td_area"] for b in berths})
    berths_by_area: Dict[str, List[str]] = {}
    for b in berths:
        berths_by_area.setdefault(b["td_area"], []).append(b["berth"])

    # --- Observed TD movements ---------------------------------------------
    movements: Dict[tuple, dict] = {}
    if berths and _has_table(conn, "td_berth_movements"):
        try:
            for area, blist in berths_by_area.items():
                for chunk in _chunks(blist, _CHUNK // 2):
                    m = _marks(len(chunk))
                    for r in conn.execute(
                        "SELECT td_area, from_berth, to_berth, COUNT(*) AS n, MAX(ts_iso) AS last_ts, "
                        "GROUP_CONCAT(DISTINCT headcode) AS hcs FROM td_berth_movements "
                        f"WHERE td_area=? AND (from_berth IN ({m}) OR to_berth IN ({m})) "
                        "GROUP BY td_area, from_berth, to_berth ORDER BY last_ts DESC LIMIT 2000",
                        [area] + chunk + chunk,
                    ):
                        k = (r["td_area"], r["from_berth"], r["to_berth"])
                        mv = movements.setdefault(k, {
                            "td_area": r["td_area"], "from_berth": r["from_berth"],
                            "to_berth": r["to_berth"], "count": 0, "last_seen": r["last_ts"],
                            "headcodes": set(),
                        })
                        mv["count"] += r["n"]
                        if r["last_ts"] and (not mv["last_seen"] or r["last_ts"] > mv["last_seen"]):
                            mv["last_seen"] = r["last_ts"]
                        mv["headcodes"].update(h.upper() for h in (r["hcs"] or "").split(",") if h)
        except DB_ERRORS as e:
            result["gaps"].append(f"TD berth movements could not be read: {e}")
        if not movements:
            result["gaps"].append(
                "No observed TD berth movements touch the corridor berths. Run the listener with "
                "--db-path (TD feed) to accumulate movements."
            )
    elif berths:
        result["gaps"].append("td_berth_movements table is missing; no observed berth movements available.")

    # Signals: physical mappings and S-class correlation scores.
    phys: Dict[tuple, List[dict]] = {}
    sclass: Dict[tuple, dict] = {}
    try:
        if areas and _has_table(conn, "physical_signal_mappings"):
            for r in conn.execute(
                "SELECT td_area, from_berth, to_berth, address, byte_offset, bit, physical_signal_number, "
                "physical_signal_location, mapping_confidence, verification_status, source "
                f"FROM physical_signal_mappings WHERE td_area IN ({_marks(len(areas))}) "
                "AND verification_status<>'revoked' ORDER BY updated_at_utc DESC LIMIT 2000",
                areas,
            ):
                for berth in (r["from_berth"], r["to_berth"]):
                    if berth:
                        phys.setdefault((r["td_area"], berth), []).append(dict(r))
        if movements and _has_table(conn, "td_sclass_movement_scores"):
            for area, blist in berths_by_area.items():
                for chunk in _chunks(blist, _CHUNK // 2):
                    m = _marks(len(chunk))
                    for r in conn.execute(
                        "SELECT td_area, from_berth, to_berth, correlation_pct, observation_count, "
                        f"associated_bits_json FROM td_sclass_movement_scores WHERE td_area=? "
                        f"AND (from_berth IN ({m}) OR to_berth IN ({m}))",
                        [area] + chunk + chunk,
                    ):
                        sclass[(r["td_area"], r["from_berth"], r["to_berth"])] = dict(r)
    except DB_ERRORS as e:
        result["gaps"].append(f"Signal evidence could not be read: {e}")

    def _signals_for(mv: dict) -> List[dict]:
        out = []
        seen = set()
        for berth in (mv["from_berth"], mv["to_berth"]):
            for p in phys.get((mv["td_area"], berth), []):
                pk = (p["address"], p["byte_offset"], p["bit"], p["physical_signal_number"])
                if pk in seen:
                    continue
                seen.add(pk)
                out.append({
                    "kind": "physical", "label": p["physical_signal_number"] or f"{p['address']}.{p['bit']}",
                    "location": p["physical_signal_location"], "status": p["verification_status"],
                    "confidence": p["mapping_confidence"], "source": p["source"] or "physical_signal_mappings",
                })
        sc = sclass.get((mv["td_area"], mv["from_berth"], mv["to_berth"]))
        if sc:
            out.append({
                "kind": "sclass", "label": "S-class bits", "location": None, "status": "inferred",
                "confidence": (sc["correlation_pct"] or 0.0) / 100.0,
                "source": f"td_sclass_movement_scores ({sc['observation_count']} obs)",
            })
        return out

    unassigned: List[dict] = []
    for mv in sorted(movements.values(), key=lambda m: m["last_seen"] or "", reverse=True):
        dirs = set()
        for hc in mv["headcodes"]:
            dirs |= headcode_dirs.get(hc, set())
        fb = berth_station.get((mv["td_area"], mv["from_berth"]))
        tb = berth_station.get((mv["td_area"], mv["to_berth"]))
        row = {
            "td_area": mv["td_area"], "from_berth": mv["from_berth"], "to_berth": mv["to_berth"],
            "from_station": fb["station"] if fb else None, "to_station": tb["station"] if tb else None,
            "count": mv["count"], "last_seen": mv["last_seen"],
            "headcodes": sorted(mv["headcodes"])[:8], "signals": _signals_for(mv),
            "direction_basis": None,
        }
        if len(dirs) == 1:
            dkey = next(iter(dirs))
            row["direction_basis"] = "headcode matches CIF schedules running in this direction"
            result["views"][dkey]["movements"].append(row)
        else:
            row["direction_basis"] = (
                "headcode matches CIF schedules in both directions" if dirs
                else "no corridor CIF schedule matches the observed headcodes"
            )
            unassigned.append(row)
    result["unassigned_movements"] = unassigned
    if movements and not any(m["signals"] for m in unassigned + [
        mv for v in result["views"].values() for mv in v["movements"]
    ]):
        result["gaps"].append(
            "No signal evidence (physical mappings or S-class correlations) is available for the "
            "observed corridor movements."
        )

    # --- Live trains (td_state) --------------------------------------------
    live_rows: List[Any] = []
    try:
        if _has_table(conn, "td_state") and (headcode_dirs or berths):
            conds, params = [], []
            hcs = sorted(headcode_dirs)
            for chunk in _chunks(hcs):
                conds.append(f"headcode IN ({_marks(len(chunk))})")
                params.extend(chunk)
            for area, blist in berths_by_area.items():
                for chunk in _chunks(blist, _CHUNK // 2):
                    m = _marks(len(chunk))
                    conds.append(f"(td_area=? AND to_berth IN ({m}))")
                    params.append(area)
                    params.extend(chunk)
            if conds:
                live_rows = conn.execute(
                    "SELECT td_area, headcode, to_berth, from_berth, last_time_iso, location_name "
                    "FROM td_state WHERE " + " OR ".join(conds) + " ORDER BY last_time_ms DESC LIMIT 1000",
                    params,
                ).fetchall()
    except DB_ERRORS as e:
        result["gaps"].append(f"Live TD state could not be read: {e}")
    unscheduled_live: List[dict] = []
    for r in live_rows:
        in_corridor = (r["td_area"], r["to_berth"]) in berth_station
        live = {
            "td_area": r["td_area"], "headcode": r["headcode"], "berth": r["to_berth"],
            "from_berth": r["from_berth"], "last_seen": r["last_time_iso"],
            "location": r["location_name"], "in_corridor_berth": in_corridor,
        }
        dirs = headcode_dirs.get((r["headcode"] or "").upper(), set())
        if not dirs:
            if in_corridor:
                unscheduled_live.append(live)
            continue
        if not in_corridor and areas and r["td_area"] not in areas:
            continue  # same headcode seen in an unrelated TD area: not shown as corridor traffic
        for dkey in dirs:
            for t in trains_by_view[dkey]:
                if t["headcode"] == (r["headcode"] or "").upper():
                    t["live"].append(live)
    for dkey, trains in trains_by_view.items():
        result["views"][dkey]["trains"] = trains
    result["unscheduled_live"] = unscheduled_live

    # --- Reconstructed topology (hypotheses only) --------------------------
    try:
        if berths and _has_table(conn, "topology_edges"):
            for area, blist in berths_by_area.items():
                ids = [f"{area}:berth:{b}" for b in blist]
                for chunk in _chunks(ids, _CHUNK // 2):
                    m = _marks(len(chunk))
                    for r in conn.execute(
                        "SELECT td_area, from_node_id, relationship, to_node_id, confidence, "
                        "verification_status, provenance FROM topology_edges "
                        f"WHERE td_area=? AND (from_node_id IN ({m}) OR to_node_id IN ({m})) "
                        "AND verification_status<>'rejected' ORDER BY confidence DESC LIMIT 300",
                        [area] + chunk + chunk,
                    ):
                        result["topology"].append(dict(r))
    except DB_ERRORS as e:
        result["gaps"].append(f"Topology data could not be read: {e}")
    if berths and not result["topology"]:
        result["gaps"].append(
            "No reconstructed topology links exist for the corridor berths; berth-to-berth connectivity "
            "is limited to directly observed movements and gaps are left unfilled."
        )
    return result


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _status_badge(status: Optional[str]) -> str:
    s = (status or "unknown").lower()
    cls = {"confirmed": "confirmed", "inferred": "inferred", "reviewed": "reviewed",
           "probable": "probable"}.get(s, "unknown")
    return f"<span class='badge {cls}'>{_e(s)}</span>"


def _pct(conf: Optional[float]) -> str:
    return f"{conf:.0%}" if conf is not None else "n/a"


def _station_label(loc: dict) -> str:
    name = loc.get("name") or loc.get("tiploc")
    extra = loc.get("crs") or ""
    return f"{_e(name)}" + (f" <span class='dim'>({_e(extra)})</span>" if extra else "")


def _station_choice_links(side: str, candidates: List[dict], from_text: str, to_text: str,
                          direction: str, date: str) -> str:
    links = []
    for c in candidates:
        value = c.get("crs") or (c["tiplocs"][0] if c.get("tiplocs") else c.get("name") or "")
        params = {"from_station": from_text, "to_station": to_text, "direction": direction}
        if date:
            params["date"] = date
        params["from_station" if side == "a" else "to_station"] = value
        links.append(
            f"<li><a href='/signal-mappings?{_e(urllib.parse.urlencode(params))}'>"
            f"{_e(c.get('name') or c['key'])}</a> <span class='dim'>"
            f"{_e(c.get('crs') or '')} {_e(', '.join(c.get('tiplocs', [])[:4]))}</span></li>"
        )
    return "<ul>" + "".join(links) + "</ul>"


def render_corridor_form(from_text: str, to_text: str, direction: str, date: str,
                         suggestions: Iterable[Any] = ()) -> str:
    opts = "".join(
        f"<option value='{_e(crs)}'>{_e(name)}</option>" for crs, name in suggestions
    )
    sel = lambda v: " selected" if direction == v else ""
    return (
        "<div style='background:#f7f9fc;padding:12px;border-radius:6px;margin:12px 0'>"
        "<h3 style='margin-top:0'>Corridor explorer: trains, berths and signals between two stations</h3>"
        "<form method='get' action='/signal-mappings' style='display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end'>"
        f"<label>From station<br><input name='from_station' list='corridor-stations' value='{_e(from_text)}' "
        "placeholder='e.g. Faversham or FAV' style='padding:6px'></label>"
        f"<label>To station<br><input name='to_station' list='corridor-stations' value='{_e(to_text)}' "
        "placeholder='e.g. Ramsgate or RAM' style='padding:6px'></label>"
        "<label>Direction<br><select name='direction' style='padding:6px'>"
        f"<option value='both'{sel('both')}>Both</option>"
        f"<option value='up'{sel('up')}>Up</option><option value='down'{sel('down')}>Down</option>"
        f"<option value='a_to_b'{sel('a_to_b')}>From \u2192 To</option>"
        f"<option value='b_to_a'{sel('b_to_a')}>To \u2192 From</option></select></label>"
        f"<label>Timetable date<br><input name='date' value='{_e(date)}' placeholder='YYYY-MM-DD' size='11' style='padding:6px'></label>"
        "<button type='submit' style='padding:7px 14px;background:#0b5cff;color:white;border:0;border-radius:6px'>Show corridor</button>"
        "</form>"
        f"<datalist id='corridor-stations'>{opts}</datalist>"
        "</div>"
    )


def render_corridor_html(result: dict, direction: str = "both") -> str:
    """Render a corridor result. All dynamic values are HTML-escaped."""
    parts: List[str] = []
    date = result.get("date") or ""
    if result["problems"]:
        for p in result["problems"]:
            parts.append(f"<p style='color:#b45309'><b>{_e(p)}</b></p>")
        for side, cands in result["ambiguous"].items():
            label = "From" if side == "a" else "To"
            parts.append(f"<p>{label} station candidates:</p>" + _station_choice_links(
                side, cands, result["from_text"], result["to_text"], direction, date))
        return "".join(parts)

    sa, sb = result["stations"]["a"], result["stations"]["b"]
    parts.append(
        f"<h3>{_e(sa['name'] or sa['key'])} \u2194 {_e(sb['name'] or sb['key'])}</h3>"
    )
    orient = result["orientation"]
    parts.append(f"<p class='dim'><b>Direction:</b> {_e(orient['note'])}</p>")
    parts.append(
        "<p class='dim'><b>Evidence legend:</b> <span class='badge confirmed'>observed</span> TD feed / "
        "CIF timetable facts; <span class='badge inferred'>inferred</span> correlation or reconstructed "
        "hypotheses. CIF alone does not determine berth order, track connectivity, signal placement or "
        "direction.</p>"
    )
    if result["gaps"]:
        parts.append("<div style='background:#fff7ed;border-left:4px solid #f59e0b;padding:8px 12px;margin:8px 0'>"
                     "<b>Insufficient evidence / gaps</b><ul>"
                     + "".join(f"<li>{_e(g)}</li>" for g in result["gaps"]) + "</ul></div>")

    if result["locations"]:
        parts.append("<h4>Locations between the stations (CIF order, timetable only)</h4><p>")
        endpoint_style = " style='background:#dbeafe'"
        parts.append(" \u2192 ".join(
            "<span class='pill'" + (endpoint_style if l["is_endpoint"] else "") + ">"
            f"{_station_label(l)} <span class='dim'>\u00d7{l['schedule_count']}</span></span>"
            for l in result["locations"]
        ))
        parts.append("</p>")

    if result["berths"]:
        parts.append("<h4>Berths tagged to corridor locations (SMART reference)</h4>"
                     "<p class='dim'>Berth-to-station association only; this does not give berth order.</p>"
                     "<p>" + " ".join(
                         f"<span class='pill mono'>{_e(b['td_area'])}:{_e(b['berth'])} "
                         f"<span class='dim'>{_e(b['station'] or b['stanox'])}</span></span>"
                         for b in result["berths"][:200]) + "</p>")

    wanted = [k for k in result["views"]
              if direction in ("both", "", None) or direction == k
              or direction == result["views"][k]["travel"]]
    if not wanted:
        wanted = list(result["views"])
    for key in wanted:
        view = result["views"][key]
        parts.append(f"<h3 style='margin-top:24px'>{_e(view['label'])}</h3>")
        parts.append(_render_trains(view["trains"]))
        parts.append(_render_movements(view["movements"], "Observed berth movements (TD)"))
    if result.get("unassigned_movements"):
        parts.append(_render_movements(
            result["unassigned_movements"],
            "Observed movements with unresolved direction"))
    if result.get("unscheduled_live"):
        parts.append("<h4>Live trains in corridor berths without a matching corridor schedule</h4><table>"
                     "<tr><th>Headcode</th><th>TD area</th><th>Berth</th><th>Last seen</th></tr>"
                     + "".join(
                         f"<tr><td>{_e(t['headcode'])}</td><td>{_e(t['td_area'])}</td>"
                         f"<td class='mono'>{_e(t['berth'])}</td><td>{_e(t['last_seen'])}</td></tr>"
                         for t in result["unscheduled_live"][:100]) + "</table>")
    if result["topology"]:
        parts.append("<h4>Reconstructed topology links (hypotheses)</h4><table style='font-size:13px'>"
                     "<tr><th>From</th><th>Relationship</th><th>To</th><th>Status</th><th>Confidence</th>"
                     "<th>Source</th></tr>" + "".join(
                         f"<tr><td class='mono'>{_e(t['from_node_id'])}</td><td>{_e(t['relationship'])}</td>"
                         f"<td class='mono'>{_e(t['to_node_id'])}</td><td>{_status_badge(t['verification_status'])}</td>"
                         f"<td>{_pct(t['confidence'])}</td><td>{_e(t['provenance'])}</td></tr>"
                         for t in result["topology"][:100]) + "</table>")
    return "".join(parts)


def _render_trains(trains: List[dict]) -> str:
    if not trains:
        return "<p><i>No scheduled trains found in this direction.</i></p>"
    rows = []
    for t in trains[:200]:
        if t["live"]:
            live = "<br>".join(
                f"<span class='badge confirmed'>observed</span> {_e(l['td_area'])}:<span class='mono'>{_e(l['berth'])}</span>"
                f" <span class='dim'>{_e(l['last_seen'] or '')}"
                + ("" if l["in_corridor_berth"] else " (headcode match only; berth not tagged to corridor)")
                + "</span>"
                for l in t["live"][:3])
        else:
            live = "<span class='dim'>not currently observed</span>"
        rows.append(
            f"<tr><td><b>{_e(t['headcode'] or '?')}</b></td><td class='mono'>{_e(t['uid'])}</td>"
            f"<td>{_e(t['toc_code'])}</td><td>{_e(t['origin'])} \u2192 {_e(t['destination'])}</td>"
            f"<td>{_e(t['time_a'] or '')} <span class='dim'>{_e(t['kind_a'] or '')}</span></td>"
            f"<td>{_e(t['time_b'] or '')} <span class='dim'>{_e(t['kind_b'] or '')}</span></td>"
            f"<td>{live}</td></tr>"
        )
    return ("<h4>Scheduled trains (CIF) and live TD position</h4><table style='font-size:13px'>"
            "<tr><th>Headcode</th><th>UID</th><th>TOC</th><th>Schedule</th><th>At From</th>"
            "<th>At To</th><th>Live / last berth</th></tr>" + "".join(rows) + "</table>")


def _render_movements(movements: List[dict], title: str) -> str:
    if not movements:
        return f"<h4>{_e(title)}</h4><p><i>None observed.</i></p>"
    rows = []
    for m in movements[:200]:
        sigs = "<br>".join(
            f"{_status_badge(s['status'])} {_e(s['label'])}"
            + (f" <span class='dim'>{_e(s['location'])}</span>" if s.get("location") else "")
            + f" <span class='dim'>conf {_pct(s['confidence'])}, {_e(s['source'])}</span>"
            for s in m["signals"][:5]
        ) or "<span class='dim'>no signal evidence</span>"
        rows.append(
            f"<tr><td>{_e(m['td_area'])}</td>"
            f"<td class='mono'>{_e(m['from_berth'])}</td><td class='mono'>{_e(m['to_berth'])}</td>"
            f"<td>{_e(m['from_station'] or '')} \u2192 {_e(m['to_station'] or '')}</td>"
            f"<td>{m['count']}</td><td>{_e(m['last_seen'])}</td>"
            f"<td>{_e(', '.join(m['headcodes']))}<br><span class='dim'>{_e(m['direction_basis'])}</span></td>"
            f"<td>{sigs}</td></tr>"
        )
    return (f"<h4>{_e(title)}</h4><table style='font-size:13px'>"
            "<tr><th>Area</th><th>From berth</th><th>To berth</th><th>Stations (SMART)</th><th>Count</th>"
            "<th>Last seen</th><th>Headcodes / direction basis</th><th>Signals</th></tr>"
            + "".join(rows) + "</table>")
