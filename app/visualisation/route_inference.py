"""Historical route inference for visualization chain reconstruction."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


ALGORITHM_VERSION = "historical-route-v1"
MAX_PATTERN_GAP = 4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_observed_chain(rows: Iterable[sqlite3.Row | dict[str, Any]]) -> list[dict[str, Any]]:
    """Build direct berth occupancy periods from enter/exit events."""
    chain: list[dict[str, Any]] = []
    berth_states: dict[str, dict[str, Any]] = {}

    for row in rows:
        event_type = row["event_type"]
        berth_id = row["object_id"]
        ts = row["ts"]

        if event_type == "berth_enter":
            berth_states[berth_id] = {
                "berth_id": berth_id,
                "enter_time": ts,
                "exit_time": None,
                "inferred": False,
                "confidence": 1.0,
                "source": "observed",
                "reason": "Observed berth enter event",
            }
        elif event_type == "berth_exit" and berth_id in berth_states:
            berth_states[berth_id]["exit_time"] = ts
            berth_states[berth_id]["reason"] = "Observed berth enter/exit events"
            chain.append(berth_states.pop(berth_id))

    open_states = sorted(
        berth_states.values(),
        key=lambda state: _parse_ts(state["enter_time"]),
    )
    chain.extend(open_states)
    return chain


def rebuild_route_patterns(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild transition counts and route patterns from historical events."""
    started_at = _utc_now()
    grouped_rows: dict[str, dict[str, Any]] = {}

    rows = conn.execute(
        """
        SELECT
            e.train_id,
            COALESCE(t.headcode, '') AS headcode,
            e.id,
            e.ts,
            e.event_type,
            e.object_id
        FROM event e
        LEFT JOIN train t ON t.id = e.train_id
        WHERE e.train_id IS NOT NULL
          AND e.event_type IN ('berth_enter', 'berth_exit')
        ORDER BY e.train_id, e.ts, e.id
        """
    ).fetchall()

    for row in rows:
        train_id = row[0]
        group = grouped_rows.setdefault(
            train_id,
            {"headcode": row[1], "rows": []},
        )
        group["rows"].append(
            {
                "id": row[2],
                "ts": row[3],
                "event_type": row[4],
                "object_id": row[5],
            }
        )

    transition_counts: dict[tuple[str, str, str], dict[str, Any]] = {}
    route_patterns: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}

    with conn:
        conn.execute("DELETE FROM berth_transition_counts")
        conn.execute("DELETE FROM headcode_route_patterns")

        for group in grouped_rows.values():
            headcode = group["headcode"]
            if not headcode:
                continue

            chain = build_observed_chain(group["rows"])
            sequence = [item["berth_id"] for item in chain]
            if not sequence:
                continue

            for left, right in zip(chain, chain[1:]):
                key = (headcode, left["berth_id"], right["berth_id"])
                stats = transition_counts.setdefault(
                    key,
                    {
                        "transition_count": 0,
                        "first_seen_ts": right["enter_time"] or left["exit_time"] or left["enter_time"],
                        "last_seen_ts": right["enter_time"] or left["exit_time"] or left["enter_time"],
                    },
                )
                evidence_ts = right["enter_time"] or left["exit_time"] or left["enter_time"]
                stats["transition_count"] += 1
                if evidence_ts < stats["first_seen_ts"]:
                    stats["first_seen_ts"] = evidence_ts
                if evidence_ts > stats["last_seen_ts"]:
                    stats["last_seen_ts"] = evidence_ts

            pattern_key = (headcode, tuple(sequence))
            pattern = route_patterns.setdefault(
                pattern_key,
                {
                    "observations": 0,
                    "updated_at": chain[-1]["exit_time"] or chain[-1]["enter_time"],
                },
            )
            pattern["observations"] += 1
            updated_at = chain[-1]["exit_time"] or chain[-1]["enter_time"]
            if updated_at > pattern["updated_at"]:
                pattern["updated_at"] = updated_at

        for (headcode, from_berth, to_berth), stats in sorted(transition_counts.items()):
            conn.execute(
                """
                INSERT INTO berth_transition_counts (
                    headcode,
                    from_berth,
                    to_berth,
                    transition_count,
                    first_seen_ts,
                    last_seen_ts
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    headcode,
                    from_berth,
                    to_berth,
                    stats["transition_count"],
                    stats["first_seen_ts"],
                    stats["last_seen_ts"],
                ),
            )

        observations_by_headcode: dict[str, int] = defaultdict(int)
        for (headcode, _), pattern in route_patterns.items():
            observations_by_headcode[headcode] += pattern["observations"]

        for (headcode, sequence), pattern in sorted(route_patterns.items()):
            total = observations_by_headcode[headcode]
            confidence = pattern["observations"] / total if total else 0.0
            route_key = "->".join(sequence)
            conn.execute(
                """
                INSERT INTO headcode_route_patterns (
                    headcode,
                    route_key,
                    berth_sequence_json,
                    observations,
                    confidence,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    headcode,
                    route_key,
                    json.dumps(list(sequence), separators=(",", ":")),
                    pattern["observations"],
                    confidence,
                    pattern["updated_at"],
                ),
            )

        summary = {
            "algorithm_version": ALGORITHM_VERSION,
            "transition_rows": len(transition_counts),
            "pattern_rows": len(route_patterns),
            "trains_scanned": len(grouped_rows),
        }
        finished_at = _utc_now()
        conn.execute(
            """
            INSERT INTO route_inference_runs (
                algorithm_version,
                started_at,
                finished_at,
                summary_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                ALGORITHM_VERSION,
                started_at,
                finished_at,
                json.dumps(summary, separators=(",", ":")),
            ),
        )

    return summary


def _load_transition_stats(
    conn: sqlite3.Connection,
    headcode: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int]]:
    rows = conn.execute(
        """
        SELECT from_berth, to_berth, transition_count, last_seen_ts
        FROM berth_transition_counts
        WHERE headcode = ?
        ORDER BY transition_count DESC, last_seen_ts DESC
        """,
        (headcode,),
    ).fetchall()

    by_edge: dict[tuple[str, str], dict[str, Any]] = {}
    totals_by_from: dict[str, int] = defaultdict(int)
    for from_berth, to_berth, count, last_seen_ts in rows:
        by_edge[(from_berth, to_berth)] = {
            "count": count,
            "last_seen_ts": last_seen_ts,
        }
        totals_by_from[from_berth] += count
    return by_edge, totals_by_from


def _load_route_patterns(conn: sqlite3.Connection, headcode: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT berth_sequence_json, observations, confidence, updated_at
        FROM headcode_route_patterns
        WHERE headcode = ?
        ORDER BY observations DESC, updated_at DESC
        """,
        (headcode,),
    ).fetchall()
    return [
        {
            "sequence": json.loads(sequence_json),
            "observations": observations,
            "confidence": confidence,
            "updated_at": updated_at,
        }
        for sequence_json, observations, confidence, updated_at in rows
    ]


