"""Candidate berth-sequence discovery and live card state.

Evidence used (all read-only):

* ``corpus_locations`` / ``smart_berths``  - resolve stations to TD berths
* ``td_berth_movements`` (or ``td_berth_events``) - observed berth-to-berth steps
* ``headcode_route_patterns``              - historical patterns, used only as a
  fallback; berths/links not directly observed are marked ``inferred``
* ``cif_schedule_locations``               - how many schedules call at both stations
* ``td_state``                             - current occupancy
* ``physical_signal_mappings``             - reviewed signal locations (no live aspect)
"""

from __future__ import annotations

import heapq
import json
import math
import sqlite3
import time
from collections import defaultdict
from typing import Any, Optional

MAX_CANDIDATES = 5
MAX_SEQUENCE_LEN = 200
TRUSTED_SIGNAL_STATUSES = ("confirmed", "corrected")


def _exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)
    ).fetchone() is not None


def resolve_stations(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Match a station by CRS, STANOX, TIPLOC or name fragment."""
    query = (query or "").strip()
    if not query:
        return []
    found: dict[str, dict[str, Any]] = {}
    if _exists(conn, "corpus_locations"):
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT stanox, MAX(name) AS name, MAX(crs) AS crs, GROUP_CONCAT(DISTINCT tiploc) AS tiplocs
            FROM corpus_locations
            WHERE stanox IS NOT NULL AND stanox != ''
              AND (UPPER(crs) = UPPER(?) OR stanox = ? OR UPPER(tiploc) = UPPER(?) OR name LIKE ?)
            GROUP BY stanox
            ORDER BY (UPPER(crs) = UPPER(?)) DESC, name
            LIMIT ?
            """,
            (query, query, query, like, query, limit),
        ).fetchall()
        for r in rows:
            found[r[0]] = {"stanox": r[0], "name": r[1], "crs": r[2],
                           "tiplocs": [t for t in (r[3] or "").split(",") if t]}
    if not found and _exists(conn, "smart_berths"):
        rows = conn.execute(
            """
            SELECT stanox, MAX(stanme) FROM smart_berths
            WHERE stanox IS NOT NULL AND (stanox = ? OR stanme LIKE ?)
            GROUP BY stanox LIMIT ?
            """,
            (query, f"%{query}%", limit),
        ).fetchall()
        for r in rows:
            found[r[0]] = {"stanox": r[0], "name": r[1], "crs": None, "tiplocs": []}
    return list(found.values())


