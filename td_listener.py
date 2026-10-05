#!/usr/bin/env python3
"""Standalone TD (Train Describer) event listener for nrod-railhub.

This is a distilled, TD-only companion to `nrod_railhub.py`. It connects to
Network Rail's STOMP feed, subscribes *only* to the TD topic, parses berth
stepping (CA/CB/CC) and signalling S-class (SF/SG/SH) events, and persists
them to a SQLite database. VSTP and TRUST processing are intentionally
omitted to keep this script minimal.

Storage: each parsed event is written to the generic `td_events` table (kept
for backwards compatibility) and additionally to the domain-specific tables
used elsewhere in the project - berth stepping events go to
`td_berth_events` and signalling/S-class events go to `td_signal_events`
(schema compatible with `nrod_railhub.database.RailDB`). Unrecognised
message types are skipped and logged, never crash the listener.

It reads the same YAML configuration file format used by `nrod_railhub.py`
(see `config.sample.yaml`), reusing the config loading/merging logic from
`nrod_railhub.cli` so the two tools stay in sync.

Usage:
    python3 td_listener.py --user you@example.com --password secret \\
        --db-path td_events.db

    # Or with a config file (same format as nrod_railhub.py):
    python3 td_listener.py --config config.yaml

    # Filter to one or more TD areas:
    python3 td_listener.py --config config.yaml --td-area EK --td-area WR
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import stomp

from nrod_railhub.cli import load_config_file, merge_config_with_args
from nrod_railhub.logging_config import setup_logger, get_logger
from nrod_railhub.models import NR_HOST, NR_PORT, TOPIC_TD, expand_td_signal_bytes, ms_to_iso_utc, safe_int, utc_now_iso, utc_now_ms

logger = get_logger("td_listener")

BERTH_MSG_TYPES = ("CA", "CB", "CC")
SIGNAL_MSG_TYPES = ("SF", "SG", "SH")

def _hex_to_int(address: Optional[str]) -> Optional[int]:
    """Convert a hex address string (e.g. '0A', '0x1F') to int; None if empty/invalid."""
    if not address:
        return None
    try:
        return int(str(address).strip(), 16)  # int(..., 16) accepts an optional '0x' prefix
    except ValueError:
        logger.warning(f"Could not convert address to int: {address!r}")
        return None


def _normalize_area_list(val: Any) -> Optional[List[str]]:
    """Accept None, list, or comma-separated string; return None or list[str]."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("none", "null"):
            return None
        items = [p.strip() for p in s.split(",") if p.strip() and p.strip().lower() not in ("none", "null")]
        return items or None
    if isinstance(val, (list, tuple)):
        items: List[str] = []
        for p in val:
            if p is None:
                continue
            for part in str(p).split(","):
                part = part.strip()
                if part and part.lower() not in ("none", "null"):
                    items.append(part)
        return items or None
    return val


