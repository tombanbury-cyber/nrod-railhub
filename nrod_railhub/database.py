#!/usr/bin/env python3
"""SQLite database persistence for nrod_railhub."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional


class RailDB:
    """SQLite persistence for TD/TRUST/VSTP with a 'current state' view plus event history."""

    def __init__(self, path: str, enable_mapper: bool = True) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._init_schema()
        if enable_mapper:
            self.ensure_mapper_schema()

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

    
    
    def ensure_mapper_schema(self) -> None:
        """Create berth signal mapper tables if they don't exist."""
        with self._conn:
            self._conn.executescript("""
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
                
                CREATE INDEX IF NOT EXISTS idx_bso_addr
                ON berth_signal_observations(td_area, address, signal_timestamp);
                
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
                
                CREATE TABLE IF NOT EXISTS mapper_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
            """)
            # Set default mapper parameters if not exists
            with self._conn:
                self._conn.execute("""
                    INSERT OR IGNORE INTO mapper_config (key, value) VALUES ('pre_ms', 1000)
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO mapper_config (key, value) VALUES ('post_ms', 5000)
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO mapper_config (key, value) VALUES ('tau_ms', 2500)
                """)
    
    def get_mapper_config(self) -> dict:
        """Get current mapper configuration parameters."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT key, value FROM mapper_config")
            return {row[0]: row[1] for row in cursor.fetchall()}
    
    def update_mapper_config(self, pre_ms: int, post_ms: int, tau_ms: int) -> None:
        """Update mapper configuration parameters."""
        with self._lock, self._conn:
            self._conn.execute("""
                UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='pre_ms'
            """, (pre_ms,))
            self._conn.execute("""
                UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='post_ms'
            """, (post_ms,))
            self._conn.execute("""
                UPDATE mapper_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='tau_ms'
            """, (tau_ms,))
    
    def rebuild_mapper_scores(self, pre_ms: int, post_ms: int, tau_ms: int, td_area: Optional[str] = None, progress_callback=None) -> dict:
        """
        Rebuild berth_signal_scores from existing observations using new parameters.
        
        Args:
            pre_ms: Pre-window in milliseconds
            post_ms: Post-window in milliseconds
            tau_ms: Tau for exponential weighting
            td_area: Optional TD area filter (None = rebuild all areas)
            progress_callback: Optional callback function(message: str) for progress updates
        
        Returns:
            Dict with statistics: {'deleted': int, 'inserted': int, 'observations_processed': int}
        """
        from .mapper import process_batch_for_mapper
        
        with self._lock:
            cursor = self._conn.cursor()
            
            # Get list of areas to process
            if td_area:
                areas = [td_area]
            else:
                cursor.execute("SELECT DISTINCT td_area FROM berth_signal_observations WHERE td_area IS NOT NULL ORDER BY td_area")
                areas = [row[0] for row in cursor.fetchall()]
            
            if progress_callback:
                progress_callback(f"Starting rebuild for {len(areas)} area(s) with pre_ms={pre_ms}, post_ms={post_ms}, tau_ms={tau_ms}")
            
            # Clear existing scores for the selected area(s)
            if td_area:
                cursor.execute("DELETE FROM berth_signal_scores WHERE td_area=?", (td_area,))
                deleted = cursor.rowcount
            else:
                cursor.execute("DELETE FROM berth_signal_scores")
                deleted = cursor.rowcount
            
            if progress_callback:
                progress_callback(f"Cleared {deleted} existing score entries")
            
            total_observations = 0
            total_inserted = 0
            
            for area in areas:
                if progress_callback:
                    progress_callback(f"Processing area: {area}")
                
                # Fetch all observations for this area
                cursor.execute("""
                    SELECT 
                        td_area, step_timestamp, from_berth, to_berth, descr,
                        signal_timestamp, address, data,
                        dt_ms, weight
                    FROM berth_signal_observations
                    WHERE td_area=?
                    ORDER BY step_timestamp
                """, (area,))
                
                obs_rows = cursor.fetchall()
                total_observations += len(obs_rows)
                
                if progress_callback:
                    progress_callback(f"  Found {len(obs_rows)} observations")
                
                # Convert to event format for reprocessing
                events = []
                for row in obs_rows:
                    td_area_val, step_ts, from_b, to_b, descr, sig_ts, addr, data, dt_ms, weight = row
                    
                    # Add step event
                    if step_ts and from_b and to_b:
                        events.append({
                            'msg_ts': step_ts,
                            'msg_type': 'CA',  # Assume CA for steps
                            'td_area': td_area_val,
                            'from_berth': from_b,
                            'to_berth': to_b,
                            'descr': descr,
                            'address': None,
                            'data': None,
                            'received_at_utc': None
                        })
                    
                    # Add signal event
                    if sig_ts and addr:
                        events.append({
                            'msg_ts': sig_ts,
                            'msg_type': 'SF',
                            'td_area': td_area_val,
                            'from_berth': None,
                            'to_berth': None,
                            'descr': None,
                            'address': addr,
                            'data': data,
                            'received_at_utc': None
                        })
                
                # Deduplicate events by key
                seen = set()
                unique_events = []
                for e in events:
                    key = (e['msg_type'], e['msg_ts'], e.get('address'), e.get('from_berth'), e.get('to_berth'))
                    if key not in seen:
                        seen.add(key)
                        unique_events.append(e)
                
                if unique_events:
                    # Reprocess with new parameters
                    _, score_rows = process_batch_for_mapper(
                        unique_events,
                        pre_ms=pre_ms,
                        post_ms=post_ms,
                        tau_ms=tau_ms
                    )
                    
                    if score_rows:
                        # Insert new scores (accumulate if duplicate)
                        cursor.executemany("""
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
                        """, score_rows)
                        
                        total_inserted += len(score_rows)
                        
                        if progress_callback:
                            progress_callback(f"  Generated {len(score_rows)} score entries")
            
            self._conn.commit()
            
            if progress_callback:
                progress_callback(f"Rebuild complete: processed {total_observations} observations, generated {total_inserted} score entries")
            
            return {
                'deleted': deleted,
                'inserted': total_inserted,
                'observations_processed': total_observations
            }