def station_berths(conn: sqlite3.Connection, stanoxes: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Map ``{td_area: {berth: info}}`` for berths SMART associates with the stations."""
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    if not stanoxes or not _exists(conn, "smart_berths"):
        return out
    marks = ",".join("?" * len(stanoxes))
    for area, berth, stanox, platform in conn.execute(
        f"SELECT td_area, berth, stanox, platform FROM smart_berths WHERE stanox IN ({marks})",
        stanoxes,
    ).fetchall():
        out[area][berth] = {"stanox": stanox, "platform": platform}
    return out


def berth_labels(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Station/platform labels for ``(td_area, berth)`` pairs where SMART/CORPUS know them."""
    labels: dict[tuple[str, str], dict[str, Any]] = {}
    if not items or not _exists(conn, "smart_berths"):
        return labels
    has_corpus = _exists(conn, "corpus_locations")
    for area in {i["td_area"] for i in items}:
        berths = sorted({i["berth"] for i in items if i["td_area"] == area})
        marks = ",".join("?" * len(berths))
        rows = conn.execute(
            f"SELECT berth, stanox, platform, stanme FROM smart_berths WHERE td_area = ? AND berth IN ({marks})",
            [area, *berths],
        ).fetchall()
        names: dict[str, str] = {}
        if has_corpus:
            stanoxes = sorted({r[1] for r in rows if r[1]})
            if stanoxes:
                m2 = ",".join("?" * len(stanoxes))
                names = {
                    s: n for s, n in conn.execute(
                        f"SELECT stanox, MAX(name) FROM corpus_locations WHERE stanox IN ({m2}) GROUP BY stanox",
                        stanoxes,
                    ).fetchall()
                }
        for berth, stanox, platform, stanme in rows:
            station = names.get(stanox) or stanme
            if station or platform:
                labels[(area, berth)] = {"station": station, "stanox": stanox, "platform": platform}
    return labels


def _observed_edges(conn: sqlite3.Connection, area: str) -> dict[tuple[str, str], int]:
    if _exists(conn, "td_berth_movements"):
        sql = """SELECT from_berth, to_berth, COUNT(*) FROM td_berth_movements
                 WHERE td_area = ? AND from_berth != to_berth GROUP BY from_berth, to_berth"""
    elif _exists(conn, "td_berth_events"):
        sql = """SELECT from_berth, to_berth, COUNT(*) FROM td_berth_events
                 WHERE td_area = ? AND from_berth IS NOT NULL AND to_berth IS NOT NULL
                   AND from_berth != '' AND to_berth != '' AND from_berth != to_berth
                 GROUP BY from_berth, to_berth"""
    else:
        return {}
    return {(f, t): c for f, t, c in conn.execute(sql, (area,)).fetchall()}


def _shortest_path(
    adj: dict[str, list[tuple[str, int]]],
    sources: set[str],
    targets: set[str],
    banned: frozenset = frozenset(),
) -> Optional[list[str]]:
    """Dijkstra preferring fewer hops, tie-broken towards well-observed steps."""
    heap: list[tuple[float, str]] = [(0.0, s) for s in sorted(sources)]
    heapq.heapify(heap)
    dist = {s: 0.0 for s in sources}
    prev: dict[str, str] = {}
    done: set[str] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if node in targets:
            path = [node]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            path.reverse()
            return path if len(path) <= MAX_SEQUENCE_LEN else None
        for nxt, count in adj.get(node, ()):
            if (node, nxt) in banned or nxt in done:
                continue
            nd = d + 1.0 + 1.0 / count
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                prev[nxt] = node
                heapq.heappush(heap, (nd, nxt))
    return None


def _confidence(min_support: int) -> float:
    return round(min_support / (min_support + 2.0), 2)


def _observed_candidates(area: str, edges: dict[tuple[str, str], int],
                         origins: set[str], dests: set[str]) -> list[dict[str, Any]]:
    adj: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (f, t), c in edges.items():
        adj[f].append((t, c))
    best = _shortest_path(adj, origins, dests)
    if not best:
        return []
    paths = [best]
    for edge in zip(best, best[1:]):
        alt = _shortest_path(adj, origins, dests, frozenset([edge]))
        if alt and alt not in paths:
            paths.append(alt)
    out = []
    for path in paths:
        supports = [edges[e] for e in zip(path, path[1:])] or [0]
        out.append({
            "td_area": area,
            "method": "td_observed_movements",
            "confidence": _confidence(min(supports)),
            "min_step_observations": min(supports),
            "total_step_observations": sum(supports),
            "sequence": [
                {"berth": b, "td_area": area, "provenance": "observed", "source": "td_berth_movements"}
                for b in path
            ],
        })
    return out


def _pattern_candidates(conn: sqlite3.Connection, area: str, edges: dict[tuple[str, str], int],
                        origins: set[str], dests: set[str]) -> list[dict[str, Any]]:
    if not _exists(conn, "headcode_route_patterns"):
        return []
    seen_nodes = {n for e in edges for n in e}
    found: dict[tuple, dict[str, Any]] = {}
    for seq_json, obs, conf in conn.execute(
        """SELECT berth_sequence_json, observations, confidence FROM headcode_route_patterns
           ORDER BY observations DESC LIMIT 5000"""
    ).fetchall():
        try:
            seq = json.loads(seq_json)
        except ValueError:
            continue
        if not isinstance(seq, list):
            continue
        for i, b in enumerate(seq):
            if b not in origins:
                continue
            j = next((k for k in range(i, len(seq)) if seq[k] in dests), None)
            if j is None:
                continue
            path = tuple(seq[i:j + 1])
            if len(path) > MAX_SEQUENCE_LEN:
                break
            entry = found.setdefault(path, {"obs": 0, "conf": float(conf or 0)})
            entry["obs"] += int(obs or 0)
            break
    out = []
    for path, info in found.items():
        items = []
        for idx, b in enumerate(path):
            link_ok = idx == 0 or (path[idx - 1], b) in edges
            observed = b in seen_nodes and link_ok
            items.append({
                "berth": b, "td_area": area,
                "provenance": "observed" if observed else "inferred",
                "source": "td_berth_movements" if observed else "headcode_route_patterns",
            })
        out.append({
            "td_area": area,
            "method": "historical_route_pattern",
            "confidence": round(min(info["conf"], 0.9), 2),
            "pattern_observations": info["obs"],
            "inferred_items": sum(1 for i in items if i["provenance"] == "inferred"),
            "sequence": items,
        })
    out.sort(key=lambda c: (-c["pattern_observations"], len(c["sequence"])))
    return out[:MAX_CANDIDATES]


def _schedule_support(conn: sqlite3.Connection, a: dict[str, Any], b: dict[str, Any]) -> Optional[int]:
    if not _exists(conn, "cif_schedule_locations"):
        return None
    ta, tb = a.get("tiplocs") or [], b.get("tiplocs") or []
    if not ta or not tb:
        return None
    ma, mb = ",".join("?" * len(ta)), ",".join("?" * len(tb))
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT x.uid) FROM cif_schedule_locations x
        JOIN cif_schedule_locations y ON y.uid = x.uid
         AND y.schedule_start_date = x.schedule_start_date
         AND y.segment_index = x.segment_index AND y.location_index > x.location_index
        WHERE x.tiploc IN ({ma}) AND y.tiploc IN ({mb})
        """,
        [*ta, *tb],
    ).fetchone()
    return int(row[0])


def chain_sequence(conn: sqlite3.Connection, td_area: str, headcode: str) -> dict[str, Any]:
    """Ordered, de-duplicated berth sequence taken from one train's observed TD events.

    Used to seed a manually edited card; every item is ``observed``.
    """
    area, hc = (td_area or "").strip(), (headcode or "").strip()
    notes: list[str] = []
    steps: list[str] = []
    if area and hc and _exists(conn, "td_berth_events"):
        for fb, tb in conn.execute(
            """SELECT from_berth, to_berth FROM td_berth_events
               WHERE td_area = ? AND headcode = ? ORDER BY ts_ms ASC, id ASC""",
            (area, hc),
        ).fetchall():
            for b in (fb, tb):
                if b and (not steps or steps[-1] != b):
                    steps.append(b)
    if not steps:
        notes.append("No TD berth events found for that area and headcode.")
    steps = steps[:MAX_SEQUENCE_LEN]
    items = [{"berth": b, "td_area": area, "provenance": "observed", "source": "td_berth_events"} for b in steps]
    labels = berth_labels(conn, items)
    for item in items:
        item.update({k: v for k, v in (labels.get((area, item["berth"])) or {}).items() if v})
    return {"td_area": area, "headcode": hc, "sequence": items, "notes": notes}


def _revision(conn: sqlite3.Connection) -> Optional[str]:
    parts = []
    if _exists(conn, "td_berth_events"):
        parts.append(str(conn.execute("SELECT MAX(id) FROM td_berth_events").fetchone()[0]))
    if _exists(conn, "td_state"):
        r = conn.execute("SELECT MAX(last_time_ms), COUNT(*) FROM td_state").fetchone()
        parts += [str(r[0]), str(r[1])]
    return ":".join(parts) if parts else None


def suggest_sequences(conn: sqlite3.Connection, from_station: str, to_station: str) -> dict[str, Any]:
    """Return ranked candidate berth sequences between two stations, with provenance."""
    result: dict[str, Any] = {
        "from_station": from_station, "to_station": to_station,
        "from_matches": [], "to_matches": [], "candidates": [], "notes": [],
    }
    from_m = resolve_stations(conn, from_station)
    to_m = resolve_stations(conn, to_station)
    result["from_matches"], result["to_matches"] = from_m, to_m
    if not from_m or not to_m:
        result["notes"].append("Could not resolve both stations from CORPUS/SMART reference data.")
        return result
    fb = station_berths(conn, [m["stanox"] for m in from_m])
    tb = station_berths(conn, [m["stanox"] for m in to_m])
    shared = sorted(set(fb) & set(tb))
    if not shared:
        result["notes"].append(
            "No TD area has SMART berths for both stations; a berth sequence cannot be suggested."
        )
        return result

    candidates: list[dict[str, Any]] = []
    for area in shared:
        edges = _observed_edges(conn, area)
        if not edges:
            result["notes"].append(f"No observed TD berth movements for area {area}.")
        origins, dests = set(fb[area]), set(tb[area])
        found = _observed_candidates(area, edges, origins, dests)
        if not found:
            found = _pattern_candidates(conn, area, edges, origins, dests)
        candidates.extend(found)
    candidates.sort(key=lambda c: (-c["confidence"], len(c["sequence"])))
    candidates = candidates[:MAX_CANDIDATES]

    if len(from_m) == 1 and len(to_m) == 1:
        try:
            result["schedule_support"] = _schedule_support(conn, from_m[0], to_m[0])
        except sqlite3.Error:
            result["schedule_support"] = None
    result["candidates"] = candidates
    if not candidates:
        result["notes"].append(
            "No berth path found in observed TD movements or stored route patterns; nothing was inferred."
        )
    all_items = [i for c in candidates for i in c["sequence"]]
    labels = berth_labels(conn, all_items)
    for c in candidates:
        for item in c["sequence"]:
            item.update({k: v for k, v in (labels.get((item["td_area"], item["berth"])) or {}).items() if v})
        c["id"] = f"{c['method']}:{c['td_area']}:{c['sequence'][0]['berth']}>{c['sequence'][-1]['berth']}:{len(c['sequence'])}"
    return result


def card_live_state(conn: sqlite3.Connection, card: dict[str, Any], max_age_s: int = 1800,
                    since_revision: Optional[str] = None) -> dict[str, Any]:
    """Occupancy along the card's saved sequence, from persisted TD state.

    If ``since_revision`` equals the current TD revision, a small ``unchanged``
    response is returned instead of recomputing the card.
    """
    revision = _revision(conn)
    if since_revision and revision is not None and since_revision == revision:
        return {"id": card.get("id"), "unchanged": True, "revision": revision}
    seq = card.get("sequence") or []
    display = card.get("display") or {}
    labels = berth_labels(conn, seq)
    items = []
    for idx, it in enumerate(seq):
        lab = labels.get((it["td_area"], it["berth"])) or {}
        items.append({
            "index": idx, "berth": it["berth"], "td_area": it["td_area"],
            "provenance": it.get("provenance", "observed"),
            "source": it.get("source"),
            "station": lab.get("station"), "platform": lab.get("platform"),
            "trains": [],
        })
    state: dict[str, Any] = {
        "id": card.get("id"), "name": card.get("name"), "display": display,
        "td_data_available": _exists(conn, "td_state"),
        "updated_at": None, "revision": revision, "items": items,
        "stations": [], "signals": [], "signal_live_state": False,
        "off_sequence_trains": 0, "stale_trains_hidden": 0, "notes": [],
    }
    # group consecutive items sharing a station label
    for it in items:
        name = it["station"]
        if not name:
            continue
        if state["stations"] and state["stations"][-1]["label"] == name and state["stations"][-1]["end"] == it["index"] - 1:
            state["stations"][-1]["end"] = it["index"]
        else:
            state["stations"].append({"label": name, "start": it["index"], "end": it["index"]})

    areas = sorted({i["td_area"] for i in items})
    if state["td_data_available"] and areas:
        by_key = {(i["td_area"], i["berth"]): i for i in items}
        marks = ",".join("?" * len(areas))
        now_ms = time.time() * 1000
        for area, hc, uid, ms, iso, fb, tb in conn.execute(
            f"""SELECT td_area, headcode, uid, last_time_ms, last_time_iso, from_berth, to_berth
                FROM td_state WHERE td_area IN ({marks}) ORDER BY last_time_ms DESC""",
            areas,
        ).fetchall():
            current = tb or fb
            if state["updated_at"] is None:
                state["updated_at"] = iso
            item = by_key.get((area, current))
            if item is None:
                state["off_sequence_trains"] += 1
                continue
            if max_age_s and ms and now_ms - ms > max_age_s * 1000:
                state["stale_trains_hidden"] += 1
                continue
            item["trains"].append({
                "headcode": hc, "uid": uid, "last_time_iso": iso, "last_time_ms": ms,
                "age_s": int((now_ms - ms) / 1000) if ms else None,
            })
    elif not state["td_data_available"]:
        state["notes"].append("No td_state table: TD occupancy is unavailable in this database.")

    if display.get("signals"):
        if _exists(conn, "physical_signal_mappings") and areas:
            marks = ",".join("?" * len(areas))
            tmarks = ",".join("?" * len(TRUSTED_SIGNAL_STATUSES))
            pos = {(i["td_area"], i["berth"]): i["index"] for i in items}
            seen_signals: set[tuple[str, int]] = set()
            for area, fb, tb, num, loc, status in conn.execute(
                f"""SELECT td_area, from_berth, to_berth, physical_signal_number,
                           physical_signal_location, verification_status
                    FROM physical_signal_mappings
                    WHERE td_area IN ({marks}) AND verification_status IN ({tmarks})
                      AND physical_signal_number IS NOT NULL""",
                [*areas, *TRUSTED_SIGNAL_STATUSES],
            ).fetchall():
                at = pos.get((area, fb), pos.get((area, tb)))
                if at is not None and (num, at) not in seen_signals:
                    seen_signals.add((num, at))
                    state["signals"].append({
                        "signal": num, "location": loc, "td_area": area, "after_index": at,
                        "verification_status": status,
                    })
        state["notes"].append(
            "Signals are shown only from reviewed physical-signal mappings; live signal aspects are not displayed."
        )
    return state