def unwrap_td_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    TD feed often wraps messages as {"CA_MSG": {...}} / {"CC_MSG": {...}} / {"SF_MSG": {...}} etc.
    Return the inner dict if found, else None.
    """
    if not isinstance(item, dict):
        return None
    if "msg_type" in item:
        return item  # already unwrapped

    if len(item) == 1:
        k, v = next(iter(item.items()))
        if isinstance(k, str) and k.endswith("_MSG") and isinstance(v, dict):
            return v

    for k, v in item.items():
        if isinstance(k, str) and k.endswith("_MSG") and isinstance(v, dict) and "msg_type" in v:
            return v

    return None


class TdEventDB:
    """Minimal thread-safe SQLite persistence for TD-only events."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS td_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    area TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    headcode TEXT,
                    from_berth TEXT,
                    to_berth TEXT,
                    address TEXT,
                    address_int INTEGER,
                    data TEXT,
                    data_int INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_td_events_ts ON td_events(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_events_area_ts ON td_events(area, ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_events_area_headcode_ts
                    ON td_events(area, headcode, ts_ms);

                CREATE TABLE IF NOT EXISTS td_berth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT,
                    headcode TEXT,
                    msg_type TEXT NOT NULL,
                    from_berth TEXT,
                    to_berth TEXT,
                    descr TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_td_berth_ts ON td_berth_events(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_berth_area_hc_ts
                    ON td_berth_events(td_area, headcode, ts_ms);

                CREATE TABLE IF NOT EXISTS td_signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT,
                    msg_type TEXT NOT NULL,
                    address TEXT,
                    address_int INT,
                    data TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_td_signal_ts ON td_signal_events(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_signal_area_ts ON td_signal_events(td_area, ts_ms);

                CREATE TABLE IF NOT EXISTS td_signal_bytes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    area_id TEXT NOT NULL,
                    address_int INTEGER NOT NULL,
                    value_int INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    raw_message_id INTEGER REFERENCES td_signal_events(id)
                );
                CREATE INDEX IF NOT EXISTS idx_td_signal_bytes_lookup
                    ON td_signal_bytes(area_id, address, timestamp);
                """
            )

    def _insert_row(self, table: str, columns: str, placeholders: str, values: tuple) -> None:
        """Execute a single parameterized INSERT under the connection lock."""
        self._conn.execute(f"INSERT INTO {table}({columns}) VALUES ({placeholders})", values)

    def insert_berth_event(
        self,
        ts_ms: int,
        ts_iso: str,
        area: str,
        msg_type: str,
        headcode: str = "",
        from_berth: str = "",
        to_berth: str = "",
    ) -> None:
        """Atomically persist a berth stepping event (CA/CB/CC) to td_events and td_berth_events."""
        try:
            with self._lock, self._conn:
                self._insert_row(
                    "td_events",
                    "ts_ms, ts_iso, area, msg_type, headcode, from_berth, to_berth, address, data",
                    "?,?,?,?,?,?,?,?,?",
                    (ts_ms, ts_iso, area, msg_type, headcode, from_berth, to_berth, "", ""),
                )
                self._insert_row(
                    "td_berth_events",
                    "ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr",
                    "?,?,?,?,?,?,?,?",
                    (ts_ms, ts_iso, area, headcode, msg_type, from_berth, to_berth, ""),
                )
        except Exception as e:
            logger.error(f"DB: failed to insert berth event area={area} msg_type={msg_type}: {e!r}")

    def _expand_signal_bytes_rows(
        self,
        ts_iso: str,
        area: str,
        msg_type: str,
        address: str,
        data: str,
        raw_message_id: Optional[int],
    ) -> List[Tuple[str, str, int, int, str, str, str, Optional[int]]]:
        """Expand an SG/SH message into td_signal_bytes row tuples (not yet inserted)."""
        bytes_expanded = expand_td_signal_bytes(address, data)
        if not bytes_expanded and data:
            logger.warning(
                f"_expand_signal_bytes_rows: could not expand {msg_type} message "
                f"area={area} address={address!r} data={data!r}"
            )
        return [
            (ts_iso, area, address_int, value_int, address_hex, value_hex, msg_type, raw_message_id)
            for address_int, value_int, address_hex, value_hex in bytes_expanded
        ]

    def _insert_signal_bytes(
        self,
        ts_iso: str,
        area: str,
        msg_type: str,
        address: str,
        data: str,
        raw_message_id: Optional[int],
    ) -> int:
        """Expand an SG/SH message into per-byte rows and insert them into td_signal_bytes.

        Must be called while holding self._lock within an active transaction
        on self._conn (e.g. from `insert_signal_event` or `rebuild_signal_bytes`).
        """
        rows = self._expand_signal_bytes_rows(ts_iso, area, msg_type, address, data, raw_message_id)
        if rows:
            self._conn.executemany(
                "INSERT INTO td_signal_bytes("
                "timestamp, area_id, address_int, value_int, address, value, source_type, raw_message_id"
                ") VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def insert_signal_event(
        self,
        ts_ms: int,
        ts_iso: str,
        area: str,
        msg_type: str,
        address: str,
        data: str = "",
    ) -> None:
        """Atomically persist a signal/S-class event (SF/SG/SH) to td_events and td_signal_events."""
        address_int = _hex_to_int(address)
        try:
            with self._lock, self._conn:
                self._insert_row(
                    "td_events",
                    "ts_ms, ts_iso, area, msg_type, headcode, from_berth, to_berth, address, address_int, data",
                    "?,?,?,?,?,?,?,?,?,?",
                    (ts_ms, ts_iso, area, msg_type, "", "", "", address, address_int, data or ""),
                )
                cursor = self._conn.execute(
                    "INSERT INTO td_signal_events(ts_ms, ts_iso, td_area, msg_type, address, address_int, data) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ts_ms, ts_iso, area, msg_type, address, address_int, data or ""),
                )
                if msg_type in ("SG", "SH"):
                    self._insert_signal_bytes(ts_iso, area, msg_type, address, data or "", cursor.lastrowid)
        except Exception as e:
            logger.error(f"DB: failed to insert signal event area={area} msg_type={msg_type}: {e!r}")
            
    def rebuild_signal_bytes(self, td_area: Optional[str] = None) -> Dict[str, int]:
        """Backfill td_signal_bytes from existing SG/SH rows in td_signal_events."""
        scanned = 0
        inserted = 0
        batch_size = 1000
        pending: List[Tuple[str, str, int, int, str, str, str, Optional[int]]] = []
        insert_sql = (
            "INSERT INTO td_signal_bytes("
            "timestamp, area_id, address_int, value_int, address, value, source_type, raw_message_id"
            ") VALUES (?,?,?,?,?,?,?,?)"
        )
        area_filter = (td_area or "").strip().upper()

        with self._lock, self._conn:
            if area_filter:
                self._conn.execute("DELETE FROM td_signal_bytes WHERE area_id=?", (area_filter,))
            else:
                self._conn.execute("DELETE FROM td_signal_bytes")

            query = "SELECT id, ts_iso, td_area, msg_type, address, data FROM td_signal_events WHERE msg_type IN ('SG', 'SH')"
            params: tuple = ()
            if area_filter:
                query += " AND UPPER(COALESCE(td_area, '')) = ?"
                params = (area_filter,)
            query += " ORDER BY ts_ms ASC, id ASC"

            for raw_id, ts_iso, area, msg_type, address, data in self._conn.execute(query, params):
                scanned += 1
                pending.extend(
                    self._expand_signal_bytes_rows(ts_iso, area, msg_type, address, data or "", raw_id)
                )
                if len(pending) >= batch_size:
                    self._conn.executemany(insert_sql, pending)
                    inserted += len(pending)
                    pending = []

            if pending:
                self._conn.executemany(insert_sql, pending)
                inserted += len(pending)

        return {"scanned": scanned, "inserted": inserted}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class TdListener(stomp.ConnectionListener):
    """STOMP listener that parses and persists TD events only."""

    def __init__(self, db: TdEventDB, td_area: Optional[List[str]] = None, verbose: bool = False) -> None:
        self.db = db
        self.td_area = td_area
        self.verbose = verbose
        self.subscribe_callback: Optional[callable] = None
        self.msg_count_total = 0
        self.last_message_at: Optional[str] = None

    def on_connecting(self, host_and_port) -> None:
        try:
            h, p = host_and_port
        except Exception:
            h, p = "?", "?"
        logger.info(f"Connecting TCP to {h}:{p} ...")

    def on_connected(self, frame) -> None:
        headers = getattr(frame, "headers", {}) or {}
        logger.info(f"CONNECTED. session={headers.get('session', '?')} server={headers.get('server', '?')}")
        if self.subscribe_callback:
            try:
                self.subscribe_callback()
                logger.info("Re-subscribed to TD topic after reconnect")
            except Exception as e:
                logger.error(f"Failed to re-subscribe after reconnect: {type(e).__name__}: {e}")

    def on_disconnected(self) -> None:
        logger.error("Disconnected. Attempting to reconnect...")

    def on_heartbeat_timeout(self) -> None:
        logger.error("Heartbeat timeout detected. Connection may be frozen; waiting for reconnect...")

    def on_error(self, frame) -> None:
        logger.error(f"STOMP ERROR headers={getattr(frame, 'headers', {})} body={getattr(frame, 'body', '')}")

    def on_message(self, frame) -> None:
        self.last_message_at = utc_now_iso()
        self.msg_count_total += 1

        body = getattr(frame, "body", None)
        if not body:
            return

        if self.verbose:
            short = (body[:200] + "…") if len(body) > 200 else body
            logger.debug(f"RX ({len(body)} bytes): {short}")

        try:
            payload = json.loads(body)
        except Exception:
            if self.verbose:
                logger.debug("Non-JSON message ignored")
            return

        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                self._handle_td_item(item)
            except Exception as e:
                logger.warning(f"Failed to process TD item: {type(e).__name__}: {e}")

    def _handle_td_item(self, item: Dict[str, Any]) -> None:
        td_msg = unwrap_td_item(item)
        if not td_msg or "msg_type" not in td_msg:
            return

        msg_type = (td_msg.get("msg_type") or "").upper()
        if msg_type not in BERTH_MSG_TYPES and msg_type not in SIGNAL_MSG_TYPES:
            if msg_type:
                logger.debug(f"Skipping unhandled TD msg_type={msg_type}")
            return

        area = (td_msg.get("area_id") or "").strip()
        if not area:
            return
        if self.td_area and area not in self.td_area:
            return

        ts_ms = safe_int(td_msg.get("time")) or utc_now_ms()
        ts_iso = ms_to_iso_utc(ts_ms)

        if msg_type in SIGNAL_MSG_TYPES:
            address = td_msg.get("address", "")
            if not address:
                return
            self.db.insert_signal_event(
                ts_ms=ts_ms,
                ts_iso=ts_iso,
                area=area,
                msg_type=msg_type,
                address=address,
                data=td_msg.get("data", ""),
            )
            print(f"TD {msg_type} area={area} address={address} data={td_msg.get('data', '')}")
            return

        headcode = (td_msg.get("descr") or "").strip()
        if not headcode:
            return
        from_berth = (td_msg.get("from") or "").strip()
        to_berth = (td_msg.get("to") or "").strip()

        self.db.insert_berth_event(
            ts_ms=ts_ms,
            ts_iso=ts_iso,
            area=area,
            msg_type=msg_type,
            headcode=headcode,
            from_berth=from_berth,
            to_berth=to_berth,
        )
        print(f"TD {msg_type} area={area} hc={headcode} {from_berth or '?'} -> {to_berth or '?'} ({ts_iso})")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TD-only STOMP listener/parser/DB storage for nrod-railhub.")

    p.add_argument("--config", help="Path to YAML configuration file (same format as nrod_railhub.py)")

    p.add_argument("--host", default=NR_HOST, help="STOMP host (default: publicdatafeeds.networkrail.co.uk)")
    p.add_argument("--port", type=int, default=NR_PORT, help="STOMP port (default: 61618)")
    p.add_argument("--vhost", default=NR_HOST, help="STOMP vhost/host header")

    p.add_argument("--user", required=False, help="Network Rail Data Feeds username/email")
    p.add_argument("--password", required=False, help="Network Rail Data Feeds password")

    p.add_argument(
        "--td-area",
        dest="td_area",
        action="append",
        default=[],
        help="Filter to specific TD area(s) (repeatable, or comma-separated)",
    )

    p.add_argument("--db-path", default="td_events.db", help="SQLite database path (default: td_events.db)")
    p.add_argument("--verbose", action="store_true", help="Show raw message preview / debug logging")
    p.add_argument("--log-level", dest="log_level", default="error",
                    choices=["verbose", "info", "warning", "error"], help="Log level (default: error)")
    p.add_argument("--reconnect-attempts", dest="reconnect_attempts", type=int, default=-1,
                    help="Max STOMP reconnect attempts, -1 = unlimited (default)")

    args = p.parse_args(argv)

    if args.config:
        config = load_config_file(args.config)
        parser_defaults = {action.dest: action.default for action in p._actions}
        args = merge_config_with_args(args, config, parser_defaults)

    args.td_area = _normalize_area_list(args.td_area)

    if args.verbose:
        args.log_level = "verbose"

    return args


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    setup_logger(log_level=args.log_level)

    db_path = str(pathlib.Path(args.db_path).expanduser())
    db = TdEventDB(db_path)
    logger.info(f"DB: TD events will be stored in {db_path}")

    conn = stomp.Connection11(
        host_and_ports=[(args.host, args.port)],
        keepalive=True,
        heartbeats=(10000, 10000),
        reconnect_attempts_max=args.reconnect_attempts,
        vhost=args.vhost,
    )

    listener = TdListener(db, td_area=args.td_area, verbose=args.verbose)
    conn.set_listener("", listener)

    logger.info(f"Broker: {args.host}:{args.port} vhost={args.vhost}")
    try:
        conn.connect(login=args.user, passcode=args.password, wait=True, headers={"host": args.vhost})
    except Exception as e:
        logger.error(f"CONNECT FAILED: {type(e).__name__}: {e!r}")
        sys.exit(1)

    def _subscribe_topics() -> None:
        conn.subscribe(destination=TOPIC_TD, id="td", ack="auto")
        logger.info(f"Subscribed to {TOPIC_TD}")

    listener.subscribe_callback = _subscribe_topics
    _subscribe_topics()

    if args.td_area:
        logger.info(f"Filter: td_area={args.td_area}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Exiting...")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
        db.close()


if __name__ == "__main__":
    main()
