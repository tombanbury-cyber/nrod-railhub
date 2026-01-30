#!/usr/bin/env python3
"""
Connect to Network Rail TD (Train Describer) feed and optionally persist messages to SQLite,
with an optional curses real-time dashboard UI.

Views (press TAB or 1-5):
  1) Dashboard: header + DB status + wrapped message log + counters
  2) Live CA/CC: latest movement/interpose rows from td_events
  3) Live SF: latest SF rows (address/data)
  4) Live Recent: latest events (all types)
  5) Signal Confidence (mapper)

Filter:
  /   enter filter (applies to live table views)
  ESC cancel filter edit
  ENTER apply filter
  BACKSPACE edit
  x   clear filter

Keys:
  q   quit
  p   pause/resume UI updates
  c   clear message log (dashboard)
  r   reset counters (dashboard)

Note:
- Raw JSON storage is optional (--store-raw-json). Default OFF.
"""

import argparse
import curses
import json
import queue
import sqlite3
import stomp
import threading
import time
import textwrap  # (bugfix) needed for wrapped log rendering
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import math
from bisect import bisect_left, bisect_right

# ---------------------------------------------------------------------------
# Shared helpers (centralised within this file)
#
# ensure_mapper_schema(conn) creates the mapper tables/indexes once.
# process_batch_for_mapper(ev_list, pre_ms, post_ms, tau_ms) implements the
# time-window joining and scoring and returns rows to insert.
# ---------------------------------------------------------------------------

ISO = "%Y-%m-%dT%H:%M:%S.%fZ"

STEP_TYPES = {"CA", "CB", "CC"}
SIG_TYPES = {"SF"}


def iso_to_ms(ts: str) -> int:
    """Convert an ISO-8601 timestamp (Z or ±HH:MM offset) into epoch milliseconds."""
    if not ts:
        return 0
    # Python can't parse trailing Z directly, so normalise it
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)  # handles fractional seconds + offsets
        return int(dt.timestamp() * 1000)
    except Exception:
        # fallback: try int() if it's a numeric string
        try:
            return int(float(ts))
        except Exception:
            return 0


def exp_weight(dt_ms: int, tau_ms: int = 2500) -> float:
    """Exponential weighting function used for scoring."""
    return math.exp(-abs(dt_ms) / float(tau_ms))


