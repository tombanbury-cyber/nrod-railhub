#!/usr/bin/env python3
"""SQLite database persistence for nrod_railhub."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional


class RailDB:
    """SQLite persistence for TD/TRUST/VSTP with a 'current state' view plus event history."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS td_state (
                    td_area TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    last_time_utc TEXT,
                    from_berth TEXT,
                    to_berth TEXT,
                    stanox TEXT,
                    location_name TEXT,
                    platform TEXT,
                    sched_dep TEXT,
                    sched_arr TEXT,
                    origin_name TEXT,
                    dest_name TEXT,
                    uid TEXT,
                    PRIMARY KEY (td_area, headcode)
                );
                CREATE TABLE IF NOT EXISTS td_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT NOT NULL,
                    td_area TEXT,
                    headcode TEXT,
                    event_type TEXT NOT NULL,
                    from_berth TEXT,
                    to_berth TEXT,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_td_event_ts ON td_event(ts_utc);
                CREATE INDEX IF NOT EXISTS idx_td_event_area_hc_ts ON td_event(td_area, headcode, ts_utc);

                CREATE TABLE IF NOT EXISTS trust_state (
                    train_id TEXT PRIMARY KEY,
                    headcode TEXT,
                    uid TEXT,
                    toc_id TEXT,
                    last_event_time TEXT,
                    last_location TEXT,
                    last_delay_min INTEGER,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trust_state_headcode ON trust_state(headcode);

                CREATE TABLE IF NOT EXISTS vstp_state (
                    uid TEXT,
                    headcode TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    raw_json TEXT,
                    PRIMARY KEY (uid, start_date)
                );
                CREATE INDEX IF NOT EXISTS idx_vstp_state_headcode ON vstp_state(headcode);
                """
            )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def insert_td_event(self, ts_utc: str, area: str, headcode: str, event_type: str, from_berth: str, to_berth: str, raw: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO td_event(ts_utc, td_area, headcode, event_type, from_berth, to_berth, raw_json) VALUES (?,?,?,?,?,?,?)",
                (ts_utc, area, headcode, event_type, from_berth, to_berth, json.dumps(raw, separators=(',',':'))),
            )

    def upsert_td_state(self, area: str, headcode: str, last_time_utc: str, from_berth: str, to_berth: str,
                        stanox: str | None = None, location_name: str | None = None, platform: str | None = None,
                        sched_dep: str | None = None, sched_arr: str | None = None, origin_name: str | None = None, dest_name: str | None = None, uid: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO td_state(td_area, headcode, last_time_utc, from_berth, to_berth, stanox, location_name, platform,
                                     sched_dep, sched_arr, origin_name, dest_name, uid)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(td_area, headcode) DO UPDATE SET
                    last_time_utc=excluded.last_time_utc,
                    from_berth=excluded.from_berth,
                    to_berth=excluded.to_berth,
                    stanox=COALESCE(excluded.stanox, td_state.stanox),
                    location_name=COALESCE(excluded.location_name, td_state.location_name),
                    platform=COALESCE(excluded.platform, td_state.platform),
                    sched_dep=COALESCE(excluded.sched_dep, td_state.sched_dep),
                    sched_arr=COALESCE(excluded.sched_arr, td_state.sched_arr),
                    origin_name=COALESCE(excluded.origin_name, td_state.origin_name),
                    dest_name=COALESCE(excluded.dest_name, td_state.dest_name),
                    uid=COALESCE(excluded.uid, td_state.uid)
                """,
                (area, headcode, last_time_utc, from_berth, to_berth, stanox, location_name, platform, sched_dep, sched_arr, origin_name, dest_name, uid),
            )

    def upsert_trust(self, train_id: str, headcode: str, uid: str, toc_id: str, last_event_time: str, last_location: str, last_delay_min: int | None, raw: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO trust_state(train_id, headcode, uid, toc_id, last_event_time, last_location, last_delay_min, raw_json)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(train_id) DO UPDATE SET
                    headcode=excluded.headcode,
                    uid=excluded.uid,
                    toc_id=excluded.toc_id,
                    last_event_time=excluded.last_event_time,
                    last_location=excluded.last_location,
                    last_delay_min=excluded.last_delay_min,
                    raw_json=excluded.raw_json
                """,
                (train_id, headcode, uid, toc_id, last_event_time, last_location, last_delay_min, json.dumps(raw, separators=(',',':'))),
            )

    def upsert_vstp(self, uid: str, headcode: str, start_date: str, end_date: str, raw: dict) -> None:
        if not uid or not start_date:
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO vstp_state(uid, headcode, start_date, end_date, raw_json)
                VALUES (?,?,?,?,?)
                ON CONFLICT(uid, start_date) DO UPDATE SET
                    headcode=excluded.headcode,
                    end_date=excluded.end_date,
                    raw_json=excluded.raw_json
                """,
                (uid, headcode, start_date, end_date, json.dumps(raw, separators=(',',':'))),
            )