def _infer_gap_items(
    start_berth: str,
    end_berth: str,
    headcode: str,
    patterns: list[dict[str, Any]],
    transition_stats: dict[tuple[str, str], dict[str, Any]],
    totals_by_from: dict[str, int],
) -> list[dict[str, Any]]:
    direct_count = transition_stats.get((start_berth, end_berth), {}).get("count", 0)
    best_candidate: dict[str, Any] | None = None

    for pattern in patterns:
        sequence = pattern["sequence"]
        for start_idx, berth_id in enumerate(sequence):
            if berth_id != start_berth:
                continue
            for end_idx in range(start_idx + 2, min(len(sequence), start_idx + MAX_PATTERN_GAP + 2)):
                if sequence[end_idx] != end_berth:
                    continue

                intermediates = sequence[start_idx + 1:end_idx]
                edges = list(zip(sequence[start_idx:end_idx], sequence[start_idx + 1:end_idx + 1]))
                edge_counts = []
                edge_confidences = []
                last_seen_values = []
                for edge in edges:
                    stats = transition_stats.get(edge)
                    if not stats:
                        break
                    edge_counts.append(stats["count"])
                    total_from = totals_by_from.get(edge[0], 0)
                    edge_confidences.append(stats["count"] / total_from if total_from else 0.0)
                    last_seen_values.append(stats["last_seen_ts"])
                else:
                    candidate = {
                        "intermediates": intermediates,
                        "support": min(edge_counts),
                        "confidence": min(edge_confidences) if edge_confidences else pattern["confidence"],
                        "last_seen_ts": max(last_seen_values) if last_seen_values else pattern["updated_at"],
                        "observations": pattern["observations"],
                    }
                    if (
                        best_candidate is None
                        or (candidate["support"], candidate["last_seen_ts"], candidate["observations"], -len(candidate["intermediates"]))
                        > (best_candidate["support"], best_candidate["last_seen_ts"], best_candidate["observations"], -len(best_candidate["intermediates"]))
                    ):
                        best_candidate = candidate

    if not best_candidate:
        return []
    if best_candidate["support"] <= direct_count:
        return []

    return [
        {
            "berth_id": berth_id,
            "enter_time": None,
            "exit_time": None,
            "inferred": True,
            "confidence": round(best_candidate["confidence"], 3),
            "source": "historical_route_pattern",
            "reason": f"Inferred between {start_berth} and {end_berth} using historical headcode {headcode} transitions",
        }
        for berth_id in best_candidate["intermediates"]
    ]


def infer_train_chain(conn: sqlite3.Connection, train_id: str) -> dict[str, Any]:
    """Build a berth chain with historical gap inference when evidence exists."""
    event_rows = conn.execute(
        """
        SELECT id, ts, event_type, object_id
        FROM event
        WHERE train_id = ? AND event_type IN ('berth_enter', 'berth_exit')
        ORDER BY ts, id
        """,
        (train_id,),
    ).fetchall()
    rows = [
        {
            "id": row[0],
            "ts": row[1],
            "event_type": row[2],
            "object_id": row[3],
        }
        for row in event_rows
    ]
    observed_chain = build_observed_chain(rows)
    if not observed_chain:
        return {"train_id": train_id, "chain": []}

    headcode_row = conn.execute(
        "SELECT headcode FROM train WHERE id = ?",
        (train_id,),
    ).fetchone()
    headcode = headcode_row[0] if headcode_row and headcode_row[0] else None
    if not headcode:
        return {"train_id": train_id, "chain": observed_chain}

    patterns = _load_route_patterns(conn, headcode)
    transition_stats, totals_by_from = _load_transition_stats(conn, headcode)
    if not patterns or not transition_stats:
        return {"train_id": train_id, "chain": observed_chain}

    chain: list[dict[str, Any]] = []
    for index, item in enumerate(observed_chain):
        chain.append(item)
        if index == len(observed_chain) - 1:
            continue
        next_item = observed_chain[index + 1]
        chain.extend(
            _infer_gap_items(
                item["berth_id"],
                next_item["berth_id"],
                headcode,
                patterns,
                transition_stats,
                totals_by_from,
            )
        )

    return {"train_id": train_id, "chain": chain}