def ensure_mapper_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure mapper tables and indexes exist (berth_signal_observations and berth_signal_scores).
    Uses unix-ms integer timestamp columns: step_timestamp and signal_timestamp.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS berth_signal_observations (
          id INTEGER PRIMARY KEY,
          td_area TEXT NOT NULL,
          step_event_id INTEGER,
          step_timestamp INTEGER,
          from_berth TEXT,
          to_berth TEXT,
          descr TEXT,
          signal_event_id INTEGER,
          signal_timestamp INTEGER,
          address TEXT NOT NULL,
          data TEXT,
          dt_ms INTEGER NOT NULL,
          weight REAL NOT NULL,
          created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
        );

        CREATE INDEX IF NOT EXISTS idx_bso_edge
        ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);
        
        CREATE INDEX IF NOT EXISTS idx_bso_edge2
        ON berth_signal_observations(td_area, from_berth, to_berth, step_timestamp);

        CREATE INDEX IF NOT EXISTS idx_bso_addr
        ON berth_signal_observations(td_area, address, signal_timestamp);
        
        CREATE INDEX IF NOT EXISTS idx_bso_addr2
        ON berth_signal_observations(td_area, address, signal_timestamp);

        -- Ensure observations are unique per (td_area, step_timestamp, signal_timestamp, address)
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bso_unique
        ON berth_signal_observations(td_area, step_timestamp, signal_timestamp, address);

        CREATE TABLE IF NOT EXISTS berth_signal_scores (
          td_area TEXT NOT NULL,
          from_berth TEXT NOT NULL,
          to_berth TEXT NOT NULL,
          address TEXT NOT NULL,
          score REAL NOT NULL,
          obs_count INTEGER NOT NULL DEFAULT 1,
          last_seen_ts INTEGER,
          last_seen_utc TEXT NOT NULL,
          last_data TEXT,
          PRIMARY KEY (td_area, from_berth, to_berth, address)
        );

        CREATE INDEX IF NOT EXISTS idx_bss_edge
        ON berth_signal_scores(td_area, from_berth, to_berth, score DESC);
        """
    )
    conn.commit()


def process_batch_for_mapper(
    evs: List[Dict[str, Any]],
    *,
    pre_ms: int = 1000,
    post_ms: int = 5000,
    tau_ms: int = 2500,
) -> Tuple[List[Tuple], List[Tuple]]:
    """
    Given a list of event dicts (each must contain keys:
      msg_ts (int), received_at_utc (str), msg_type (str), td_area (str), descr, from_berth, to_berth, address, data)
    Return (obs_rows, score_rows) where:
      obs_rows: rows to insert into berth_signal_observations with columns:
        (td_area, step_event_id, step_timestamp, from_berth, to_berth, descr,
         signal_event_id, signal_timestamp, address, data, dt_ms, weight)
      score_rows: rows to insert into berth_signal_scores with columns:
        (td_area, from_berth, to_berth, address, score, last_seen_ts, last_seen_utc, last_data)
    """
    # signals: SF with positive msg_ts
    signals = [e for e in evs if e.get("msg_type") in SIG_TYPES and e.get("address") and int(e.get("msg_ts", 0)) > 0]
    signals.sort(key=lambda e: int(e["msg_ts"]))
    sig_times = [int(e["msg_ts"]) for e in signals]

    steps = [e for e in evs if e.get("msg_type") in STEP_TYPES and e.get("from_berth") and e.get("to_berth") and int(e.get("msg_ts", 0)) > 0]
    steps.sort(key=lambda e: int(e["msg_ts"]))

    obs_rows: List[Tuple] = []
    score_rows: List[Tuple] = []

    for st in steps:
        st_ts = int(st["msg_ts"])
        lo = bisect_left(sig_times, st_ts - pre_ms)
        hi = bisect_right(sig_times, st_ts + post_ms)
        for s in signals[lo:hi]:
            s_ts = int(s["msg_ts"])
            dt = s_ts - st_ts
            w = exp_weight(dt, tau_ms)

            obs_rows.append((
                st.get("td_area"),
                None,                  # step_event_id (unknown in batch-local processing)
                st_ts,                 # step_timestamp (unix ms)
                st.get("from_berth"),
                st.get("to_berth"),
                st.get("descr"),
                None,                  # signal_event_id
                s_ts,                  # signal_timestamp (unix ms)
                str(s.get("address")),
                s.get("data"),
                abs(int(dt)),
                float(w),
            ))

            score_rows.append((
                st.get("td_area"),
                st.get("from_berth"),
                st.get("to_berth"),
                str(s.get("address")),
                float(w),
                int(s_ts),                    # last_seen_ts (unix ms)
                s.get("received_at_utc"),     # last_seen_utc (ISO)
                s.get("data"),
            ))

    return obs_rows, score_rows


# ---------------------------------------------------------------------------
# Inline BerthSignalMapper and Candidate definitions
# ---------------------------------------------------------------------------

def hex_to_bits(hex_str: str) -> str:
    try:
        b = int(hex_str, 16)
        return format(b, "08b")  # 8 bits
    except Exception:
        return ""


@dataclass
class Candidate:
    """
    Represents a candidate signal address for a given berth step.
    """
    address: str
    score: float
    obs_count: int
    conf: float
    last_seen_utc: str
    last_data: Optional[str]

    @property
    def age_s(self) -> Optional[float]:
        """Age in seconds since this candidate was last observed (None on error)."""
        try:
            t = datetime.strptime(self.last_seen_utc, ISO).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - t).total_seconds()
        except Exception:
            return None


# Helper to fetch mapper edges from a SQLite connection
def fetch_mapper_edges(
    conn: sqlite3.Connection,
    *,
    td_area: Optional[str] = None,
    top_k: int = 6,
    max_edges: int = 100,
) -> List[Tuple[str, str, List[Candidate]]]:
    """
    Read the top berth edges and candidate signal addresses from the database.

    If td_area is provided we restrict to that area; if td_area is None we
    include all areas (useful when the UI is not restricted to a single area).

    The returned list contains up to `max_edges` entries sorted by the best
    candidate score.  Each entry is a tuple of (from_berth, to_berth, [Candidate…]).
    """
    # Ensure rows are sqlite3.Row so we can index by column name
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if td_area:
        q = """
            SELECT td_area, from_berth, to_berth, address, score, obs_count, last_seen_utc, last_data
            FROM berth_signal_scores
            WHERE td_area = ?
            ORDER BY from_berth, to_berth, score DESC
        """
        params = (td_area,)
    else:
        # include td_area in results when querying across all areas
        q = """
            SELECT td_area, from_berth, to_berth, address, score, obs_count, last_seen_utc, last_data
            FROM berth_signal_scores
            ORDER BY td_area, from_berth, to_berth, score DESC
        """
        params = ()

    rows = [dict(r) for r in cur.execute(q, params).fetchall()]

    edge_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        # r is a dict-like row now
        key = (str(r.get("from_berth") or ""), str(r.get("to_berth") or ""))
        edge_map[key].append(r)

    edge_items: List[Tuple[str, str, float, List[Dict[str, Any]]]] = []
    for (fb, tb), row_list in edge_map.items():
        if not row_list:
            continue
        # row_list should already be ordered by score DESC due to the SQL ORDER BY,
        # but sort again defensively just by score descending
        row_list.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        best_score = float(row_list[0].get("score") or 0.0)
        edge_items.append((fb, tb, best_score, row_list))

    edge_items.sort(key=lambda x: x[2], reverse=True)
    edge_items = edge_items[: int(max_edges)]

    result: List[Tuple[str, str, List[Candidate]]] = []
    for fb, tb, _, row_list in edge_items:
        top_rows = row_list[: int(top_k)]
        total = sum(float(r.get("score") or 0.0) for r in top_rows) or 1.0
        cand_list: List[Candidate] = []
        for r in top_rows:
            score = float(r.get("score") or 0.0)
            conf = score / total
            cand_list.append(
                Candidate(
                    address=str(r.get("address")),
                    score=score,
                    obs_count=int(r.get("obs_count") or 1),
                    conf=conf,
                    last_seen_utc=str(r.get("last_seen_utc") or ""),
                    last_data=r.get("last_data"),
                )
            )
        result.append((fb, tb, cand_list))

    return result


# ----------------------------
# Small helpers
# ----------------------------

def _conf_color(conf: float) -> int:
    if conf >= 0.90:
        return 21
    if conf >= 0.70:
        return 22
    return 23


def _fmt_age(age_s: Optional[float]) -> str:
    if age_s is None:
        return "-"
    if age_s < 60:
        return f"{age_s:4.1f}s"
    if age_s < 3600:
        return f"{age_s/60:4.1f}m"
    return f"{age_s/3600:4.1f}h"


def _bar(conf: float, width: int) -> str:
    filled = int(round(conf * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ----------------------------
# Configuration defaults
# ----------------------------
HOST = "publicdatafeeds.networkrail.co.uk"
PORT = 61618
TOPIC_DEFAULT = "TD_ALL_SIG_AREA"
AREA_FILTER_DEFAULT = ["EK"]

DEFAULT_TABLE_EVENTS = "td_events"

LIVE_TABLE_LIMIT = 40

# ----------------------------
# SQLite helper (threaded writer)
# ----------------------------
class SQLiteWriterThreaded:
    """
    Threaded SQLite writer. UI thread never touches the writable connection.

    Supports:
      - Optional raw JSON storage (per-event msg_json)
      - Single table td_events (no td_batches)
    """
    def __init__(self, db_path: str, table_events: str, store_raw_json: bool = False):
        self.db_path = db_path
        self.table_events = table_events
        self.store_raw_json = store_raw_json

        self.q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sqlite-writer", daemon=True)
        self._started = False

        self.saved_writes = 0
        self.saved_events = 0
        self.last_saved_at_utc: Optional[str] = None
        self.last_error: Optional[str] = None

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        self._started = True

    def close(self) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.q.put_nowait(("__STOP__",))
        except queue.Full:
            pass
        if self._started:
            self._thread.join(timeout=5)

    def queue_depth(self) -> int:
        try:
            return self.q.qsize()
        except NotImplementedError:
            return 0

    def enqueue_batch_and_events(
        self,
        received_at_utc: str,
        topic: str,
        area_filter: Optional[List[str]],
        data: List[Dict[str, Any]],
        extracted_events: List[Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Dict[str, Any]]],
    ) -> None:
        self.q.put((received_at_utc, topic, area_filter, data, extracted_events), timeout=5)

    def _run(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")

        self._init_schema(conn)

        try:
            while not self._stop.is_set():
                item = self.q.get()
                if not item:
                    continue
                if item[0] == "__STOP__":
                    break

                received_at_utc, topic, area_filter, data, extracted_events = item
                try:
                    ev_count = self._insert_batch_and_events(
                        conn, received_at_utc, topic, area_filter, data, extracted_events
                    )
                    self.saved_writes += 1
                    self.saved_events += ev_count
                    self.last_saved_at_utc = received_at_utc
                    self.last_error = None
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        """
        Create td_events table (single table) and ensure mapper tables exist.
        """
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_events} (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_timestamp   INTEGER NOT NULL,
                received_at_utc TEXT NOT NULL,

                msg_wrapper     TEXT,
                msg_type        TEXT,
                td_area         TEXT,
                descr           TEXT,

                from_berth      TEXT,
                to_berth        TEXT,
                address         TEXT,
                data            TEXT,

                msg_json        TEXT
            );
            """
        )

        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_events}_area ON {self.table_events}(td_area);"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_events}_type ON {self.table_events}(msg_type);"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_events}_received ON {self.table_events}(received_at_utc);"
        )
        # Add index on timestamp to accelerate time-window lookups for the mapper.
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_events}_msg_ts ON {self.table_events}(msg_timestamp);"
        )

        conn.commit()
        # Ensure the mapper tables/indexes exist too (centralised DDL)
        try:
            ensure_mapper_schema(conn)
        except Exception:
            # keep writer available even if mapper schema creation fails
            pass

    def _insert_batch_and_events(
        self,
        conn: sqlite3.Connection,
        received_at_utc: str,
        topic: str,
        area_filter: Optional[List[str]],
        data: List[Dict[str, Any]],
        extracted_events: List[Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Dict[str, Any]]],
    ) -> int:
        """
        Insert events directly into td_events and run batch-local mapper processing.
        Returns number of events inserted.
        """
        raw_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False) if self.store_raw_json else None
        cur = conn.cursor()

        # Build per-event rows (no batches table)
        event_rows = []
        for msg_wrapper, msg_type, area_id, descr, msg_dict in extracted_events:
            ts_raw = (msg_dict.get("time") if msg_dict else None)
            try:
                ts_int = int(ts_raw) if ts_raw is not None else 0
            except Exception:
                ts_int = 0

            msg_json = json.dumps(msg_dict, separators=(",", ":"), ensure_ascii=False) if (self.store_raw_json and msg_dict) else None

            event_rows.append(
                (
                    ts_int,                 # msg_timestamp (epoch ms)
                    received_at_utc,        # received_at_utc (ISO)
                    msg_wrapper,
                    msg_type,
                    area_id,                # td_area
                    descr,
                    (msg_dict.get("from") if msg_dict else None),
                    (msg_dict.get("to") if msg_dict else None),
                    (msg_dict.get("address") if msg_dict else None),
                    (msg_dict.get("data") if msg_dict else None),
                    msg_json,
                )
            )

        if event_rows:
            cur.executemany(
                f"""
                INSERT INTO {self.table_events}
                (msg_timestamp, received_at_utc, msg_wrapper, msg_type, td_area, descr,
                 from_berth, to_berth, address, data, msg_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_rows,
            )

            # -----------------------------
            # Mapper logic (batch-local + look-back across db)
            # -----------------------------
            if getattr(self, "enable_berth_signal_mapper", True):
                try:
                    # Group batch events by TD area
                    by_area = defaultdict(list)
                    for r in event_rows:
                        msg_ts, recv_utc, _wrap, mtype, td_area, descr, f, t, addr, dat, _mj = r
                        if not td_area:
                            continue
                        ts = int(msg_ts or 0)
                        by_area[td_area].append({
                            "msg_ts": ts,
                            "received_at_utc": recv_utc,
                            "msg_type": mtype,
                            "td_area": td_area,
                            "descr": descr,
                            "from_berth": f,
                            "to_berth": t,
                            "address": addr,
                            "data": dat,
                        })

                    # For each area, query recent events in the time window around this batch
                    for td_area, evs in by_area.items():
                        if not evs:
                            continue

                        pre_ms = getattr(self, "mapper_pre_ms", 1000)
                        post_ms = getattr(self, "mapper_post_ms", 5000)
                        tau_ms = getattr(self, "mapper_tau_ms", 2500)

                        # Compute time window for look-back/look-ahead
                        ts_vals = [int(e.get("msg_ts", 0) or 0) for e in evs]
                        if not ts_vals:
                            continue
                        window_lo = min(ts_vals) - int(pre_ms)
                        window_hi = max(ts_vals) + int(post_ms)

                        # Fetch candidate events from DB in the window (steps + SF)
                        db_rows = []
                        try:
                            qcur = conn.cursor()
                            # Restrict to relevant msg_types for efficiency
                            qcur.execute(
                                f"""
                                SELECT
                                    msg_timestamp AS msg_ts,
                                    received_at_utc,
                                    msg_type,
                                    td_area,
                                    descr,
                                    from_berth,
                                    to_berth,
                                    address,
                                    data
                                FROM {self.table_events}
                                WHERE td_area=?
                                  AND msg_timestamp BETWEEN ? AND ?
                                  AND msg_type IN ('CA','CB','CC','SF')
                                ORDER BY msg_timestamp ASC
                                """,
                                (td_area, int(window_lo), int(window_hi)),
                            )
                            db_rows = [dict(r) for r in qcur.fetchall()]
                        except Exception:
                            db_rows = []

                        # Merge DB rows and batch events, deduplicating by a deterministic key
                        merged: List[Dict[str, Any]] = []
                        seen = set()

                        def _key_for_event(ev: Dict[str, Any]) -> Tuple:
                            return (
                                (ev.get("msg_type") or ""),
                                int(ev.get("msg_ts") or 0),
                                str(ev.get("address") or ""),
                                str(ev.get("from_berth") or ""),
                                str(ev.get("to_berth") or ""),
                                str(ev.get("descr") or ""),
                                str(ev.get("data") or ""),
                            )

                        for db_r in db_rows:
                            try:
                                db_ev = {
                                    "msg_ts": int(db_r.get("msg_ts") or 0),
                                    "received_at_utc": db_r.get("received_at_utc"),
                                    "msg_type": db_r.get("msg_type"),
                                    "td_area": db_r.get("td_area"),
                                    "descr": db_r.get("descr"),
                                    "from_berth": db_r.get("from_berth"),
                                    "to_berth": db_r.get("to_berth"),
                                    "address": db_r.get("address"),
                                    "data": db_r.get("data"),
                                }
                            except Exception:
                                continue
                            k = _key_for_event(db_ev)
                            if k in seen:
                                continue
                            seen.add(k)
                            merged.append(db_ev)

                        for ev in evs:
                            k = _key_for_event(ev)
                            if k in seen:
                                continue
                            seen.add(k)
                            merged.append(ev)

                        if not merged:
                            continue

                        # Use centralized process_batch_for_mapper to obtain rows from merged events
                        obs_rows, score_rows = process_batch_for_mapper(
                            merged,
                            pre_ms=pre_ms,
                            post_ms=post_ms,
                            tau_ms=tau_ms,
                        )

                        if obs_rows:
                            try:
                                # Rely on the unique index to avoid duplicate observations; use INSERT OR IGNORE
                                cur.executemany(
                                    """
                                    INSERT OR IGNORE INTO berth_signal_observations (
                                      td_area, step_event_id, step_timestamp, from_berth, to_berth, descr,
                                      signal_event_id, signal_timestamp, address, data, dt_ms, weight
                                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                                    """,
                                    obs_rows
                                )
                            except sqlite3.OperationalError:
                                # table missing or other operational error; skip mapper writes
                                pass

                        if score_rows:
                            try:
                                cur.executemany(
                                    """
                                    INSERT INTO berth_signal_scores (
                                      td_area, from_berth, to_berth, address, score, last_seen_ts, last_seen_utc, last_data
                                    )
                                    VALUES (?,?,?,?,?,?,?,?)
                                    ON CONFLICT(td_area, from_berth, to_berth, address)
                                    DO UPDATE SET
                                      score = score + excluded.score,
                                      obs_count = obs_count + 1,
                                      last_seen_ts = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_seen_ts ELSE last_seen_ts END,
                                      last_seen_utc = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_seen_utc ELSE last_seen_utc END,
                                      last_data = CASE WHEN excluded.last_seen_ts > last_seen_ts THEN excluded.last_data ELSE last_data END
                                    """,
                                    score_rows
                                )
                            except sqlite3.OperationalError:
                                # table missing or other operational error; skip mapper writes
                                pass

                except Exception as e:
                    # CRITICAL: never break ingestion
                    print(f"[mapper] disabled for this batch due to error: {e!r}")

        conn.commit()
        return len(event_rows)


# ----------------------------
# TD Listener -> pushes summaries into a UI queue
# ----------------------------
@dataclass
class UIEvent:
    kind: str
    payload: dict


class TDListener(stomp.ConnectionListener):
    WRAPPER_KEYS = ["CA_MSG", "CB_MSG", "CC_MSG", "SF_MSG", "SG_MSG", "SH_MSG"]

    def __init__(
        self,
        *,
        topic: str,
        area_filter: Optional[List[str]] = None,
        writer: Optional[SQLiteWriterThreaded] = None,
        ui_queue: Optional["queue.Queue[UIEvent]"] = None,
        print_plain: bool = False,
    ):
        self.connected = False
        self.area_filter = set(area_filter) if area_filter else None
        self.topic = topic
        self.writer = writer
        self.ui_queue = ui_queue
        self.print_plain = print_plain
        self.last_error: Optional[str] = None

    def _push(self, kind: str, payload: dict) -> None:
        if self.ui_queue is None:
            return
        try:
            self.ui_queue.put_nowait(UIEvent(kind=kind, payload=payload))
        except queue.Full:
            pass

    def on_error(self, frame):
        msg = f"ERROR FRAME: {frame.headers} cmd={getattr(frame, 'cmd', '')}"
        self.last_error = msg
        if self.print_plain:
            print(msg)
        self._push("error", {"msg": msg})

    def on_connected(self, frame):
        self.connected = True
        info = {
            "session": frame.headers.get("session", "unknown"),
            "server": frame.headers.get("server", "unknown"),
            "version": frame.headers.get("version", "unknown"),
        }
        if self.print_plain:
            print(f"CONNECTED: {info}")
        self._push("connected", info)

    def on_disconnected(self):
        self.connected = False
        if self.print_plain:
            print("DISCONNECTED")
        self._push("disconnected", {})

    def on_heartbeat_timeout(self):
        if self.print_plain:
            print("Heartbeat timeout")
        self._push("heartbeat_timeout", {})

    def _extract_events(
        self, data: List[Dict[str, Any]]
    ) -> List[Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Dict[str, Any]]]:
        events = []
        for item in data:
            if not isinstance(item, dict):
                continue
            msg = None
            msg_wrapper = None
            for key in self.WRAPPER_KEYS:
                if key in item and isinstance(item[key], dict):
                    msg = item[key]
                    msg_wrapper = key
                    break
            if not msg:
                continue
            events.append((msg_wrapper, msg.get("msg_type"), msg.get("area_id"), msg.get("descr"), msg))
        return events

    def on_message(self, frame):
        body = frame.body
        try:
            data = json.loads(body)
            if not isinstance(data, list):
                return

            # Filter by area_id if configured
            if self.area_filter:
                filtered_data = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    msg = None
                    for key in self.WRAPPER_KEYS:
                        if key in item and isinstance(item[key], dict):
                            msg = item[key]
                            break
                    if msg and msg.get("area_id") in self.area_filter:
                        filtered_data.append(item)
                if not filtered_data:
                    return
                data = filtered_data

            received_at_utc = datetime.now(timezone.utc).isoformat()
            extracted_events = self._extract_events(data)

            msg_types = Counter()
            areas = Counter()
            for (_, msg_type, area_id, _, _) in extracted_events:
                msg_types[msg_type or "unknown"] += 1
                areas[area_id or "unknown"] += 1

            if self.writer:
                self.writer.enqueue_batch_and_events(
                    received_at_utc=received_at_utc,
                    topic=self.topic,
                    area_filter=sorted(self.area_filter) if self.area_filter else None,
                    data=data,
                    extracted_events=extracted_events,
                )

            # Build log lines (wrapping happens in UI)
            lines = []
            for (_, msg_type, area_id, descr, msg) in extracted_events[:250]:
                ts = msg.get("time") if isinstance(msg, dict) else None
                ts_short = ""
                try:
                    if ts:
                        dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                        ts_short = dt.strftime("%H:%M:%S")
                except Exception:
                    ts_short = ""

                f = msg.get("from") if isinstance(msg, dict) else None
                t = msg.get("to") if isinstance(msg, dict) else None
                addr = msg.get("address") if isinstance(msg, dict) else None
                dat = msg.get("data") if isinstance(msg, dict) else None

                parts = [
                    ts_short or datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    (area_id or "-"),
                    (msg_type or "-"),
                    (descr or ""),
                ]
                if f or t:
                    parts.append(f"{f or ''}->{t or ''}")
                elif addr or dat:
                    parts.append(f"{addr or ''}:{dat or ''}")

                lines.append(" ".join([p for p in parts if p != ""]))

            self._push(
                "batch",
                {
                    "received_at_utc": received_at_utc,
                    "event_count": len(extracted_events),
                    "msg_types": dict(msg_types),
                    "areas": dict(areas),
                    "lines": lines[:140],
                },
            )

            if self.print_plain:
                print(f"[{received_at_utc}] events={len(extracted_events)}")

        except json.JSONDecodeError:
            self._push("error", {"msg": "Non-JSON message received"})
        except Exception as e:
            self._push("error", {"msg": f"{type(e).__name__}: {e}"})


# ----------------------------
# Dashboard state + rendering
# ----------------------------
VIEW_DASHBOARD = 1
VIEW_CA_CC = 2
VIEW_SF = 3
VIEW_RECENT = 4
VIEW_SIGNALS = 5


@dataclass
class DashboardState:
    topic: str
    host: str
    port: int
    area_filter: Optional[list[str]]
    area_filter_desc: str
    db_path: Optional[str]
    table_events: str
    store_raw_json: bool

    connected: bool = False
    server: str = "-"
    session: str = "-"
    version: str = "-"

    last_msg_utc: Optional[str] = None
    last_event_count: int = 0
    total_batches: int = 0
    total_events: int = 0

    _rx_times: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    _rx_events: Deque[int] = field(default_factory=lambda: deque(maxlen=200))

    msg_type_counts: Counter = field(default_factory=Counter)
    area_counts: Counter = field(default_factory=Counter)

    log_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=800))

    # writer stats
    writer_queue_depth: int = 0
    writer_saved_writes: int = 0
    writer_saved_events: int = 0
    writer_last_saved_utc: Optional[str] = None
    writer_last_error: Optional[str] = None

    # db observed stats
    db_batches_rows: Optional[int] = None  # no longer used; kept optional for UI
    db_events_rows: Optional[int] = None
    db_last_received_utc: Optional[str] = None

    last_error: Optional[str] = None

    # UI mode
    view: int = VIEW_DASHBOARD
    filter_text: str = ""
    filter_editing: bool = False

    # live tables cache
    live_rows: List[Dict[str, Any]] = field(default_factory=list)
    live_last_poll: float = 0.0
    live_last_error: Optional[str] = None

    def note_batch(self, received_at_utc: str, event_count: int, msg_types: Dict[str, int], areas: Dict[str, int], lines: List[str]) -> None:
        self.total_batches += 1
        self.total_events += int(event_count)
        self.last_msg_utc = received_at_utc
        self.last_event_count = int(event_count)
        self._rx_times.append(time.time())
        self._rx_events.append(int(event_count))
        self.msg_type_counts.update(msg_types)
        self.area_counts.update(areas)
        for ln in lines:
            self.log_lines.appendleft(ln)

    def rate_batches_per_min(self) -> float:
        if len(self._rx_times) < 2:
            return 0.0
        dt = self._rx_times[-1] - self._rx_times[0]
        if dt <= 0:
            return 0.0
        return (len(self._rx_times) / dt) * 60.0

    def rate_events_per_sec(self) -> float:
        if len(self._rx_times) < 2:
            return 0.0
        dt = self._rx_times[-1] - self._rx_times[0]
        if dt <= 0:
            return 0.0
        return (sum(self._rx_events) / dt)

    def view_name(self) -> str:
        return {
            VIEW_DASHBOARD: "Dashboard",
            VIEW_CA_CC: "Live CA/CC",
            VIEW_SF: "Live SF",
            VIEW_RECENT: "Live Recent",
            VIEW_SIGNALS: "Signal Confidence",
        }.get(self.view, "Unknown")


# ----------------------------
# Curses helpers + colours
# ----------------------------
COLOR_DEFAULT = 0
CP_OK = 1
CP_WARN = 2
CP_ERR = 3
CP_TITLE = 4
CP_DIM = 5
CP_BORDER = 6
CP_TITLE_DIM = 7

CP_ROW = 20
CP_ROW_ALT = 21

CP_CA = 8
CP_CC = 9
CP_SF = 10
CP_CB = 11
CP_OTHER = 12
CP_ALERT_BG = 13

def _init_colors() -> None:
    try:
        curses.start_color()
        if hasattr(curses, "use_default_colors"):
            curses.use_default_colors()
        curses.init_pair(CP_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(CP_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(CP_ERR, curses.COLOR_RED, -1)
        curses.init_pair(CP_TITLE, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_DIM, curses.COLOR_BLUE, -1)
        curses.init_pair(CP_BORDER, curses.COLOR_WHITE, -1)
        curses.init_pair(CP_TITLE_DIM, curses.COLOR_CYAN, -1)

        curses.init_pair(CP_ROW, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_ROW_ALT, curses.COLOR_BLUE, -1)

        curses.init_pair(CP_CA, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_CC, curses.COLOR_MAGENTA, -1)
        curses.init_pair(CP_SF, curses.COLOR_GREEN, -1)
        curses.init_pair(CP_CB, curses.COLOR_YELLOW, -1)
        curses.init_pair(CP_OTHER, curses.COLOR_WHITE, -1)
        curses.init_pair(CP_ALERT_BG, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    except Exception:
        pass


def _cattr(pair_id: int, extra: int = 0) -> int:
    try:
        return curses.color_pair(pair_id) | extra
    except Exception:
        return extra


def _row_attr(row_index: int) -> int:
    return _cattr(CP_ROW_ALT) if (row_index % 2 == 1) else 0


def _msg_attr(msg_type: str) -> int:
    t = (msg_type or "").upper()
    if t == "CA":
        return _cattr(CP_CA, curses.A_BOLD)
    if t == "CC":
        return _cattr(CP_CC, curses.A_BOLD)
    if t == "SF":
        return _cattr(CP_SF, curses.A_BOLD)
    if t == "CB":
        return _cattr(CP_CB)
    return _cattr(CP_OTHER)


def _blink_attr() -> int:
    try:
        return curses.A_BLINK
    except Exception:
        return curses.A_REVERSE


def _chrome() -> int:
    return _cattr(CP_BORDER, curses.A_DIM)


def _title_soft() -> int:
    return _cattr(CP_TITLE_DIM, curses.A_BOLD)


def _safe_addstr(win, y: int, x: int, s: str, attr: int = 0) -> None:
    try:
        win.addnstr(y, x, s, max(0, win.getmaxyx()[1] - x - 1), attr)
    except curses.error:
        pass


def _draw_box_title(win, title: str, title_attr: int = 0) -> None:
    try:
        win.attron(_chrome())
        win.border()
        win.attroff(_chrome())
        _safe_addstr(win, 0, 2, f" {title} ", title_attr or _cattr(CP_TITLE, curses.A_BOLD))
    except curses.error:
        pass


def _format_counter_table(counter: Counter, max_rows: int) -> List[Tuple[str, int]]:
    return counter.most_common()[:max_rows]


def _wrap_lines(text: str, width: int) -> List[str]:
    if width <= 3:
        return [text[: max(0, width - 1)]]
    return textwrap.wrap(text, width=width, replace_whitespace=False, drop_whitespace=False) or [""]


def _detect_batches_area_col_ro(db_path: str, table_batches: str) -> str:
    # no batches table anymore; always use td_area
    return "td_area"


def _poll_db_stats(db_path: str, table_batches: str, table_events: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        batches = None
        cur.execute(f"SELECT COUNT(*) AS c FROM {table_events}")
        events = int(cur.fetchone()["c"])
        cur.execute(f"SELECT received_at_utc FROM {table_events} ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        last_received = row["received_at_utc"] if row else None
        conn.close()
        return batches, events, last_received
    except Exception:
        return None, None, None


def _fmt_hms_from_ms(ms: Any) -> str:
    try:
        ms_int = int(ms)
        if ms_int <= 0:
            return ""
        dt = datetime.fromtimestamp(ms_int / 1000, tz=timezone.utc)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return ""


# ----------------------------
# Live table queries
# ----------------------------
def _live_query(
    db_path: str,
    table_events: str,
    view: int,
    limit: int,
    filter_text: str,
) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    like = f"%{filter_text.strip()}%" if filter_text.strip() else None

    where = []
    params: List[Any] = []

    if view == VIEW_CA_CC:
        where.append("(msg_type IN ('CA','CC'))")
    elif view == VIEW_SF:
        where.append("(msg_type = 'SF')")

    if like:
        where.append(
            "("
            "IFNULL(descr,'') LIKE ? OR "
            "IFNULL(td_area,'') LIKE ? OR "
            "IFNULL(from_berth,'') LIKE ? OR "
            "IFNULL(to_berth,'') LIKE ? OR "
            "IFNULL(address,'') LIKE ? OR "
            "IFNULL(data,'') LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT
            id, msg_timestamp, received_at_utc, msg_type, td_area, descr,
            from_berth, to_berth, address, data
        FROM {table_events}
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(int(limit))

    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ----------------------------
# Rendering views
# ----------------------------

def _draw_signal_mapper(stdscr, conn, state: DashboardState, td_area: str, selected_idx: int, y0: int, body_h: int, w: int):
    win = stdscr.derwin(body_h, w, y0, 0)
    win.erase()

    title = f" Signal Mapper  td_area={td_area}  (berth edge -> top signal candidates) "
    try:
        _draw_box_title(win, title, _cattr(CP_TITLE))
    except curses.error:
        pass

    max_rows = max(0, body_h - 3)
    edges = fetch_mapper_edges(conn, td_area=td_area, top_k=6, max_edges=max_rows)

    if not edges:
        try:
            win.addnstr(2, 2, "No mapper data yet (waiting for berth steps + SF events)...", w - 4)
        except curses.error:
            pass
        win.noutrefresh()
        return edges, 0

    selected_idx = max(0, min(selected_idx, len(edges) - 1))

    edge_w = 12
    bar_w = 10
    tail_w = 18
    cand_w = max(10, w - (edge_w + bar_w + tail_w + 6))

    y = 1
    hdr = f"{'EDGE':<{edge_w}}  {'CONF':<{bar_w}}  {'CANDIDATES':<{cand_w}}  {'DATA/AGE/OBS':<{tail_w}}"
    try:
        win.attron(curses.A_DIM)
        win.addnstr(y, 2, hdr.ljust(w), w-3)
        win.attroff(curses.A_DIM)
    except curses.error:
        pass

    for i, (fb, tb, cands) in enumerate(edges[:max_rows]):
        y = 2 + i
        if y >= body_h:
            break

        if i == selected_idx:
            win.attron(curses.A_REVERSE)

        edge = f"{fb} → {tb}"
        best = cands[0] if cands else None
        conf = best.conf if best else 0.0

        try:
            win.addnstr(y, 2, edge.ljust(edge_w), edge_w)
        except curses.error:
            pass

        bar = _bar(conf, bar_w)
        try:
            win.attron(curses.color_pair(_conf_color(conf)))
            win.addnstr(y, edge_w + 2, bar, bar_w-2)
            win.attroff(curses.color_pair(_conf_color(conf)))
        except curses.error:
            pass

        cand_parts = []
        for j, c in enumerate(cands[:6]):
            pct = int(round(c.conf * 100))
            cand_parts.append(f"{c.address} {pct:>2d}%")
        cand_str = "  ".join(cand_parts)
        cand_str = cand_str[:cand_w].ljust(cand_w)

        try:
            if best:
                win.attron(curses.color_pair(_conf_color(best.conf)))
            win.addnstr(y, edge_w + 2 + bar_w + 2, cand_str, cand_w)
            if best:
                win.attroff(curses.color_pair(_conf_color(best.conf)))
        except curses.error:
            pass

        tail = ""
        if best:
            tail = f"{(best.last_data or '-'):>2}  {_fmt_age(best.age_s):>6}  n={best.obs_count}"
        tail = tail[:tail_w].ljust(tail_w)

        try:
            win.attron(curses.A_DIM)
            win.addnstr(y, edge_w + 2 + bar_w + 2 + cand_w + 2, tail, tail_w-2)
            win.attroff(curses.A_DIM)
        except curses.error:
            pass

        if i == selected_idx:
            win.attroff(curses.A_REVERSE)

    win.noutrefresh()
    return edges, selected_idx


def _render_header(stdscr, state: DashboardState, header_h: int, w: int, paused: bool) -> None:
    header = stdscr.derwin(header_h, w, 0, 0)
    _draw_box_title(header, f" Network Rail TD  •  View: {state.view_name()} ", _cattr(CP_TITLE, curses.A_BOLD))

    conn_icon = "●"
    conn_str = "CONNECTED" if state.connected else "DISCONNECTED"
    conn_attr = _cattr(CP_OK, curses.A_BOLD) if state.connected else _cattr(CP_ERR, curses.A_BOLD)
    _safe_addstr(header, 1, 2, f"{conn_icon} {conn_str}", conn_attr)

    _safe_addstr(header, 1, 18, f"{state.host}:{state.port}  topic=/topic/{state.topic}", _cattr(CP_DIM))
    _safe_addstr(header, 2, 2, f"Areas: {state.area_filter_desc}", _cattr(CP_DIM))

    rates = f"{state.rate_batches_per_min():.1f} batches/min   {state.rate_events_per_sec():.1f} events/sec"
    _safe_addstr(header, 3, 2, f"Rx: batches={state.total_batches}  events={state.total_events}  {rates}", _cattr(CP_DIM))

    lm = state.last_msg_utc or "-"
    _safe_addstr(header, 4 if header_h > 4 else 3, 2, f"Last msg (utc): {lm}"[: max(0, w - 4)], _cattr(CP_DIM))

    if paused:
        _safe_addstr(header, 1, w - 14, "⏸ PAUSED", _cattr(CP_WARN, curses.A_BOLD))


def _render_db_panel(stdscr, state: DashboardState, y0: int, db_h: int, w: int) -> None:
    dbw = stdscr.derwin(db_h, w, y0, 0)
    _draw_box_title(dbw, " DB / Persistence status ", _cattr(CP_TITLE, curses.A_BOLD))

    y = 1
    _safe_addstr(dbw, y, 2, f"STOMP session: {state.session}   Server: {state.server}   Version: {state.version}"[: max(0, w - 4)], _cattr(CP_DIM)); y += 1
    raw_icon = "✓" if state.store_raw_json else "✗"
    raw_attr = _cattr(CP_OK) if state.store_raw_json else _cattr(CP_WARN)
    _safe_addstr(dbw, y, 2, f"Raw JSON storage: {raw_icon} {'ON' if state.store_raw_json else 'OFF'}", raw_attr); y += 1

    if state.db_path:
        _safe_addstr(dbw, y, 2, f"SQLite: {state.db_path}"[: max(0, w - 4)], _cattr(CP_DIM)); y += 1
        _safe_addstr(dbw, y, 2, f"Table: {state.table_events}   (events only)"[: max(0, w - 4)], _cattr(CP_DIM)); y += 1

        queue_n = state.writer_queue_depth
        if queue_n >= 7000:
            q_attr = _cattr(CP_ALERT_BG, curses.A_BOLD) | _blink_attr()
            q_icon = "🚨"
        elif queue_n >= 2000:
            q_attr = _cattr(CP_WARN, curses.A_BOLD)
            q_icon = "⚠"
        else:
            q_attr = _cattr(CP_OK)
            q_icon = "✓"

        _safe_addstr(
            dbw, y, 2,
            f"{q_icon} Writer queue: {queue_n}   writer saved: writes={state.writer_saved_writes} events={state.writer_saved_events}"[: max(0, w - 4)],
            q_attr
        )
        y += 1

        _safe_addstr(dbw, y, 2, f"Writer last save (utc): {state.writer_last_saved_utc or '-'}"[: max(0, w - 4)], _cattr(CP_DIM)); y += 1

        batches_str = "-" if state.db_batches_rows is None else str(state.db_batches_rows)
        _safe_addstr(dbw, y, 2, f"DB rowcounts: batches={batches_str} events={state.db_events_rows}   DB last event utc: {state.db_last_received_utc or '-'}"[: max(0, w - 4)], _cattr(CP_DIM)); y += 1

        if state.writer_last_error:
            _safe_addstr(dbw, y, 2, f"Writer error: {state.writer_last_error}"[: max(0, w - 4)], _cattr(CP_ERR, curses.A_BOLD)); y += 1
    else:
        _safe_addstr(dbw, y, 2, "SQLite: (disabled)", _cattr(CP_WARN)); y += 1

    if state.last_error:
        _safe_addstr(dbw, db_h - 2, 2, f"Last error: {state.last_error}"[: max(0, w - 4)], _cattr(CP_ERR, curses.A_BOLD))


def _render_dashboard_body(stdscr, state: DashboardState, y0: int, body_h: int, w: int) -> None:
    right_w = max(28, w // 3)
    left_w = w - right_w

    logw = stdscr.derwin(body_h, left_w, y0, 0)
    _draw_box_title(logw, " Messages received (newest first) — wrapped ", _cattr(CP_TITLE))
    usable_w = max(1, left_w - 2)
    max_rows = max(1, body_h - 2)
    row = 1
    for entry in list(state.log_lines):
        wrapped = _wrap_lines(entry, usable_w)
        for wl in wrapped:
            if row > max_rows:
                break
            if row % 2:
                _safe_addstr(logw, row, 1, wl, _cattr(CP_ROW))
            else:
                _safe_addstr(logw, row, 1, wl, _cattr(CP_ROW_ALT))
            row += 1
        if row > max_rows:
            break

    right = stdscr.derwin(body_h, right_w, y0, left_w)
    half = max(5, body_h // 2)
    t1h = half
    t2h = body_h - t1h

    t1 = right.derwin(t1h, right_w, 0, 0)
    t2 = right.derwin(t2h, right_w, t1h, 0)
    _draw_box_title(t1, " Msg types ", _cattr(CP_TITLE))
    _draw_box_title(t2, " Areas ", _cattr(CP_TITLE))

    rows1 = max(1, t1h - 2)
    for i, (k, v) in enumerate(_format_counter_table(state.msg_type_counts, rows1)):
        _safe_addstr(t1, 1 + i, 1, f"{k:<6} {v:>8}", _cattr(CP_DIM))

    rows2 = max(1, t2h - 2)
    for i, (k, v) in enumerate(_format_counter_table(state.area_counts, rows2)):
        _safe_addstr(t2, 1 + i, 1, f"{k:<8} {v:>8}", _cattr(CP_DIM))


def _render_live_table(stdscr, state: DashboardState, y0: int, body_h: int, w: int) -> None:
    win = stdscr.derwin(body_h, w, y0, 0)

    filt = state.filter_text.strip()
    filt_disp = filt if filt else "(none)"
    title = f" {state.view_name()}  •  filter: {filt_disp} "
    if state.filter_editing:
        title += " [editing] "
    _draw_box_title(win, title, _cattr(CP_TITLE))

    if not state.db_path:
        _safe_addstr(win, 2, 2, "SQLite is disabled. Start with --db to enable live tables.", _cattr(CP_WARN, curses.A_BOLD))
        return

    if state.live_last_error:
        _safe_addstr(win, 1, 2, f"⚠ {state.live_last_error}"[: max(0, w - 4)], _cattr(CP_ERR, curses.A_BOLD))

    usable_w = max(1, w - 2)
    max_rows = max(1, body_h - 3)
    y = 1

    if state.view == VIEW_SF:
        header = "UTC     AREA  TYPE  ADDRESS  DATA   DESCR/FROM->TO"
    elif state.view == VIEW_CA_CC:
        header = "UTC     AREA  TYPE  HEADCODE   FROM->TO"
    else:
        header = "UTC     AREA  TYPE  HEADCODE   DETAILS"
    _safe_addstr(win, y, 1, header[: usable_w], _cattr(CP_TITLE, curses.A_BOLD))
    y += 1

    for r in state.live_rows[:max_rows]:
        utc = _fmt_hms_from_ms(r.get("msg_timestamp")) or ""
        area = (r.get("td_area") or "")[:6]
        typ = (r.get("msg_type") or "")[:3]
        descr = (r.get("descr") or "")[:12]
        frm = (r.get("from_berth") or "")
        to = (r.get("to_berth") or "")
        addr = (r.get("address") or "")
        dat = (r.get("data") or "")

        if state.view == VIEW_SF:
            details = ""
            if addr or dat:
                details = f"{addr:<7} {dat:<5}"
            tail = descr or (f"{frm}->{to}" if (frm or to) else "")
            line = f"{utc:<7} {area:<5} {typ:<4} {details:<14} {tail}"
        elif state.view == VIEW_CA_CC:
            line = f"{utc:<7} {area:<5} {typ:<4} {descr:<10} {frm}->{to}"
        else:
            if typ == "SF":
                det = f"{addr}:{dat}"
            elif frm or to:
                det = f"{frm}->{to}"
            else:
                det = ""
            line = f"{utc:<7} {area:<5} {typ:<4} {descr:<10} {det}"

        row_attr = _row_attr(y - 2)
        type_attr = _msg_attr(typ)
        attr = row_attr | type_attr
        _safe_addstr(win, y, 1, line[:usable_w], attr)

        y += 1
        if y >= body_h - 1:
            break

    if state.filter_editing:
        prompt = "Filter> " + state.filter_text
        _safe_addstr(win, body_h - 2, 2, prompt[: max(0, w - 4)], _cattr(CP_WARN, curses.A_BOLD))


def _render_footer(stdscr, h: int, w: int, state: DashboardState) -> None:
    footer = stdscr.derwin(1, w, h - 1, 0)
    if state.view == VIEW_DASHBOARD:
        help_txt = "Keys: q quit | TAB/1-5 views | p pause | c clear log | r reset | / filter (live) | x clear filter"
    else:
        help_txt = "Keys: q quit | TAB/1-5 views | p pause | / filter | x clear filter | ESC cancel | ENTER apply"
    _safe_addstr(footer, 0, 1, help_txt[: max(0, w - 2)], _cattr(CP_DIM))


# ----------------------------
# Main dashboard loop
# ----------------------------
def dashboard_loop(
    stdscr,
    *,
    state: DashboardState,
    ui_queue: "queue.Queue[UIEvent]",
    writer: Optional[SQLiteWriterThreaded],
    stop_event: threading.Event,
) -> None:
    _init_colors()
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    paused = False
    last_db_poll = 0.0
    db_poll_interval = 1.0
    last_detect_poll = 0.0

    while not stop_event.is_set():
        try:
            ch = stdscr.getch()
        except Exception:
            ch = -1

        if ch != -1:
            if state.filter_editing:
                if ch in (27,):
                    state.filter_editing = False
                elif ch in (10, 13):
                    state.filter_editing = False
                    state.live_last_poll = 0.0
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    state.filter_text = state.filter_text[:-1]
                elif 0 <= ch <= 255 and chr(ch).isprintable():
                    state.filter_text += chr(ch)
            else:
                if ch in (ord("q"), ord("Q")):
                    stop_event.set()
                    break
                if ch in (ord("p"), ord("P")):
                    paused = not paused
                if ch in (ord("\t"),):
                    state.view = VIEW_DASHBOARD if state.view == VIEW_SIGNALS else state.view + 1
                    state.live_last_poll = 0.0
                if ch in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                    state.view = int(chr(ch))
                    state.live_last_poll = 0.0

                if ch in (ord("/"),):
                    if state.view != VIEW_DASHBOARD:
                        state.filter_editing = True
                if ch in (ord("x"), ord("X")):
                    state.filter_text = ""
                    state.live_last_poll = 0.0

                if state.view == VIEW_DASHBOARD:
                    if ch in (ord("c"), ord("C")):
                        state.log_lines.clear()
                    if ch in (ord("r"), ord("R")):
                        state.msg_type_counts.clear()
                        state.area_counts.clear()
                        state.total_batches = 0
                        state.total_events = 0
                        state._rx_times.clear()
                        state._rx_events.clear()

        if not paused:
            for _ in range(400):
                try:
                    ev = ui_queue.get_nowait()
                except queue.Empty:
                    break
                if ev.kind == "connected":
                    state.connected = True
                    state.session = ev.payload.get("session", "-")
                    state.server = ev.payload.get("server", "-")
                    state.version = ev.payload.get("version", "-")
                elif ev.kind == "disconnected":
                    state.connected = False
                elif ev.kind == "error":
                    state.last_error = ev.payload.get("msg")
                elif ev.kind == "batch":
                    state.note_batch(
                        received_at_utc=ev.payload.get("received_at_utc", ""),
                        event_count=int(ev.payload.get("event_count", 0)),
                        msg_types=ev.payload.get("msg_types", {}) or {},
                        areas=ev.payload.get("areas", {}) or {},
                        lines=ev.payload.get("lines", []) or [],
                    )

        if writer is not None:
            state.writer_queue_depth = writer.queue_depth()
            state.writer_saved_writes = writer.saved_writes if hasattr(writer, "saved_writes") else getattr(writer, "saved_writes", 0)
            state.writer_saved_events = writer.saved_events
            state.writer_last_saved_utc = writer.last_saved_at_utc
            state.writer_last_error = writer.last_error

        now = time.time()
        if state.db_path and (now - last_detect_poll) >= 10.0:
            last_detect_poll = now
            state.db_batches_rows = None

        if state.db_path and (now - last_db_poll) >= db_poll_interval:
            last_db_poll = now
            b, e, last = _poll_db_stats(state.db_path, "", state.table_events)
            state.db_batches_rows = b
            state.db_events_rows = e
            state.db_last_received_utc = last

        if (not paused) and state.db_path and state.view in (VIEW_CA_CC, VIEW_SF, VIEW_RECENT, VIEW_SIGNALS):
            if (now - state.live_last_poll) >= 0.5:
                try:
                    state.live_rows = _live_query(
                        db_path=state.db_path,
                        table_events=state.table_events,
                        view=state.view,
                        limit=LIVE_TABLE_LIMIT,
                        filter_text=state.filter_text,
                    )
                    state.live_last_error = None
                except Exception as e:
                    state.live_last_error = f"{type(e).__name__}: {e}"
                state.live_last_poll = now

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        header_h = 5
        db_h = 8
        footer_h = 1
        remaining = h - header_h - db_h - footer_h
        if remaining < 6:
            header_h = max(3, min(5, h - 3))
            db_h = max(3, min(6, h - header_h - 2))
            remaining = max(0, h - header_h - db_h - footer_h)
        body_h = remaining
        body_y = header_h + db_h

        _render_header(stdscr, state, header_h, w, paused)
        _render_db_panel(stdscr, state, header_h, db_h, w)

        if state.view == VIEW_DASHBOARD:
            _render_dashboard_body(stdscr, state, body_y, body_h, w)
        elif state.view == VIEW_SIGNALS:
            conn = sqlite3.connect(f"file:{state.db_path}?mode=ro", uri=True, timeout=1)
            mapper_td_area = None
            if isinstance(state.area_filter, list) and len(state.area_filter) == 1:
                mapper_td_area = state.area_filter[0]
            elif isinstance(state.area_filter, str):
                mapper_td_area = state.area_filter

            _draw_signal_mapper(stdscr, conn, state, mapper_td_area, 0, body_y, body_h, w)
        else:
            _render_live_table(stdscr, state, body_y, body_h, w)

        _render_footer(stdscr, h, w, state)

        stdscr.noutrefresh()
        curses.doupdate()
        time.sleep(0.05)


# ----------------------------
# CLI + main
# ----------------------------
def _validate_sqlite_ident(name: str) -> str:
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise argparse.ArgumentTypeError(
            f"Invalid table name '{name}'. Use only letters/numbers/underscore and don't start with a number."
        )
    return name


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Network Rail TD feed listener with optional SQLite persistence + curses UI.")
    p.add_argument("--username", required=True, help="Network Rail username (email).")
    p.add_argument("--password", required=True, help="Network Rail password.")
    p.add_argument("--topic", default=TOPIC_DEFAULT, help=f"Topic name (default: {TOPIC_DEFAULT})")

    p.add_argument(
        "--area",
        action="append",
        dest="areas",
        help="Filter by area_id (repeatable). Example: --area EK --area AW. If omitted, uses default.",
    )
    p.add_argument("--all-areas", action="store_true", help="Disable area filtering (show/store all areas).")

    p.add_argument("--db", help="SQLite database file path. If omitted, nothing is stored.")
    p.add_argument("--table-events", type=_validate_sqlite_ident, default=DEFAULT_TABLE_EVENTS)

    p.add_argument(
        "--store-raw-json",
        action="store_true",
        help="Store raw batch JSON (per-event msg_json) in td_events.msg_json. Default: off.",
    )

    p.add_argument("--plain", action="store_true", help="Disable curses UI; print one-line summaries instead.")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    topic = args.topic
    username = args.username
    password = args.password

    if args.all_areas:
        area_filter = None
        area_desc = "ALL"
    else:
        area_filter = args.areas if args.areas else AREA_FILTER_DEFAULT
        area_desc = ",".join(area_filter)

    writer: Optional[SQLiteWriterThreaded] = None
    if args.db:
        writer = SQLiteWriterThreaded(
            args.db,
            args.table_events,
            store_raw_json=bool(args.store_raw_json),
        )
        writer.start()

    ui_q: "queue.Queue[UIEvent]" = queue.Queue(maxsize=5000)
    stop_event = threading.Event()

    conn = stomp.Connection12(
        host_and_ports=[(HOST, PORT)],
        heartbeats=(10000, 10000),
        keepalive=True,
    )

    listener = TDListener(
        topic=topic,
        area_filter=area_filter,
        writer=writer,
        ui_queue=ui_q,
        print_plain=args.plain,
    )
    conn.set_listener("td_listener", listener)

    try:
        conn.connect(
            username=username,
            passcode=password,
            wait=True,
            headers={
                "host": "/",
                "client-id": username,
            },
        )

        conn.subscribe(destination=f"/topic/{topic}", id=1, ack="auto")

        if args.plain:
            print(f"Connected. Listening /topic/{topic} areas={area_desc} (Ctrl+C to stop)")
            while True:
                time.sleep(1)

        state = DashboardState(
            topic=topic,
            host=HOST,
            port=PORT,
            area_filter=area_filter,
            area_filter_desc=area_desc,
            db_path=args.db,
            table_events=args.table_events,
            store_raw_json=bool(args.store_raw_json),
        )
        curses.wrapper(dashboard_loop, state=state, ui_queue=ui_q, writer=writer, stop_event=stop_event)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        if args.plain:
            raise
        else:
            print(f"Exception: {type(e).__name__}: {e}")
            raise
    finally:
        stop_event.set()
        try:
            if conn.is_connected():
                conn.disconnect()
        except Exception:
            pass
        if writer:
            writer.close()


if __name__ == "__main__":
    main()
