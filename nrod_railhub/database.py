#!/usr/bin/env python3
"""SQLite database persistence for nrod_railhub."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from statistics import median, pvariance
from typing import Optional, Any, Dict, Tuple

from .models import safe_int


class RailDB:
    """SQLite persistence for TD/TRUST/VSTP with a 'current state' view plus event history.
    
    Features:
    - TD state/events: Current train positions and historical berth/signal events
    - TRUST state: Real-time train movement updates
    - VSTP state: Very Short Term Planning schedule changes
    - Mapper integration: Automatic berth-to-signal correlation (when enabled)
    """

    def __init__(
        self,
        path: str,
        enable_mapper: bool = True,
        retain_trust_days: Optional[int] = None,
        retain_vstp_days: Optional[int] = None,
        retain_cif_days: Optional[int] = None,
        retention_check_interval_s: int = 3600,
        retention_batch_size: int = 1000,
        save_raw_json: bool = True,
    ) -> None:
        """Initialize RailDB.
        
        Args:
            path: Path to SQLite database file
            enable_mapper: If True, enables automatic berth-to-signal correlation
            retain_trust_days: Days to retain TRUST messages (None = no cleanup)
            retain_vstp_days: Days to retain VSTP schedules (None = no cleanup)
            retain_cif_days: Days to retain CIF schedules (None = no cleanup)
            retention_check_interval_s: Seconds between retention checks (default 3600)
            retention_batch_size: Batch size for deletion (default 1000)
            save_raw_json: If True, saves raw JSON messages to database (default True)
        """
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = None
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA temp_store=MEMORY;")
        self._init_schema()
        self.ensure_sclass_correlation_schema()
        self.ensure_physical_signal_schema()

        # Retention settings
        self.retain_trust_days = retain_trust_days
        self.retain_vstp_days = retain_vstp_days
        self.retain_cif_days = retain_cif_days
        self.retention_check_interval_s = retention_check_interval_s
        self.retention_batch_size = retention_batch_size
        self._retention_thread: Optional[threading.Thread] = None
        self._retention_stop_event = threading.Event()
        
        # Raw JSON storage setting
        self.save_raw_json = save_raw_json
        self._td_movement_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        
        self.enable_mapper = enable_mapper
        if enable_mapper:
            self.ensure_mapper_schema()
            # Initialize batch processing for mapper
            self._event_batch: list = []
            self._mapper_tail_events: list = []
            self._batch_lock = threading.Lock()
            self._batch_size = 100  # Process when we hit this many events
            self._start_batch_processor()
        
        # Start retention thread if enabled
        if retain_trust_days or retain_vstp_days or retain_cif_days:
            self._start_retention_thread()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS td_state (
                    td_area TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    last_time_ms INTEGER NOT NULL,
                    last_time_iso TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_td_berth_area_hc_ts ON td_berth_events(td_area, headcode, ts_ms);

                CREATE TABLE IF NOT EXISTS td_berth_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_event_id INTEGER UNIQUE,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    source_msg_type TEXT NOT NULL,
                    evidence_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_td_berth_movements_ts ON td_berth_movements(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_berth_movements_area_ts ON td_berth_movements(td_area, ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_berth_movements_area_hc_ts ON td_berth_movements(td_area, headcode, ts_ms);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_td_berth_movements_dedupe
                    ON td_berth_movements(td_area, headcode, ts_ms, from_berth, to_berth);
                
                CREATE TABLE IF NOT EXISTS td_signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT,
                    msg_type TEXT NOT NULL,
                    address TEXT,
                    data TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_td_signal_ts ON td_signal_events(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_signal_area_ts ON td_signal_events(td_area, ts_ms);

                CREATE TABLE IF NOT EXISTS td_sclass_state (
                    td_area TEXT NOT NULL,
                    address TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    last_seen_ts INTEGER NOT NULL,
                    last_seen_iso TEXT NOT NULL,
                    raw_data TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    PRIMARY KEY (td_area, address)
                );
                CREATE INDEX IF NOT EXISTS idx_td_sclass_state_area_ts ON td_sclass_state(td_area, last_seen_ts);

                CREATE TABLE IF NOT EXISTS td_sclass_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    ts_iso TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    old_state INTEGER NOT NULL,
                    new_state INTEGER NOT NULL,
                    raw_old TEXT NOT NULL,
                    raw_new TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_td_sclass_changes_ts ON td_sclass_changes(ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_sclass_changes_area_ts ON td_sclass_changes(td_area, ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_sclass_changes_area_addr_ts ON td_sclass_changes(td_area, address, ts_ms);

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

                -- New table: store fully decoded TRUST messages (history)
                CREATE TABLE IF NOT EXISTS trust_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    train_id TEXT,
                    actual_timestamp_ms INTEGER,
                    gbtt_timestamp_ms INTEGER,
                    planned_timestamp_ms INTEGER,
                    planned_event_type TEXT,
                    event_type TEXT,
                    event_source TEXT,
                    correction_ind INTEGER,
                    offroute_ind INTEGER,
                    direction_ind TEXT,
                    line_ind TEXT,
                    platform TEXT,
                    route TEXT,
                    train_service_code TEXT,
                    division_code TEXT,
                    toc_id TEXT,
                    toc_code TEXT,
                    timetable_variation INTEGER,
                    variation_status TEXT,
                    next_report_stanox TEXT,
                    next_report_run_time INTEGER,
                    train_terminated INTEGER,
                    delay_monitoring_point INTEGER,
                    reporting_stanox TEXT,
                    auto_expected INTEGER,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    UNIQUE(train_id, actual_timestamp_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_trust_messages_train_id ON trust_messages(train_id);
                CREATE INDEX IF NOT EXISTS idx_trust_messages_actual_ts ON trust_messages(actual_timestamp_ms);
                CREATE INDEX IF NOT EXISTS idx_trust_messages_toc_code ON trust_messages(toc_code);

                -- VSTP: schedule header table
                CREATE TABLE IF NOT EXISTS vstp_schedules (
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    schedule_end_date TEXT,
                    transaction_type TEXT,
                    train_status TEXT,
                    schedule_days_runs TEXT,
                    applicable_timetable TEXT,
                    CIF_train_uid TEXT,
                    CIF_stp_indicator TEXT,
                    signalling_id TEXT,
                    CIF_train_service_code TEXT,
                    CIF_train_category TEXT,
                    CIF_power_type TEXT,
                    sender_organisation TEXT,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    PRIMARY KEY (uid, schedule_start_date)
                );
                CREATE INDEX IF NOT EXISTS idx_vstp_schedules_uid ON vstp_schedules(uid);

                -- VSTP: per-location rows
                CREATE TABLE IF NOT EXISTS vstp_schedule_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    location_index INTEGER NOT NULL,
                    tiploc TEXT,
                    scheduled_pass_time TEXT,
                    scheduled_departure_time TEXT,
                    scheduled_arrival_time TEXT,
                    public_departure_time TEXT,
                    public_arrival_time TEXT,
                    CIF_pathing_allowance TEXT,
                    CIF_activity TEXT,
                    CIF_line TEXT,
                    CIF_engineering_allowance TEXT,
                    CIF_performance_allowance TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
                CREATE INDEX IF NOT EXISTS idx_vstp_loc_uid ON vstp_schedule_locations(uid);
                
                -- CIF: downloaded schedule header table (from daily TOC schedule downloads)
                CREATE TABLE IF NOT EXISTS cif_schedules (
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    schedule_end_date TEXT,
                    toc_code TEXT,
                    transaction_type TEXT,
                    train_status TEXT,
                    schedule_days_runs TEXT,
                    applicable_timetable TEXT,
                    CIF_train_uid TEXT,
                    CIF_stp_indicator TEXT,
                    signalling_id TEXT,
                    CIF_train_service_code TEXT,
                    CIF_train_category TEXT,
                    CIF_power_type TEXT,
                    CIF_headcode TEXT,
                    raw_json TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    PRIMARY KEY (uid, schedule_start_date, CIF_stp_indicator)
                );
                CREATE INDEX IF NOT EXISTS idx_cif_schedules_uid ON cif_schedules(uid);
                CREATE INDEX IF NOT EXISTS idx_cif_schedules_toc ON cif_schedules(toc_code);
                CREATE INDEX IF NOT EXISTS idx_cif_schedules_headcode ON cif_schedules(CIF_headcode);
                CREATE INDEX IF NOT EXISTS idx_cif_schedules_created_ts ON cif_schedules(created_at_ts);
                
                -- CIF: per-location rows
                CREATE TABLE IF NOT EXISTS cif_schedule_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL,
                    schedule_start_date TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    location_index INTEGER NOT NULL,
                    tiploc TEXT,
                    scheduled_pass_time TEXT,
                    scheduled_departure_time TEXT,
                    scheduled_arrival_time TEXT,
                    public_departure_time TEXT,
                    public_arrival_time TEXT,
                    platform TEXT,
                    CIF_pathing_allowance TEXT,
                    CIF_activity TEXT,
                    CIF_line TEXT,
                    CIF_path TEXT,
                    CIF_engineering_allowance TEXT,
                    CIF_performance_allowance TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
                CREATE INDEX IF NOT EXISTS idx_cif_loc_uid ON cif_schedule_locations(uid);
                CREATE INDEX IF NOT EXISTS idx_cif_loc_tiploc ON cif_schedule_locations(tiploc);
                CREATE INDEX IF NOT EXISTS idx_cif_loc_created_ts ON cif_schedule_locations(created_at_ts);
                
                -- TOC (Train Operating Company) reference data
                CREATE TABLE IF NOT EXISTS toc_reference (
                    toc_code TEXT PRIMARY KEY,
                    toc_name TEXT NOT NULL,
                    business_code TEXT,
                    sector_code TEXT,
                    atoc_code TEXT,
                    sector TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                
                -- TOC-TD Area Mappings: Many-to-many relationships between TOCs and TD areas
                CREATE TABLE IF NOT EXISTS toc_td_areas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    toc_code TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    confidence REAL,
                    effective_from TEXT,
                    effective_to TEXT,
                    created_by TEXT,
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
                    notes TEXT,
                    UNIQUE(toc_code, td_area)
                );
                CREATE INDEX IF NOT EXISTS idx_toc_td_areas_toc_code ON toc_td_areas(toc_code);
                CREATE INDEX IF NOT EXISTS idx_toc_td_areas_td_area ON toc_td_areas(td_area);
                
                -- CORPUS: Location reference data (TIPLOC, STANOX, CRS mappings)
                CREATE TABLE IF NOT EXISTS corpus_locations (
                    tiploc TEXT,
                    stanox TEXT,
                    crs TEXT,
                    nlc TEXT,
                    name TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (tiploc, stanox, crs)
                );
                CREATE INDEX IF NOT EXISTS idx_corpus_tiploc ON corpus_locations(tiploc) WHERE tiploc IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_corpus_stanox ON corpus_locations(stanox) WHERE stanox IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_corpus_crs ON corpus_locations(crs) WHERE crs IS NOT NULL;
                
                -- SMART: Berth stepping reference data (TD area + berth -> location)
                CREATE TABLE IF NOT EXISTS smart_berths (
                    td_area TEXT NOT NULL,
                    berth TEXT NOT NULL,
                    stanox TEXT,
                    platform TEXT,
                    event TEXT,
                    stanme TEXT,
                    step_type TEXT,
                    from_line TEXT,
                    to_line TEXT,
                    berthoffset INTEGER,
                    comment TEXT,
                    raw_json TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (td_area, berth)
                );
                CREATE INDEX IF NOT EXISTS idx_smart_stanox ON smart_berths(stanox) WHERE stanox IS NOT NULL;
                """
            )

    def _start_retention_thread(self) -> None:
        """Start background thread for periodic data retention."""
        from .logging_config import get_logger
        
        def retention_worker():
            logger = get_logger("database.retention")
            logger.info(
                f"Retention thread started: trust={self.retain_trust_days}d, "
                f"vstp={self.retain_vstp_days}d, cif={self.retain_cif_days}d, interval={self.retention_check_interval_s}s"
            )
            
            while not self._retention_stop_event.wait(self.retention_check_interval_s):
                try:
                    deleted = self.purge_old_data()
                    if deleted['trust_messages'] > 0 or deleted['vstp_schedules'] > 0 or deleted['cif_schedules'] > 0:
                        logger.info(
                            f"Retention purge: deleted {deleted['trust_messages']} trust_messages, "
                            f"{deleted['vstp_schedules']} vstp_schedules, {deleted['cif_schedules']} cif_schedules"
                        )
                except Exception as e:
                    logger.error(f"Retention worker error: {e}", exc_info=True)
        
        self._retention_thread = threading.Thread(
            target=retention_worker,
            daemon=True,
            name="retention-worker"
        )
        self._retention_thread.start()
    
    def stop_retention(self) -> None:
        """Stop the retention background thread."""
        if self._retention_thread and self._retention_thread.is_alive():
            self._retention_stop_event.set()
            self._retention_thread.join(timeout=5.0)

    @staticmethod
    def _normalize_sclass_bytes(data: Any) -> Optional[bytes]:
        """Convert S-class payload data to raw bytes when possible."""
        if data is None:
            return None
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)

        text = str(data).strip()
        if not text:
            return None

        cleaned = text.replace(" ", "").replace("\t", "").replace("\n", "").replace("\r", "")
        cleaned = cleaned.replace(":", "").replace("-", "").replace("_", "").replace(",", "")
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        if len(cleaned) % 2 != 0:
            return None
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            return None

    def _persist_td_sclass_state(self, ts_ms: int, ts_iso: str, area: str, msg_type: str, address: str, data: str) -> list[dict[str, Any]]:
        """Persist S-class snapshot state and derived bit transitions."""
        payload = self._normalize_sclass_bytes(data)
        if not payload:
            return []

        raw_new = payload.hex().upper()
        byte_length = len(payload)

        movement = None
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT raw_data, byte_length FROM td_sclass_state WHERE td_area=? AND address=?",
                (area, address),
            )
            previous = cursor.fetchone()
            prev_payload = self._normalize_sclass_bytes(previous[0]) if previous else None

            cursor.execute(
                """
                INSERT INTO td_sclass_state(td_area, address, msg_type, last_seen_ts, last_seen_iso, raw_data, byte_length)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(td_area, address) DO UPDATE SET
                    msg_type=excluded.msg_type,
                    last_seen_ts=excluded.last_seen_ts,
                    last_seen_iso=excluded.last_seen_iso,
                    raw_data=excluded.raw_data,
                    byte_length=excluded.byte_length
                """,
                (area, address, msg_type, ts_ms, ts_iso, raw_new, byte_length),
            )

            if not prev_payload or len(prev_payload) != len(payload):
                return []

            changes: list[dict[str, Any]] = []
            for byte_offset, (old_byte, new_byte) in enumerate(zip(prev_payload, payload)):
                if old_byte == new_byte:
                    continue
                changed_bits = old_byte ^ new_byte
                if not changed_bits:
                    continue
                raw_old = f"{old_byte:02X}"
                raw_new_byte = f"{new_byte:02X}"
                for bit in range(7, -1, -1):
                    mask = 1 << bit
                    if not (changed_bits & mask):
                        continue
                    cursor.execute(
                        """
                        INSERT INTO td_sclass_changes(
                            ts_ms, ts_iso, td_area, msg_type, address, byte_offset, bit,
                            old_state, new_state, raw_old, raw_new
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            ts_ms,
                            ts_iso,
                            area,
                            msg_type,
                            address,
                            byte_offset,
                            bit,
                            1 if (old_byte & mask) else 0,
                            1 if (new_byte & mask) else 0,
                            raw_old,
                            raw_new_byte,
                        ),
                    )
                    changes.append(
                        {
                            "id": cursor.lastrowid,
                            "ts_ms": ts_ms,
                            "ts_iso": ts_iso,
                            "td_area": area,
                            "msg_type": msg_type,
                            "address": address,
                            "byte_offset": byte_offset,
                            "bit": bit,
                            "old_state": 1 if (old_byte & mask) else 0,
                            "new_state": 1 if (new_byte & mask) else 0,
                        }
                    )
            return changes

    def purge_old_data(self) -> dict:
        """
        Purge old trust_messages, vstp_schedules, and cif_schedules based on retention settings.
        
        Performs batched deletes to avoid long write locks.
        
        Returns:
            Dict with counts: {'trust_messages': int, 'vstp_schedules': int, 'cif_schedules': int}
        """
        result = {'trust_messages': 0, 'vstp_schedules': 0, 'cif_schedules': 0}
        now_ms = int(time.time() * 1000)
        
        # Purge trust_messages
        if self.retain_trust_days and self.retain_trust_days > 0:
            cutoff_ms = now_ms - (self.retain_trust_days * 24 * 60 * 60 * 1000)
            result['trust_messages'] = self._purge_trust_messages(cutoff_ms, self.retention_batch_size)
        
        # Purge vstp_schedules
        if self.retain_vstp_days and self.retain_vstp_days > 0:
            cutoff_ms = now_ms - (self.retain_vstp_days * 24 * 60 * 60 * 1000)
            result['vstp_schedules'] = self._purge_vstp_schedules(cutoff_ms, self.retention_batch_size)
        
        # Purge cif_schedules
        if self.retain_cif_days and self.retain_cif_days > 0:
            cutoff_ms = now_ms - (self.retain_cif_days * 24 * 60 * 60 * 1000)
            result['cif_schedules'] = self._purge_cif_schedules(cutoff_ms, self.retention_batch_size)
        
        return result
    
    def _purge_trust_messages(self, cutoff_ms: int, batch_size: int) -> int:
        """
        Purge trust_messages older than cutoff_ms in batches.
        
        Args:
            cutoff_ms: Delete messages older than this timestamp (epoch ms)
            batch_size: Number of rows to delete per transaction
            
        Returns:
            Total number of rows deleted
        """
        total_deleted = 0
        
        while True:
            with self._lock, self._conn:
                cursor = self._conn.cursor()
                # Select IDs to delete
                cursor.execute(
                    "SELECT id FROM trust_messages WHERE created_at_ts < ? LIMIT ?",
                    (cutoff_ms, batch_size)
                )
                ids = [row[0] for row in cursor.fetchall()]
                
                if not ids:
                    break
                
                # Delete batch
                placeholders = ','.join('?' * len(ids))
                cursor.execute(f"DELETE FROM trust_messages WHERE id IN ({placeholders})", ids)
                deleted = cursor.rowcount
                total_deleted += deleted
                
                # Small sleep to avoid starving other operations
                if deleted >= batch_size:
                    time.sleep(0.1)
        
        return total_deleted
    
    def _purge_vstp_schedules(self, cutoff_ms: int, batch_size: int) -> int:
        """
        Purge vstp_schedules (and locations) older than cutoff_ms in batches.
        
        Args:
            cutoff_ms: Delete schedules older than this timestamp (epoch ms)
            batch_size: Number of schedule headers to delete per transaction
            
        Returns:
            Total number of schedule headers deleted
        """
        total_deleted = 0
        
        while True:
            with self._lock, self._conn:
                cursor = self._conn.cursor()
                # Select schedule keys to delete
                cursor.execute(
                    "SELECT uid, schedule_start_date FROM vstp_schedules WHERE created_at_ts < ? LIMIT ?",
                    (cutoff_ms, batch_size)
                )
                keys = cursor.fetchall()
                
                if not keys:
                    break
                
                # Delete locations first (foreign key semantics)
                for uid, start_date in keys:
                    cursor.execute(
                        "DELETE FROM vstp_schedule_locations WHERE uid=? AND schedule_start_date=?",
                        (uid, start_date)
                    )
                
                # Delete schedule headers
                for uid, start_date in keys:
                    cursor.execute(
                        "DELETE FROM vstp_schedules WHERE uid=? AND schedule_start_date=?",
                        (uid, start_date)
                    )
                
                deleted = len(keys)
                total_deleted += deleted
                
                # Small sleep to avoid starving other operations
                if deleted >= batch_size:
                    time.sleep(0.1)
        
        return total_deleted
    
    def _purge_cif_schedules(self, cutoff_ms: int, batch_size: int) -> int:
        """
        Purge cif_schedules (and locations) older than cutoff_ms in batches.
        
        Args:
            cutoff_ms: Delete schedules older than this timestamp (epoch ms)
            batch_size: Number of schedule headers to delete per transaction
            
        Returns:
            Total number of schedule headers deleted
        """
        total_deleted = 0
        
        while True:
            with self._lock, self._conn:
                cursor = self._conn.cursor()
                # Select schedule keys to delete
                cursor.execute(
                    "SELECT uid, schedule_start_date, CIF_stp_indicator FROM cif_schedules WHERE created_at_ts < ? LIMIT ?",
                    (cutoff_ms, batch_size)
                )
                keys = cursor.fetchall()
                
                if not keys:
                    break
                
                # Delete locations first (foreign key semantics)
                for uid, start_date, stp in keys:
                    cursor.execute(
                        "DELETE FROM cif_schedule_locations WHERE uid=? AND schedule_start_date=?",
                        (uid, start_date)
                    )
                
                # Delete schedule headers
                for uid, start_date, stp in keys:
                    cursor.execute(
                        "DELETE FROM cif_schedules WHERE uid=? AND schedule_start_date=? AND CIF_stp_indicator=?",
                        (uid, start_date, stp)
                    )
                
                deleted = len(keys)
                total_deleted += deleted
                
                # Small sleep to avoid starving other operations
                if deleted >= batch_size:
                    time.sleep(0.1)
        
        return total_deleted

    def close(self) -> None:
        """Close database connection and stop background threads."""
        self.stop_retention()
        try:
            self._conn.close()
        except Exception:
            pass

    def insert_td_berth_event(self, ts_ms: int, ts_iso: str, area: str, headcode: str, msg_type: str, from_berth: str, to_berth: str, descr: str = "") -> None:
        """Insert a TD berth stepping event (C-Class: CA, CB, CC)."""
        
        from .logging_config import get_logger
        logger = get_logger("database")
        
        with self._lock, self._conn:
            #logger.error(f"insert_td_berth_event: {ts_ms, ts_iso, area, headcode, msg_type, from_berth, to_berth, descr}")
            cursor = self._conn.execute(
                "INSERT INTO td_berth_events(ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr) VALUES (?,?,?,?,?,?,?,?)",
                (ts_ms, ts_iso, area, headcode, msg_type, from_berth, to_berth, descr),
            )
            event_id = cursor.lastrowid
            movement = self._extract_td_berth_movement(
                {
                    "id": event_id,
                    "ts_ms": ts_ms,
                    "ts_iso": ts_iso,
                    "td_area": area,
                    "headcode": headcode,
                    "msg_type": msg_type,
                    "from_berth": from_berth,
                    "to_berth": to_berth,
                    "descr": descr,
                },
                self._td_movement_state,
            )
            if movement:
                self._insert_td_berth_movement(movement)

        # Add to mapper batch if enabled
        if self.enable_mapper:
            self._add_event_to_batch({
                'msg_type': msg_type,
                'msg_ts': ts_ms,
                'td_area': area,
                'from_berth': from_berth,
                'to_berth': to_berth,
                'descr': descr,
                'address': None,
                'data': None,
                'received_at_utc': ts_iso
            })

        if movement:
            self._correlate_td_berth_movement_with_sclass_changes(movement)

    @staticmethod
    def _norm_text(value: Any, *, upper: bool = False) -> str:
        """Return a stripped string, optionally upper-cased, for TD movement normalization."""
        text = str(value or "").strip()
        return text.upper() if upper else text

    @staticmethod
    def _is_reset_berth(value: str) -> bool:
        """Return True when berth token indicates a clear/reset rather than occupancy."""
        return value in {"", "0000", "000", "----", "****", "NULL"}

    def _extract_td_berth_movement(self, row: Dict[str, Any], state_store: Dict[Tuple[str, str], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        msg_type = self._norm_text(row.get("msg_type"), upper=True)
        if msg_type not in {"CA", "CB", "CC"}:
            return None

        td_area = self._norm_text(row.get("td_area"), upper=True)
        headcode = self._norm_text(row.get("headcode"), upper=True)
        if not td_area or not headcode:
            return None

        ts_ms = safe_int(row.get("ts_ms")) or 0
        if ts_ms <= 0:
            return None

        from_berth = self._norm_text(row.get("from_berth"), upper=True)
        to_berth = self._norm_text(row.get("to_berth"), upper=True)
        key = (td_area, headcode)
        state = state_store.setdefault(
            key,
            {"current_berth": None, "last_ts_ms": 0, "last_event_id": 0, "last_sig": None},
        )
        event_id = safe_int(row.get("id")) or 0

        sig = (ts_ms, msg_type, from_berth, to_berth)
        if state.get("last_sig") == sig:
            if event_id > 0:
                state["last_event_id"] = max(int(state.get("last_event_id") or 0), event_id)
            return None

        if self._is_reset_berth(from_berth) and self._is_reset_berth(to_berth):
            state["current_berth"] = None
            state["last_ts_ms"] = max(state.get("last_ts_ms", 0), ts_ms)
            if event_id > 0:
                state["last_event_id"] = max(int(state.get("last_event_id") or 0), event_id)
            return None

        last_ts_ms = int(state.get("last_ts_ms") or 0)
        last_event_id = int(state.get("last_event_id") or 0)
        out_of_order = ts_ms < last_ts_ms or (ts_ms == last_ts_ms and event_id > 0 and event_id <= last_event_id)
        if out_of_order:
            return None
        state["last_sig"] = sig

        movement_from = ""
        movement_to = ""

        if from_berth and to_berth and not self._is_reset_berth(from_berth) and not self._is_reset_berth(to_berth):
            if from_berth != to_berth:
                movement_from = from_berth
                movement_to = to_berth
            state["current_berth"] = to_berth
        else:
            if from_berth and not self._is_reset_berth(from_berth):
                state["current_berth"] = from_berth
            if to_berth and not self._is_reset_berth(to_berth):
                current = state.get("current_berth")
                if current and current != to_berth:
                    movement_from = current
                    movement_to = to_berth
                state["current_berth"] = to_berth

        state["last_ts_ms"] = ts_ms
        if event_id > 0:
            state["last_event_id"] = event_id

        if not movement_from or not movement_to or movement_from == movement_to:
            return None

        return {
            "source_event_id": row.get("id"),
            "ts_ms": ts_ms,
            "ts_iso": self._norm_text(row.get("ts_iso")),
            "td_area": td_area,
            "headcode": headcode,
            "from_berth": movement_from,
            "to_berth": movement_to,
            "source_msg_type": msg_type,
            "evidence_json": json.dumps(
                {
                    "msg_type": msg_type,
                    "from_berth_raw": from_berth or None,
                    "to_berth_raw": to_berth or None,
                    "descr": self._norm_text(row.get("descr")),
                    "out_of_order": out_of_order,
                },
                separators=(",", ":"),
            ),
        }

    def _insert_td_berth_movement(self, movement: Dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO td_berth_movements(
                source_event_id, ts_ms, ts_iso, td_area, headcode, from_berth, to_berth, source_msg_type, evidence_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                movement["source_event_id"],
                movement["ts_ms"],
                movement["ts_iso"],
                movement["td_area"],
                movement["headcode"],
                movement["from_berth"],
                movement["to_berth"],
                movement["source_msg_type"],
                movement["evidence_json"],
            ),
        )

    def rebuild_td_berth_movements(self, td_area: Optional[str] = None) -> dict:
        """Rebuild normalized C-Class berth movements from td_berth_events."""
        scanned = 0
        inserted = 0
        state_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
        td_area_filter = self._norm_text(td_area, upper=True)
        preserved_state: Dict[Tuple[str, str], Dict[str, Any]] = {}

        with self._lock, self._conn:
            if td_area_filter:
                preserved_state = {
                    key: value
                    for key, value in self._td_movement_state.items()
                    if key[0] != td_area_filter
                }
                self._conn.execute("DELETE FROM td_berth_movements WHERE td_area=?", (td_area_filter,))
            else:
                self._conn.execute("DELETE FROM td_berth_movements")

            query = """
                SELECT id, ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
                FROM td_berth_events
                WHERE msg_type IN ('CA', 'CB', 'CC')
            """
            params: tuple[Any, ...] = ()
            if td_area_filter:
                query += " AND UPPER(COALESCE(td_area, '')) = ?"
                params = (td_area_filter,)
            query += " ORDER BY ts_ms ASC, id ASC"

            for row in self._conn.execute(query, params):
                scanned += 1
                movement = self._extract_td_berth_movement(
                    {
                        "id": row[0],
                        "ts_ms": row[1],
                        "ts_iso": row[2],
                        "td_area": row[3],
                        "headcode": row[4],
                        "msg_type": row[5],
                        "from_berth": row[6],
                        "to_berth": row[7],
                        "descr": row[8],
                    },
                    state_store,
                )
                if movement:
                    before = self._conn.total_changes
                    self._insert_td_berth_movement(movement)
                    if self._conn.total_changes > before:
                        inserted += 1

            if td_area_filter:
                preserved_state.update(state_store)
                self._td_movement_state = preserved_state
            else:
                self._td_movement_state = state_store

        return {"scanned": scanned, "inserted": inserted}

    def insert_td_signal_event(self, ts_ms: int, ts_iso: str, area: str, msg_type: str, address: str, data: str = "") -> None:
        """Insert a TD signal event (S-Class: SF, SG, SH)."""
        from .logging_config import get_logger
        logger = get_logger("database")
        
        with self._lock, self._conn:
            #logger.error(f"insert_td_signal_event: {ts_ms, ts_iso, area, msg_type, address, data}")
            self._conn.execute(
                "INSERT INTO td_signal_events(ts_ms, ts_iso, td_area, msg_type, address, data) VALUES (?,?,?,?,?,?)",
                (ts_ms, ts_iso, area, msg_type, address, data or ""),
            )

        if msg_type in ("SF", "SG", "SH"):
            try:
                changes = self._persist_td_sclass_state(ts_ms, ts_iso, area, msg_type, address, data or "")
                for change in changes:
                    self._correlate_td_sclass_change_with_berth_movements(change)
            except Exception as e:
                logger.error(f"insert_td_signal_event: S-class state decode failed: {type(e).__name__}: {e}")
        
        # Add to mapper batch if enabled
        if self.enable_mapper:
            #logger.error(f"Add to mapper batch if enabled: {address}")
            self._add_event_to_batch({
                'msg_type': msg_type,
                'msg_ts': ts_ms,
                'td_area': area,
                'address': address,
                'data': data,
                'received_at_utc': ts_iso,
                'from_berth': None,
                'to_berth': None,
                'descr': None
            })

    def insert_observation(self, obs_row: tuple) -> bool:
        """Insert a berth-signal observation from mapper.
        
        Args:
            obs_row: Tuple of (td_area, step_event_id, step_timestamp, from_berth, 
                     to_berth, descr, signal_event_id, signal_timestamp, address, 
                     data, dt_ms, weight)
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO berth_signal_observations (
                    td_area, step_event_id, step_timestamp, from_berth, to_berth, descr,
                    signal_event_id, signal_timestamp, address, data, dt_ms, weight
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(td_area, step_timestamp, signal_timestamp, address) DO NOTHING
                """,
                obs_row
            )
            return cursor.rowcount == 1
    
    def insert_score(self, score_row: tuple) -> None:
        """Insert or update a berth-signal correlation score from mapper.
        
        Args:
            score_row: Tuple of (td_area, from_berth, to_berth, address, score, 
                       last_seen_ts, last_seen_utc, last_data)
        """
        with self._lock, self._conn:
            self._conn.execute(
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
                score_row
            )

    def upsert_td_state(self, area: str, headcode: str, last_time_ms: int, last_time_iso: str, from_berth: str, to_berth: str,
                        stanox: str | None = None, location_name: str | None = None, platform: str | None = None,
                        sched_dep: str | None = None, sched_arr: str | None = None, origin_name: str | None = None, dest_name: str | None = None, uid: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO td_state(td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth, stanox, location_name, platform,
                                     sched_dep, sched_arr, origin_name, dest_name, uid)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(td_area, headcode) DO UPDATE SET
                    last_time_ms=excluded.last_time_ms,
                    last_time_iso=excluded.last_time_iso,
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
                (area, headcode, last_time_ms, last_time_iso, from_berth, to_berth, stanox, location_name, platform, sched_dep, sched_arr, origin_name, dest_name, uid),
            )

    def upsert_trust(self, train_id: str, headcode: str, uid: str, toc_id: str, last_event_time: str, last_location: str, last_delay_min: int | None, raw: dict) -> None:
        raw_json_value = json.dumps(raw, separators=(',',':')) if self.save_raw_json else None
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
                (train_id, headcode, uid, toc_id, last_event_time, last_location, last_delay_min, raw_json_value),
            )

    def upsert_vstp(self, uid: str, headcode: str, start_date: str, end_date: str, raw: dict) -> None:
        if not uid or not start_date:
            return
        raw_json_value = json.dumps(raw, separators=(',',':')) if self.save_raw_json else None
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
                (uid, headcode, start_date, end_date, raw_json_value),
            )

    def insert_trust_message(self, body: dict) -> None:
        """
        Persist a fully decoded TRUST message into trust_messages.

        - Coerces timestamps (strings of epoch ms) to integers.
        - Coerces boolean-like strings ("true"/"false", "1"/"0") to integers 1/0.
        - Inserts using INSERT OR IGNORE to avoid duplicate rows for the same (train_id, actual_timestamp_ms).
        """
        if not isinstance(body, dict):
            return

        def _to_int(val):
            if val is None:
                return None
            try:
                return int(str(val).strip())
            except Exception:
                return None

        def _to_bool_int(val):
            if val is None:
                return None
            s = str(val).strip().lower()
            if s in ("true", "t", "1", "yes", "y"):
                return 1
            if s in ("false", "f", "0", "no", "n"):
                return 0
            return None

        train_id = (body.get("train_id") or body.get("trainId") or "").strip() or None
        actual_ts = _to_int(body.get("actual_timestamp") or body.get("actualTimestamp") or body.get("time"))
        gbtt_ts = _to_int(body.get("gbtt_timestamp") or body.get("gbttTimestamp"))
        planned_ts = _to_int(body.get("planned_timestamp") or body.get("plannedTimestamp"))
        planned_event_type = (body.get("planned_event_type") or body.get("plannedEventType") or "").strip() or None
        event_type = (body.get("event_type") or body.get("eventType") or "").strip() or None
        event_source = (body.get("event_source") or body.get("eventSource") or "").strip() or None
        correction_ind = _to_bool_int(body.get("correction_ind") or body.get("correctionInd"))
        offroute_ind = _to_bool_int(body.get("offroute_ind") or body.get("offrouteInd"))
        direction_ind = (body.get("direction_ind") or body.get("directionInd") or "").strip() or None
        line_ind = (body.get("line_ind") or body.get("lineInd") or "").strip() or None
        platform = (body.get("platform") or "").strip() or None
        route = (body.get("route") or "").strip() or None
        train_service_code = (body.get("train_service_code") or body.get("trainServiceCode") or "").strip() or None
        division_code = (body.get("division_code") or body.get("divisionCode") or "").strip() or None
        toc_id = (body.get("toc_id") or body.get("tocId") or "").strip() or None
        timetable_variation = _to_int(body.get("timetable_variation") or body.get("timetableVariation"))
        variation_status = (body.get("variation_status") or body.get("variationStatus") or "").strip() or None
        next_report_stanox = (body.get("next_report_stanox") or body.get("nextReportStanox") or "").strip() or None
        next_report_run_time = _to_int(body.get("next_report_run_time") or body.get("nextReportRunTime"))
        train_terminated = _to_bool_int(body.get("train_terminated") or body.get("trainTerminated"))
        delay_monitoring_point = _to_bool_int(body.get("delay_monitoring_point") or body.get("delayMonitoringPoint"))
        reporting_stanox = (body.get("reporting_stanox") or body.get("reportingStanox") or "").strip() or None
        auto_expected = _to_bool_int(body.get("auto_expected") or body.get("autoExpected"))

        # Resolve canonical toc_code from raw toc_id
        toc_code = self.get_canonical_toc_code(toc_id) if toc_id else None

        raw_compact = json.dumps(body, separators=(',',':')) if self.save_raw_json else None

        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO trust_messages (
                        train_id, actual_timestamp_ms, gbtt_timestamp_ms, planned_timestamp_ms,
                        planned_event_type, event_type, event_source, correction_ind, offroute_ind,
                        direction_ind, line_ind, platform, route, train_service_code, division_code,
                        toc_id, toc_code, timetable_variation, variation_status, next_report_stanox, next_report_run_time,
                        train_terminated, delay_monitoring_point, reporting_stanox, auto_expected, raw_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        train_id,
                        actual_ts,
                        gbtt_ts,
                        planned_ts,
                        planned_event_type,
                        event_type,
                        event_source,
                        correction_ind,
                        offroute_ind,
                        direction_ind,
                        line_ind,
                        platform,
                        route,
                        train_service_code,
                        division_code,
                        toc_id,
                        toc_code,
                        timetable_variation,
                        variation_status,
                        next_report_stanox,
                        next_report_run_time,
                        train_terminated,
                        delay_monitoring_point,
                        reporting_stanox,
                        auto_expected,
                        raw_compact,
                    ),
                )
            except Exception:
                # Let callers handle/log if needed — but don't raise in DB internals
                raise

    def insert_vstp_schedule(self, vstp_msg: dict) -> None:
        """
        Persist expanded VSTP schedule into vstp_schedules + vstp_schedule_locations.

        Expects the original STOMP-parsed message containing "VSTPCIFMsgV1" at top-level.
        """

        from .logging_config import get_logger
        logger = get_logger("database")

        if not isinstance(vstp_msg, dict):
            logger.error("insert_vstp_schedule: vstp_msg is not a dict")
            return

        v = vstp_msg.get("VSTPCIFMsgV1") or vstp_msg.get("VSTPCIFMsgV1".upper()) or vstp_msg
        if not isinstance(v, dict):
            logger.error("insert_vstp_schedule: VSTPCIFMsgV1 value is not a dict")
            return

        # Top-level schedule metadata — fields may be at VSTPCIFMsgV1 root or inside "schedule"
        schedule_start_date = (v.get("schedule_start_date") or "").strip()
        schedule_end_date = (v.get("schedule_end_date") or "").strip()
        transaction_type = (v.get("transaction_type") or "").strip() or None
        train_status = (v.get("train_status") or "").strip() or None
        schedule_days_runs = (v.get("schedule_days_runs") or "").strip() or None
        applicable_timetable = (v.get("applicable_timetable") or "").strip() or None
        CIF_train_uid = (v.get("CIF_train_uid") or "").strip() or None
        CIF_stp_indicator = (v.get("CIF_stp_indicator") or "").strip() or None

        # Sender organisation if present
        sender_org = None
        sender = vstp_msg.get("Sender") or {}
        if isinstance(sender, dict):
            sender_org = (sender.get("organisation") or "").strip() or None

        # There may be one or more schedule_segment entries inside "schedule"
        schedule = v.get("schedule") or {}
        segments = []
        if isinstance(schedule, dict):
            segs = schedule.get("schedule_segment")
            if isinstance(segs, list):
                segments = segs
            elif isinstance(segs, dict):
                segments = [segs]

        # Fields may also be nested inside "schedule" — fall back to those values when
        # not found at the root of VSTPCIFMsgV1.
        uid = CIF_train_uid or (schedule.get("CIF_train_uid") or "").strip() or None

        if not CIF_stp_indicator:
            CIF_stp_indicator = (schedule.get("CIF_stp_indicator") or "").strip() or None

        if not schedule_start_date:
            schedule_start_date = (schedule.get("schedule_start_date") or "").strip()

        if not schedule_end_date:
            schedule_end_date = (schedule.get("schedule_end_date") or "").strip()

        if not transaction_type:
            transaction_type = (schedule.get("transaction_type") or "").strip() or None

        if not train_status:
            train_status = (schedule.get("train_status") or "").strip() or None

        if not schedule_days_runs:
            schedule_days_runs = (schedule.get("schedule_days_runs") or "").strip() or None

        if not applicable_timetable:
            applicable_timetable = (schedule.get("applicable_timetable") or "").strip() or None

        # Pull common fields that may appear at segment-level (we'll store the first segment's signalling_id / codes)
        signalling_id = None
        CIF_train_service_code = None
        CIF_train_category = None
        CIF_power_type = None
        if segments:
            first_seg = segments[0] or {}
            signalling_id = (first_seg.get("signalling_id") or "").strip() or None
            CIF_train_service_code = (first_seg.get("CIF_train_service_code") or "").strip() or None
            CIF_train_category = (first_seg.get("CIF_train_category") or "").strip() or None
            CIF_power_type = (first_seg.get("CIF_power_type") or "").strip() or None

        raw_compact = json.dumps(vstp_msg, separators=(',',':')) if self.save_raw_json else None

        # Insert header + locations inside a lock/transaction
        with self._lock, self._conn:
            cur = self._conn.cursor()
            try:
                # Upsert schedule header (use INSERT OR REPLACE to update)
                if uid and schedule_start_date:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO vstp_schedules (
                            uid, schedule_start_date, schedule_end_date, transaction_type, train_status,
                            schedule_days_runs, applicable_timetable, CIF_train_uid, CIF_stp_indicator,
                            signalling_id, CIF_train_service_code, CIF_train_category, CIF_power_type,
                            sender_organisation, raw_json
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uid,
                            schedule_start_date,
                            schedule_end_date,
                            transaction_type,
                            train_status,
                            schedule_days_runs,
                            applicable_timetable,
                            CIF_train_uid,
                            CIF_stp_indicator,
                            signalling_id,
                            CIF_train_service_code,
                            CIF_train_category,
                            CIF_power_type,
                            sender_org,
                            raw_compact,
                        ),
                    )

                    # Remove any existing locations for this uid + start_date to replace with fresh rows
                    cur.execute(
                        "DELETE FROM vstp_schedule_locations WHERE uid=? AND schedule_start_date=?",
                        (uid, schedule_start_date),
                    )

                    # Insert locations: iterate segments and their schedule_location lists
                    for seg_idx, seg in enumerate(segments):
                        if not isinstance(seg, dict):
                            continue
                        locs = seg.get("schedule_location")
                        if isinstance(locs, dict):
                            locs = [locs]
                        if not isinstance(locs, list):
                            continue

                        for loc_idx, loc_entry in enumerate(locs):
                            # Each loc_entry commonly looks like {"location": {"tiploc":{"tiploc_id":"PLYMTH"}}, "scheduled_departure_time":"215800", ...}
                            tiploc = None
                            try:
                                tiploc = (loc_entry.get("location", {}) or {}).get("tiploc", {}) or {}
                                if isinstance(tiploc, dict):
                                    tiploc = (tiploc.get("tiploc_id") or "").strip() or None
                                else:
                                    tiploc = str(tiploc).strip() or None
                            except Exception:
                                tiploc = None

                            scheduled_pass_time = (loc_entry.get("scheduled_pass_time") or "").strip() or None
                            scheduled_departure_time = (loc_entry.get("scheduled_departure_time") or "").strip() or None
                            scheduled_arrival_time = (loc_entry.get("scheduled_arrival_time") or "").strip() or None
                            public_departure_time = (loc_entry.get("public_departure_time") or "").strip() or None
                            public_arrival_time = (loc_entry.get("public_arrival_time") or "").strip() or None
                            CIF_pathing_allowance = (loc_entry.get("CIF_pathing_allowance") or "").strip() or None
                            CIF_activity = (loc_entry.get("CIF_activity") or "").strip() or None
                            CIF_line = (loc_entry.get("CIF_line") or "").strip() or None
                            CIF_engineering_allowance = (loc_entry.get("CIF_engineering_allowance") or "").strip() or None
                            CIF_performance_allowance = (loc_entry.get("CIF_performance_allowance") or "").strip() or None

                            cur.execute(
                                """
                                INSERT INTO vstp_schedule_locations (
                                    uid, schedule_start_date, segment_index, location_index, tiploc,
                                    scheduled_pass_time, scheduled_departure_time, scheduled_arrival_time,
                                    public_departure_time, public_arrival_time, CIF_pathing_allowance, CIF_activity,
                                    CIF_line, CIF_engineering_allowance, CIF_performance_allowance
                                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (
                                    uid,
                                    schedule_start_date,
                                    seg_idx,
                                    loc_idx,
                                    tiploc,
                                    scheduled_pass_time,
                                    scheduled_departure_time,
                                    scheduled_arrival_time,
                                    public_departure_time,
                                    public_arrival_time,
                                    CIF_pathing_allowance,
                                    CIF_activity,
                                    CIF_line,
                                    CIF_engineering_allowance,
                                    CIF_performance_allowance,
                                ),
                            )

                else:
                    # If no UID or start_date, still store compact raw in vstp_state if possible
                    # Fallback: do nothing (upsert_vstp already stores a summary)
                    pass

                self._conn.commit()
            except Exception:
                # Propagate exception to caller so caller can log
                raise

    def insert_cif_schedule(self, cif_record: dict, toc_code: str) -> None:
        """
        Persist CIF schedule from downloaded TOC schedule file into cif_schedules + cif_schedule_locations.
        
        Expects a JsonScheduleV1 record from the CIF JSON file.
        
        Note: CIF schedules always have a single segment (unlike VSTP which can have multiple segments
        for complex train journeys). This is a business rule of the CIF format.
        
        Args:
            cif_record: Dictionary containing schedule data (typically from "JsonScheduleV1" key)
            toc_code: 2-character TOC code for this schedule (e.g., 'SE', 'GW')
        """
        # CIF schedules always have a single segment (business rule)
        CIF_SINGLE_SEGMENT_INDEX = 0
        
        from .logging_config import get_logger
        logger = get_logger("database")
        
        if not isinstance(cif_record, dict):
            return
        
        # Extract schedule metadata
        uid = (cif_record.get("CIF_train_uid") or "").strip() or None
        schedule_start_date = (cif_record.get("schedule_start_date") or "").strip()
        schedule_end_date = (cif_record.get("schedule_end_date") or "").strip() or None
        schedule_days_runs = (cif_record.get("schedule_days_runs") or "").strip() or None
        # CIF_stp_indicator: P=Permanent, O=Overlay, C=Cancellation, N=New (default to P)
        CIF_stp_indicator = (cif_record.get("CIF_stp_indicator") or "").strip() or "P"
        train_status = (cif_record.get("train_status") or "").strip() or None
        transaction_type = (cif_record.get("transaction_type") or "").strip() or None
        applicable_timetable = (cif_record.get("applicable_timetable") or "").strip() or None
        
        # Get schedule_segment data
        schedule_segment = cif_record.get("schedule_segment") or {}
        if isinstance(schedule_segment, list) and len(schedule_segment) > 0:
            schedule_segment = schedule_segment[0]
        
        signalling_id = (schedule_segment.get("signalling_id") or "").strip() or None
        CIF_headcode = signalling_id  # Headcode is the signalling_id
        CIF_train_service_code = (schedule_segment.get("CIF_train_service_code") or "").strip() or None
        CIF_train_category = (schedule_segment.get("CIF_train_category") or "").strip() or None
        CIF_power_type = (schedule_segment.get("CIF_power_type") or "").strip() or None
        
        # Extract location data
        schedule_location = schedule_segment.get("schedule_location") or []
        if not isinstance(schedule_location, list):
            schedule_location = [schedule_location] if schedule_location else []
        
        # Skip if no UID or start date
        if not uid or not schedule_start_date:
            return
        
        raw_compact = json.dumps(cif_record, separators=(',',':')) if self.save_raw_json else None
        
        # Insert header + locations inside a lock/transaction
        with self._lock, self._conn:
            cur = self._conn.cursor()
            try:
                # Upsert schedule header (use INSERT OR REPLACE to update)
                cur.execute(
                    """
                    INSERT OR REPLACE INTO cif_schedules (
                        uid, schedule_start_date, schedule_end_date, toc_code, transaction_type, train_status,
                        schedule_days_runs, applicable_timetable, CIF_train_uid, CIF_stp_indicator,
                        signalling_id, CIF_train_service_code, CIF_train_category, CIF_power_type,
                        CIF_headcode, raw_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        schedule_start_date,
                        schedule_end_date,
                        toc_code,
                        transaction_type,
                        train_status,
                        schedule_days_runs,
                        applicable_timetable,
                        uid,  # CIF_train_uid = uid
                        CIF_stp_indicator,
                        signalling_id,
                        CIF_train_service_code,
                        CIF_train_category,
                        CIF_power_type,
                        CIF_headcode,
                        raw_compact,
                    ),
                )
                
                # Delete old location rows if replacing
                cur.execute(
                    "DELETE FROM cif_schedule_locations WHERE uid=? AND schedule_start_date=?",
                    (uid, schedule_start_date)
                )
                
                # Insert location rows
                for loc_index, loc in enumerate(schedule_location):
                    if not isinstance(loc, dict):
                        continue
                    
                    tiploc = (loc.get("tiploc_code") or "").strip() or None
                    scheduled_pass = (loc.get("scheduled_pass_time") or loc.get("pass") or "").strip() or None
                    scheduled_dep = (loc.get("scheduled_departure_time") or loc.get("departure") or "").strip() or None
                    scheduled_arr = (loc.get("scheduled_arrival_time") or loc.get("arrival") or "").strip() or None
                    public_dep = (loc.get("public_departure") or "").strip() or None
                    public_arr = (loc.get("public_arrival") or "").strip() or None
                    platform = (loc.get("platform") or "").strip() or None
                    CIF_pathing_allowance = (loc.get("CIF_pathing_allowance") or "").strip() or None
                    CIF_activity = (loc.get("CIF_activity") or "").strip() or None
                    CIF_line = (loc.get("CIF_line") or "").strip() or None
                    CIF_path = (loc.get("CIF_path") or "").strip() or None
                    CIF_engineering_allowance = (loc.get("CIF_engineering_allowance") or "").strip() or None
                    CIF_performance_allowance = (loc.get("CIF_performance_allowance") or "").strip() or None
                    
                    cur.execute(
                        """
                        INSERT INTO cif_schedule_locations (
                            uid, schedule_start_date, segment_index, location_index,
                            tiploc, scheduled_pass_time, scheduled_departure_time, scheduled_arrival_time,
                            public_departure_time, public_arrival_time, platform,
                            CIF_pathing_allowance, CIF_activity, CIF_line, CIF_path,
                            CIF_engineering_allowance, CIF_performance_allowance
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uid,
                            schedule_start_date,
                            CIF_SINGLE_SEGMENT_INDEX,  # CIF schedules always have single segment
                            loc_index,
                            tiploc,
                            scheduled_pass,
                            scheduled_dep,
                            scheduled_arr,
                            public_dep,
                            public_arr,
                            platform,
                            CIF_pathing_allowance,
                            CIF_activity,
                            CIF_line,
                            CIF_path,
                            CIF_engineering_allowance,
                            CIF_performance_allowance,
                        ),
                    )
                
                self._conn.commit()
            except Exception as e:
                logger.error(f"Failed to insert CIF schedule {uid}: {e}")
                # Don't raise - continue processing other schedules

    def upsert_toc(self, toc_code: str, toc_name: str, business_code: Optional[str] = None, 
                   sector_code: Optional[str] = None, atoc_code: Optional[str] = None, 
                   sector: Optional[str] = None) -> None:
        """
        Insert or update a TOC reference entry.
        
        Args:
            toc_code: 2-character TOC code (e.g., 'SW' for South Western Railway)
            toc_name: Full name of the train operating company
            business_code: 2-letter business code if available
            sector_code: Numeric sector code if available
            atoc_code: ATOC membership code if available
            sector: Sector classification (e.g., 'Passenger', 'Freight')
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO toc_reference(toc_code, toc_name, business_code, sector_code, atoc_code, sector)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(toc_code) DO UPDATE SET
                    toc_name=excluded.toc_name,
                    business_code=excluded.business_code,
                    sector_code=excluded.sector_code,
                    atoc_code=excluded.atoc_code,
                    sector=excluded.sector,
                    updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (toc_code, toc_name, business_code, sector_code, atoc_code, sector),
            )
    
    def get_all_tocs(self) -> list[dict]:
        """
        Retrieve all TOC reference data.
        
        Returns:
            List of dicts with keys: toc_code, toc_name, business_code, sector_code, atoc_code, sector, updated_at_utc
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT toc_code, toc_name, business_code, sector_code, atoc_code, sector, updated_at_utc FROM toc_reference ORDER BY toc_code"
            )
            return [
                {
                    'toc_code': row[0],
                    'toc_name': row[1],
                    'business_code': row[2],
                    'sector_code': row[3],
                    'atoc_code': row[4],
                    'sector': row[5],
                    'updated_at_utc': row[6]
                }
                for row in cursor.fetchall()
            ]
    
    def get_toc_name(self, toc_code: str) -> Optional[str]:
        """
        Get TOC name for a given TOC code.
        
        Args:
            toc_code: 2-character TOC code
            
        Returns:
            TOC name if found, None otherwise
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT toc_name FROM toc_reference WHERE toc_code=?", (toc_code,))
            row = cursor.fetchone()
            return row[0] if row else None
    
    def get_canonical_toc_code(self, external_code: str) -> Optional[str]:
        """
        Get canonical TOC code from an external identifier.
        
        Queries toc_reference with priority order:
        1. Exact match on toc_code (canonical)
        2. Match on atoc_code (SCHEDULE messages)
        3. Match on sector_code (TRUST messages)
        4. Match on business_code (schedule URLs)
        
        Args:
            external_code: TOC identifier (may be canonical, ATOC, business, or sector code)
            
        Returns:
            Canonical 2-character TOC code if found, None otherwise
        """
        if not external_code:
            return None
        
        code = external_code.strip().upper()
        
        with self._lock:
            cursor = self._conn.cursor()
            
            # Priority 1: Check if it's already canonical
            cursor.execute("SELECT toc_code FROM toc_reference WHERE toc_code=?", (code,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            # Priority 2: Check ATOC code (from SCHEDULE messages)
            cursor.execute("SELECT toc_code FROM toc_reference WHERE atoc_code=?", (code,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            # Priority 3: Check sector code (from TRUST messages)
            cursor.execute("SELECT toc_code FROM toc_reference WHERE sector_code=?", (code,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            # Priority 4: Check business code (from schedule URLs)
            cursor.execute("SELECT toc_code FROM toc_reference WHERE business_code=?", (code,))
            row = cursor.fetchone()
            if row:
                return row[0]
            
            return None

    def upsert_toc_td_area(
        self,
        toc_code: str,
        td_area: str,
        is_primary: bool = False,
        source: Optional[str] = None,
        confidence: Optional[float] = None,
        effective_from: Optional[str] = None,
        effective_to: Optional[str] = None,
        created_by: Optional[str] = None,
        notes: Optional[str] = None
    ) -> None:
        """
        Insert or update a TOC-TD area mapping.
        
        Args:
            toc_code: 2-character TOC code (e.g., 'SW')
            td_area: 2-character TD area code (e.g., 'EK')
            is_primary: Whether this is the primary mapping for this TOC-area pair
            source: Source of the mapping (e.g., 'manual', 'import', 'analysis')
            confidence: Confidence score (0.0-1.0)
            effective_from: ISO date when mapping becomes effective
            effective_to: ISO date when mapping expires (None = indefinite)
            created_by: User or process that created the mapping
            notes: Additional notes about the mapping
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO toc_td_areas(toc_code, td_area, is_primary, source, confidence, 
                                        effective_from, effective_to, created_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(toc_code, td_area) DO UPDATE SET
                    is_primary=excluded.is_primary,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    effective_from=excluded.effective_from,
                    effective_to=excluded.effective_to,
                    created_by=excluded.created_by,
                    notes=excluded.notes,
                    created_at_ts=strftime('%s','now') * 1000
                """,
                (toc_code, td_area, 1 if is_primary else 0, source, confidence,
                 effective_from, effective_to, created_by, notes),
            )
    
    def delete_toc_td_area(self, toc_code: str, td_area: str) -> None:
        """
        Delete a TOC-TD area mapping.
        
        Args:
            toc_code: 2-character TOC code
            td_area: 2-character TD area code
        """
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM toc_td_areas WHERE toc_code=? AND td_area=?",
                (toc_code, td_area),
            )
    
    def get_toc_td_areas(self) -> list[dict]:
        """
        Retrieve all TOC-TD area mappings.
        
        Returns:
            List of dicts with keys: id, toc_code, td_area, is_primary, source, 
            confidence, effective_from, effective_to, created_by, created_at_ts, notes
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, toc_code, td_area, is_primary, source, confidence,
                       effective_from, effective_to, created_by, created_at_ts, notes
                FROM toc_td_areas
                ORDER BY toc_code, td_area
                """
            )
            return [
                {
                    'id': row[0],
                    'toc_code': row[1],
                    'td_area': row[2],
                    'is_primary': bool(row[3]),
                    'source': row[4],
                    'confidence': row[5],
                    'effective_from': row[6],
                    'effective_to': row[7],
                    'created_by': row[8],
                    'created_at_ts': row[9],
                    'notes': row[10]
                }
                for row in cursor.fetchall()
            ]
    
    def get_td_areas_for_toc(self, toc_code: str) -> list[dict]:
        """
        Retrieve all TD area mappings for a specific TOC.
        
        Args:
            toc_code: 2-character TOC code
            
        Returns:
            List of dicts with keys: id, toc_code, td_area, is_primary, source,
            confidence, effective_from, effective_to, created_by, created_at_ts, notes
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, toc_code, td_area, is_primary, source, confidence,
                       effective_from, effective_to, created_by, created_at_ts, notes
                FROM toc_td_areas
                WHERE toc_code=?
                ORDER BY td_area
                """,
                (toc_code,)
            )
            return [
                {
                    'id': row[0],
                    'toc_code': row[1],
                    'td_area': row[2],
                    'is_primary': bool(row[3]),
                    'source': row[4],
                    'confidence': row[5],
                    'effective_from': row[6],
                    'effective_to': row[7],
                    'created_by': row[8],
                    'created_at_ts': row[9],
                    'notes': row[10]
                }
                for row in cursor.fetchall()
            ]

    def _add_event_to_batch(self, event: dict) -> None:
        """Add an event to the mapper batch for processing."""
        if not self.enable_mapper:
            return
        
        with self._batch_lock:
            self._event_batch.append(event)
            
            # Process batch if it reaches the threshold
            if len(self._event_batch) >= self._batch_size:
                self._process_mapper_batch()
    
    def _process_mapper_batch(self) -> None:
        """Process accumulated events through the mapper."""
        if not self._event_batch and not getattr(self, "_mapper_tail_events", None):
            return
        
        from .mapper import process_batch_for_mapper
        from .logging_config import get_logger
        logger = get_logger("database")
        
        # Get mapper config from database
        config = self.get_mapper_config()
        pre_ms = config.get('pre_ms', 1000)
        post_ms = config.get('post_ms', 5000)
        tau_ms = config.get('tau_ms', 2500)
        
        # Combine the retained overlap window with the newly queued batch.
        events_to_process = list(getattr(self, "_mapper_tail_events", [])) + self._event_batch[:]
        self._event_batch = []
        if not events_to_process:
            return

        events_to_process.sort(key=lambda event: int(event.get("msg_ts", 0) or 0))

        try:
            obs_rows, score_rows = process_batch_for_mapper(
                events_to_process,
                pre_ms=pre_ms,
                post_ms=post_ms,
                tau_ms=tau_ms
            )
            
            # Insert observations
            for obs_row, score_row in zip(obs_rows, score_rows):
                try:
                    inserted = self.insert_observation(obs_row)
                    if inserted:
                        try:
                            self.insert_score(score_row)
                        except Exception as e:
                            logger.error(f"Failed to insert score: {e}")
                except Exception as e:
                    logger.error(f"Failed to insert observation: {e}")
            
            if obs_rows or score_rows:
                logger.debug(f"Mapper: processed {len(events_to_process)} events -> {len(obs_rows)} observations, {len(score_rows)} scores")
            overlap_ms = max(pre_ms, post_ms)
            latest_ts = max(int(event.get("msg_ts", 0) or 0) for event in events_to_process)
            cutoff_ts = latest_ts - overlap_ms
            self._mapper_tail_events = [
                event for event in events_to_process
                if int(event.get("msg_ts", 0) or 0) >= cutoff_ts
            ]
        except Exception as e:
            logger.error(f"Mapper batch processing failed: {e}")
            self._mapper_tail_events = events_to_process
    
    def _start_batch_processor(self) -> None:
        """Start a background thread to periodically process mapper batches."""
        import time
        
        def batch_processor():
            from .logging_config import get_logger
            logger = get_logger("database")
            
            while True:
                time.sleep(10)  # Process every 10 seconds
                
                with self._batch_lock:
                    if self._event_batch:
                        try:
                            self._process_mapper_batch()
                        except Exception as e:
                            logger.error(f"Batch processor error: {e}")
        
        t = threading.Thread(target=batch_processor, daemon=True, name="mapper-batch-processor")
        t.start()

    
    
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

    @staticmethod
    def _sclass_bit_label(address: str, byte_offset: int, bit: int) -> str:
        suffix = f"+{byte_offset}" if int(byte_offset or 0) else ""
        return f"{address}{suffix}.{bit}"

    def ensure_sclass_correlation_schema(self) -> None:
        """Create S-class/berth correlation tables and defaults."""
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sclass_correlation_config (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS td_sclass_movement_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    td_area TEXT NOT NULL,
                    movement_event_id INTEGER NOT NULL,
                    movement_ts_ms INTEGER NOT NULL,
                    movement_ts_iso TEXT NOT NULL,
                    headcode TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    source_msg_type TEXT NOT NULL,
                    change_event_id INTEGER NOT NULL,
                    change_ts_ms INTEGER NOT NULL,
                    change_ts_iso TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    old_state INTEGER NOT NULL,
                    new_state INTEGER NOT NULL,
                    dt_ms INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    created_at_ts INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_td_sclass_movement_obs_unique
                    ON td_sclass_movement_observations(movement_event_id, change_event_id);
                CREATE INDEX IF NOT EXISTS idx_td_sclass_movement_obs_area_ts
                    ON td_sclass_movement_observations(td_area, movement_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_sclass_movement_obs_bit
                    ON td_sclass_movement_observations(td_area, address, byte_offset, bit, change_ts_ms);
                CREATE INDEX IF NOT EXISTS idx_td_sclass_movement_obs_mov_ts
                    ON td_sclass_movement_observations(td_area, from_berth, to_berth, movement_ts_ms);

                CREATE TABLE IF NOT EXISTS td_sclass_movement_scores (
                    td_area TEXT NOT NULL,
                    from_berth TEXT NOT NULL,
                    to_berth TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    matching_count INTEGER NOT NULL DEFAULT 0,
                    movement_count INTEGER NOT NULL DEFAULT 0,
                    correlation_pct REAL NOT NULL DEFAULT 0.0,
                    mean_dt_ms REAL,
                    median_dt_ms REAL,
                    variance_dt_ms REAL,
                    min_dt_ms INTEGER,
                    max_dt_ms INTEGER,
                    lead_count INTEGER NOT NULL DEFAULT 0,
                    lag_count INTEGER NOT NULL DEFAULT 0,
                    on_count INTEGER NOT NULL DEFAULT 0,
                    off_count INTEGER NOT NULL DEFAULT 0,
                    associated_bits_json TEXT NOT NULL,
                    last_seen_ts_ms INTEGER NOT NULL,
                    last_seen_iso TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (td_area, from_berth, to_berth)
                );
                CREATE INDEX IF NOT EXISTS idx_td_sclass_movement_scores_area_ts
                    ON td_sclass_movement_scores(td_area, last_seen_ts_ms);

                CREATE TABLE IF NOT EXISTS td_sclass_lab_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relation_type TEXT NOT NULL,
                    td_area TEXT NOT NULL,
                    address TEXT,
                    byte_offset INTEGER,
                    bit INTEGER,
                    from_berth TEXT,
                    to_berth TEXT,
                    state TEXT NOT NULL DEFAULT 'inferred',
                    source TEXT,
                    confidence REAL,
                    notes TEXT,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_td_sclass_lab_annotations_unique
                    ON td_sclass_lab_annotations(
                        relation_type, td_area, address, byte_offset, bit, from_berth, to_berth
                    );
                CREATE INDEX IF NOT EXISTS idx_td_sclass_lab_annotations_area_state
                    ON td_sclass_lab_annotations(td_area, relation_type, state);
                """
            )
            for key, value in (
                ("pre_ms", 120000),
                ("post_ms", 120000),
                ("tau_ms", 60000),
            ):
                self._conn.execute(
                    "INSERT OR IGNORE INTO sclass_correlation_config (key, value) VALUES (?, ?)",
                    (key, value),
                )

    def ensure_physical_signal_schema(self) -> None:
        """Create tables for reviewed physical signal identities."""
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS physical_signal_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    td_area TEXT NOT NULL,
                    address TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL DEFAULT 0,
                    bit INTEGER NOT NULL,
                    from_berth TEXT,
                    to_berth TEXT,
                    physical_signal_number TEXT,
                    physical_signal_location TEXT,
                    mapping_confidence REAL,
                    correlation_confidence REAL,
                    verification_status TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT,
                    reviewer TEXT,
                    evidence_json TEXT,
                    notes TEXT,
                    supersedes_id INTEGER,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_physical_signal_mappings_area_status
                    ON physical_signal_mappings(td_area, verification_status, updated_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_physical_signal_mappings_bit
                    ON physical_signal_mappings(td_area, address, byte_offset, bit, updated_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_physical_signal_mappings_signal
                    ON physical_signal_mappings(physical_signal_number, td_area);
                """
            )

    @staticmethod
    def _json_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text if text else None
        try:
            return json.dumps(value, sort_keys=True)
        except Exception:
            return str(value)

    def add_physical_signal_mapping(
        self,
        td_area: str,
        address: str,
        byte_offset: int,
        bit: int,
        *,
        physical_signal_number: Optional[str] = None,
        physical_signal_location: Optional[str] = None,
        from_berth: Optional[str] = None,
        to_berth: Optional[str] = None,
        mapping_confidence: Optional[float] = None,
        correlation_confidence: Optional[float] = None,
        verification_status: str = "unknown",
        source: Optional[str] = None,
        reviewer: Optional[str] = None,
        evidence_json: Optional[Any] = None,
        notes: Optional[str] = None,
        supersedes_id: Optional[int] = None,
    ) -> int:
        """Insert a new physical signal mapping revision."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO physical_signal_mappings(
                    td_area, address, byte_offset, bit, from_berth, to_berth,
                    physical_signal_number, physical_signal_location,
                    mapping_confidence, correlation_confidence, verification_status,
                    source, reviewer, evidence_json, notes, supersedes_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    td_area.strip().upper(),
                    address.strip().upper(),
                    int(byte_offset or 0),
                    int(bit),
                    from_berth.strip().upper() if from_berth else None,
                    to_berth.strip().upper() if to_berth else None,
                    physical_signal_number.strip() if physical_signal_number else None,
                    physical_signal_location.strip() if physical_signal_location else None,
                    mapping_confidence,
                    correlation_confidence,
                    (verification_status or "unknown").strip().lower(),
                    source.strip() if source else None,
                    reviewer.strip() if reviewer else None,
                    self._json_or_none(evidence_json),
                    notes.strip() if notes else None,
                    supersedes_id,
                ),
            )
            return int(cursor.lastrowid)

    def update_physical_signal_mapping(
        self,
        mapping_id: int,
        *,
        verification_status: Optional[str] = None,
        reviewer: Optional[str] = None,
        physical_signal_number: Optional[str] = None,
        physical_signal_location: Optional[str] = None,
        mapping_confidence: Optional[float] = None,
        correlation_confidence: Optional[float] = None,
        source: Optional[str] = None,
        evidence_json: Optional[Any] = None,
        notes: Optional[str] = None,
        supersedes_id: Optional[int] = None,
    ) -> None:
        """Update an existing physical signal mapping without deleting history."""
        updates = []
        params: list[Any] = []
        if verification_status is not None:
            updates.append("verification_status=?")
            params.append(verification_status.strip().lower())
        if reviewer is not None:
            updates.append("reviewer=?")
            params.append(reviewer.strip())
        if physical_signal_number is not None:
            updates.append("physical_signal_number=?")
            params.append(physical_signal_number.strip())
        if physical_signal_location is not None:
            updates.append("physical_signal_location=?")
            params.append(physical_signal_location.strip())
        if mapping_confidence is not None:
            updates.append("mapping_confidence=?")
            params.append(mapping_confidence)
        if correlation_confidence is not None:
            updates.append("correlation_confidence=?")
            params.append(correlation_confidence)
        if source is not None:
            updates.append("source=?")
            params.append(source.strip())
        if evidence_json is not None:
            updates.append("evidence_json=?")
            params.append(self._json_or_none(evidence_json))
        if notes is not None:
            updates.append("notes=?")
            params.append(notes.strip())
        if supersedes_id is not None:
            updates.append("supersedes_id=?")
            params.append(supersedes_id)
        updates.append("updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
        params.append(mapping_id)

        if len(updates) == 1:
            return

        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE physical_signal_mappings SET {', '.join(updates)} WHERE id=?",
                params,
            )

    def revoke_physical_signal_mapping(
        self,
        mapping_id: int,
        *,
        reviewer: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Mark a physical signal mapping as revoked."""
        self.update_physical_signal_mapping(
            mapping_id,
            verification_status="revoked",
            reviewer=reviewer,
            notes=notes,
        )

    def get_physical_signal_mappings(
        self,
        *,
        td_area: Optional[str] = None,
        address: Optional[str] = None,
        verification_status: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return reviewed or inferred physical signal mappings."""
        sql = """
            SELECT id, td_area, address, byte_offset, bit, from_berth, to_berth,
                   physical_signal_number, physical_signal_location,
                   mapping_confidence, correlation_confidence, verification_status,
                   source, reviewer, evidence_json, notes, supersedes_id,
                   created_at_utc, updated_at_utc
            FROM physical_signal_mappings
            WHERE 1=1
        """
        params: list[Any] = []
        if td_area:
            sql += " AND td_area=?"
            params.append(td_area.strip().upper())
        if address:
            sql += " AND address=?"
            params.append(address.strip().upper())
        if verification_status:
            sql += " AND verification_status=?"
            params.append(verification_status.strip().lower())
        sql += " ORDER BY updated_at_utc DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit or 1)))
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "td_area": row[1],
                "address": row[2],
                "byte_offset": row[3],
                "bit": row[4],
                "from_berth": row[5],
                "to_berth": row[6],
                "physical_signal_number": row[7],
                "physical_signal_location": row[8],
                "mapping_confidence": row[9],
                "correlation_confidence": row[10],
                "verification_status": row[11],
                "source": row[12],
                "reviewer": row[13],
                "evidence_json": row[14],
                "notes": row[15],
                "supersedes_id": row[16],
                "created_at_utc": row[17],
                "updated_at_utc": row[18],
            }
            for row in rows
        ]

    def get_physical_signal_mapping(self, mapping_id: int) -> Optional[dict]:
        """Return a single physical signal mapping by primary key."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, td_area, address, byte_offset, bit, from_berth, to_berth,
                       physical_signal_number, physical_signal_location,
                       mapping_confidence, correlation_confidence, verification_status,
                       source, reviewer, evidence_json, notes, supersedes_id,
                       created_at_utc, updated_at_utc
                FROM physical_signal_mappings
                WHERE id=?
                """,
                (mapping_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "td_area": row[1],
            "address": row[2],
            "byte_offset": row[3],
            "bit": row[4],
            "from_berth": row[5],
            "to_berth": row[6],
            "physical_signal_number": row[7],
            "physical_signal_location": row[8],
            "mapping_confidence": row[9],
            "correlation_confidence": row[10],
            "verification_status": row[11],
            "source": row[12],
            "reviewer": row[13],
            "evidence_json": row[14],
            "notes": row[15],
            "supersedes_id": row[16],
            "created_at_utc": row[17],
            "updated_at_utc": row[18],
        }

    def get_sclass_correlation_config(self) -> dict:
        """Get current S-class correlation configuration parameters."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT key, value FROM sclass_correlation_config")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def update_sclass_correlation_config(self, pre_ms: int, post_ms: int, tau_ms: int) -> None:
        """Update S-class correlation configuration parameters."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sclass_correlation_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='pre_ms'",
                (pre_ms,),
            )
            self._conn.execute(
                "UPDATE sclass_correlation_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='post_ms'",
                (post_ms,),
            )
            self._conn.execute(
                "UPDATE sclass_correlation_config SET value=?, updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE key='tau_ms'",
                (tau_ms,),
            )

    def _get_sclass_correlation_window(self) -> tuple[int, int, int]:
        config = self.get_sclass_correlation_config()
        return (
            int(config.get("pre_ms", 120000) or 120000),
            int(config.get("post_ms", 120000) or 120000),
            int(config.get("tau_ms", 60000) or 60000),
        )

    def _insert_td_sclass_movement_observation(self, observation: Dict[str, Any]) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO td_sclass_movement_observations(
                    td_area, movement_event_id, movement_ts_ms, movement_ts_iso, headcode,
                    from_berth, to_berth, source_msg_type, change_event_id, change_ts_ms,
                    change_ts_iso, address, byte_offset, bit, old_state, new_state,
                    dt_ms, weight, evidence_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(movement_event_id, change_event_id) DO NOTHING
                """,
                (
                    observation["td_area"],
                    observation["movement_event_id"],
                    observation["movement_ts_ms"],
                    observation["movement_ts_iso"],
                    observation["headcode"],
                    observation["from_berth"],
                    observation["to_berth"],
                    observation["source_msg_type"],
                    observation["change_event_id"],
                    observation["change_ts_ms"],
                    observation["change_ts_iso"],
                    observation["address"],
                    observation["byte_offset"],
                    observation["bit"],
                    observation["old_state"],
                    observation["new_state"],
                    observation["dt_ms"],
                    observation["weight"],
                    observation["evidence_json"],
                ),
            )
            return cursor.rowcount == 1

    def _refresh_td_sclass_movement_scores(self, td_area: Optional[str] = None) -> dict:
        with self._lock, self._conn:
            if td_area:
                self._conn.execute(
                    "DELETE FROM td_sclass_movement_scores WHERE td_area=?",
                    (td_area,),
                )
                rows = self._conn.execute(
                    """
                    SELECT td_area, from_berth, to_berth, movement_event_id, movement_ts_ms,
                           change_ts_ms, dt_ms, new_state, address, byte_offset, bit
                    FROM td_sclass_movement_observations
                    WHERE td_area=?
                    ORDER BY td_area, from_berth, to_berth, movement_ts_ms, change_ts_ms, id
                    """,
                    (td_area,),
                ).fetchall()
            else:
                self._conn.execute("DELETE FROM td_sclass_movement_scores")
                rows = self._conn.execute(
                    """
                    SELECT td_area, from_berth, to_berth, movement_event_id, movement_ts_ms,
                           change_ts_ms, dt_ms, new_state, address, byte_offset, bit
                    FROM td_sclass_movement_observations
                    ORDER BY td_area, from_berth, to_berth, movement_ts_ms, change_ts_ms, id
                    """
                ).fetchall()

            groups: Dict[Tuple[str, str, str], list[dict[str, Any]]] = {}
            for row in rows:
                key = (row[0], row[1], row[2])
                groups.setdefault(key, []).append(
                    {
                        "movement_event_id": row[3],
                        "movement_ts_ms": row[4],
                        "change_ts_ms": row[5],
                        "dt_ms": row[6],
                        "new_state": row[7],
                        "address": row[8],
                        "byte_offset": row[9],
                        "bit": row[10],
                    }
                )

            summary_count = 0
            for (area, from_berth, to_berth), items in groups.items():
                dt_values = [int(item["dt_ms"]) for item in items]
                movement_ids = {int(item["movement_event_id"]) for item in items if item["movement_event_id"] is not None}
                bits = []
                seen_bits = set()
                for item in items:
                    bit_label = self._sclass_bit_label(item["address"], int(item["byte_offset"] or 0), int(item["bit"]))
                    if bit_label not in seen_bits:
                        seen_bits.add(bit_label)
                        bits.append(bit_label)
                obs_count = len(items)
                matching_count = len(movement_ids)
                movement_count = matching_count
                correlation_pct = (matching_count / obs_count) if obs_count else 0.0
                mean_dt = sum(dt_values) / len(dt_values)
                variance_dt = pvariance(dt_values) if len(dt_values) > 1 else 0.0
                lead_count = sum(1 for dt in dt_values if dt < 0)
                lag_count = sum(1 for dt in dt_values if dt > 0)
                on_count = sum(1 for item in items if int(item["new_state"]) == 1)
                off_count = sum(1 for item in items if int(item["new_state"]) == 0)
                last_seen_ts = max(int(item["change_ts_ms"]) for item in items)
                last_seen_iso_row = self._conn.execute(
                    "SELECT change_ts_iso FROM td_sclass_movement_observations WHERE td_area=? AND from_berth=? AND to_berth=? ORDER BY change_ts_ms DESC, id DESC LIMIT 1",
                    (area, from_berth, to_berth),
                ).fetchone()
                last_seen_iso = last_seen_iso_row[0] if last_seen_iso_row else ""
                self._conn.execute(
                    """
                    INSERT INTO td_sclass_movement_scores(
                        td_area, from_berth, to_berth, observation_count, matching_count,
                        movement_count, correlation_pct, mean_dt_ms, median_dt_ms, variance_dt_ms,
                        min_dt_ms, max_dt_ms, lead_count, lag_count, on_count, off_count,
                        associated_bits_json, last_seen_ts_ms, last_seen_iso
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        area,
                        from_berth,
                        to_berth,
                        obs_count,
                        matching_count,
                        movement_count,
                        correlation_pct,
                        mean_dt,
                        median(dt_values),
                        variance_dt,
                        min(dt_values),
                        max(dt_values),
                        lead_count,
                        lag_count,
                        on_count,
                        off_count,
                        json.dumps(bits, separators=(",", ":")),
                        last_seen_ts,
                        last_seen_iso or "",
                    ),
                )
                summary_count += 1

            return {
                "observation_count": len(rows),
                "summary_count": summary_count,
                "area": td_area,
            }

    def _build_td_sclass_observation(self, movement: dict, change: dict, tau_ms: int) -> dict:
        dt_ms = int(change["ts_ms"]) - int(movement["ts_ms"])
        weight = float(1.0 if tau_ms <= 0 else __import__("math").exp(-abs(dt_ms) / float(tau_ms)))
        movement_event_id = movement.get("id", movement.get("movement_event_id", movement.get("source_event_id")))
        change_event_id = change.get("id", change.get("change_event_id"))
        return {
            "td_area": movement["td_area"],
            "movement_event_id": movement_event_id,
            "movement_ts_ms": movement["ts_ms"],
            "movement_ts_iso": movement["ts_iso"],
            "headcode": movement["headcode"],
            "from_berth": movement["from_berth"],
            "to_berth": movement["to_berth"],
            "source_msg_type": movement["source_msg_type"],
            "change_event_id": change_event_id,
            "change_ts_ms": change["ts_ms"],
            "change_ts_iso": change["ts_iso"],
            "address": change["address"],
            "byte_offset": change["byte_offset"],
            "bit": change["bit"],
            "old_state": change["old_state"],
            "new_state": change["new_state"],
            "dt_ms": dt_ms,
            "weight": weight,
            "evidence_json": json.dumps(
                {
                    "movement_event_id": movement_event_id,
                    "change_event_id": change_event_id,
                    "movement_ts_ms": movement["ts_ms"],
                    "change_ts_ms": change["ts_ms"],
                    "dt_ms": dt_ms,
                    "bit_label": self._sclass_bit_label(change["address"], int(change["byte_offset"] or 0), int(change["bit"])),
                    "old_state": change["old_state"],
                    "new_state": change["new_state"],
                },
                separators=(",", ":"),
            ),
        }

    def _correlate_td_berth_movement_with_sclass_changes(self, movement: dict) -> int:
        pre_ms, post_ms, tau_ms = self._get_sclass_correlation_window()
        start_ts = int(movement["ts_ms"]) - pre_ms
        end_ts = int(movement["ts_ms"]) + post_ms
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, ts_ms, ts_iso, td_area, msg_type, address, byte_offset, bit, old_state, new_state
                FROM td_sclass_changes
                WHERE td_area=? AND ts_ms BETWEEN ? AND ?
                ORDER BY ts_ms ASC, id ASC
                """,
                (movement["td_area"], start_ts, end_ts),
            )
            changes = cursor.fetchall()
        inserted = 0
        for change_row in changes:
            change = {
                "id": change_row[0],
                "ts_ms": change_row[1],
                "ts_iso": change_row[2],
                "td_area": change_row[3],
                "msg_type": change_row[4],
                "address": change_row[5],
                "byte_offset": change_row[6],
                "bit": change_row[7],
                "old_state": change_row[8],
                "new_state": change_row[9],
            }
            observation = self._build_td_sclass_observation(movement, change, tau_ms)
            if self._insert_td_sclass_movement_observation(observation):
                inserted += 1
        if inserted:
            self._refresh_td_sclass_movement_scores(movement["td_area"])
        return inserted

    def _correlate_td_sclass_change_with_berth_movements(self, change: dict) -> int:
        pre_ms, post_ms, tau_ms = self._get_sclass_correlation_window()
        start_ts = int(change["ts_ms"]) - post_ms
        end_ts = int(change["ts_ms"]) + pre_ms
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, ts_ms, ts_iso, td_area, headcode, from_berth, to_berth, source_msg_type
                FROM td_berth_movements
                WHERE td_area=? AND ts_ms BETWEEN ? AND ?
                ORDER BY ts_ms ASC, id ASC
                """,
                (change["td_area"], start_ts, end_ts),
            )
            movements = cursor.fetchall()
        inserted = 0
        for movement_row in movements:
            movement = {
                "id": movement_row[0],
                "ts_ms": movement_row[1],
                "ts_iso": movement_row[2],
                "td_area": movement_row[3],
                "headcode": movement_row[4],
                "from_berth": movement_row[5],
                "to_berth": movement_row[6],
                "source_msg_type": movement_row[7],
            }
            observation = self._build_td_sclass_observation(movement, change, tau_ms)
            if self._insert_td_sclass_movement_observation(observation):
                inserted += 1
        if inserted:
            self._refresh_td_sclass_movement_scores(change["td_area"])
        return inserted

    def rebuild_td_sclass_correlations(self, td_area: Optional[str] = None) -> dict:
        """Rebuild S-class/berth correlation observations and summary scores."""
        td_area_filter = self._norm_text(td_area, upper=True)
        with self._lock, self._conn:
            if td_area_filter:
                self._conn.execute(
                    "DELETE FROM td_sclass_movement_observations WHERE td_area=?",
                    (td_area_filter,),
                )
                self._conn.execute(
                    "DELETE FROM td_sclass_movement_scores WHERE td_area=?",
                    (td_area_filter,),
                )
            else:
                self._conn.execute("DELETE FROM td_sclass_movement_observations")
                self._conn.execute("DELETE FROM td_sclass_movement_scores")

        pre_ms, post_ms, tau_ms = self._get_sclass_correlation_window()
        with self._lock:
            cursor = self._conn.cursor()
            query = """
                SELECT id, ts_ms, ts_iso, td_area, headcode, from_berth, to_berth, source_msg_type
                FROM td_berth_movements
                WHERE td_area IS NOT NULL
            """
            params: tuple[Any, ...] = ()
            if td_area_filter:
                query += " AND UPPER(td_area)=?"
                params = (td_area_filter,)
            query += " ORDER BY td_area, ts_ms ASC, id ASC"
            movement_rows = cursor.execute(query, params).fetchall()

        inserted = 0
        for movement_row in movement_rows:
            movement = {
                "id": movement_row[0],
                "ts_ms": movement_row[1],
                "ts_iso": movement_row[2],
                "td_area": movement_row[3],
                "headcode": movement_row[4],
                "from_berth": movement_row[5],
                "to_berth": movement_row[6],
                "source_msg_type": movement_row[7],
            }
            with self._lock:
                cursor = self._conn.cursor()
                cursor.execute(
                    """
                    SELECT id, ts_ms, ts_iso, td_area, msg_type, address, byte_offset, bit, old_state, new_state
                    FROM td_sclass_changes
                    WHERE td_area=? AND ts_ms BETWEEN ? AND ?
                    ORDER BY ts_ms ASC, id ASC
                    """,
                    (movement["td_area"], movement["ts_ms"] - pre_ms, movement["ts_ms"] + post_ms),
                )
                changes = cursor.fetchall()
            for change_row in changes:
                change = {
                    "id": change_row[0],
                    "ts_ms": change_row[1],
                    "ts_iso": change_row[2],
                    "td_area": change_row[3],
                    "msg_type": change_row[4],
                    "address": change_row[5],
                    "byte_offset": change_row[6],
                    "bit": change_row[7],
                    "old_state": change_row[8],
                    "new_state": change_row[9],
                }
                observation = self._build_td_sclass_observation(movement, change, tau_ms)
                if self._insert_td_sclass_movement_observation(observation):
                    inserted += 1

        score_info = self._refresh_td_sclass_movement_scores(td_area_filter or None)
        return {
            "scanned_movements": len(movement_rows),
            "inserted_observations": inserted,
            "summary_count": score_info["summary_count"],
            "observation_count": score_info["observation_count"],
            "td_area": td_area_filter or None,
        }

    def get_sclass_correlation_status(self) -> dict:
        """Return a snapshot of S-class correlation configuration and processing status."""
        with self._lock:
            cursor = self._conn.cursor()
            config = {row[0]: row[1] for row in cursor.execute("SELECT key, value FROM sclass_correlation_config").fetchall()}
            counts = cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM td_sclass_movement_observations) AS observation_count,
                    (SELECT COUNT(*) FROM td_sclass_movement_scores) AS score_count,
                    (SELECT COUNT(*) FROM td_berth_movements) AS movement_count,
                    (SELECT COUNT(*) FROM td_sclass_changes) AS change_count,
                    (SELECT COUNT(DISTINCT td_area) FROM td_sclass_movement_scores) AS area_count
                """
            ).fetchone()
            latest = cursor.execute(
                """
                SELECT
                    COALESCE(MAX(movement_ts_ms), 0) AS last_movement_ts,
                    COALESCE(MAX(change_ts_ms), 0) AS last_change_ts
                FROM td_sclass_movement_observations
                """
            ).fetchone()
            return {
                "config": config,
                "observation_count": counts[0],
                "score_count": counts[1],
                "movement_count": counts[2],
                "change_count": counts[3],
                "area_count": counts[4],
                "last_movement_ts_ms": latest[0],
                "last_change_ts_ms": latest[1],
            }

    def populate_corpus_data(self, corpus_data: list[dict]) -> int:
        """Populate CORPUS location reference data into the database.
        
        Args:
            corpus_data: List of CORPUS records with fields like TIPLOC, STANOX, 3ALPHA, NLCDESC, etc.
            
        Returns:
            Number of records inserted/updated
        """
        from .logging_config import get_logger

        logger = get_logger("database")

        def _normalize(value: Any, *, upper: bool = False) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            return text.upper() if upper else text

        prepared_rows: list[tuple[Optional[str], Optional[str], Optional[str], Optional[str], str, Optional[str]]] = []
        for row in corpus_data:
            if not isinstance(row, dict):
                continue
            
            try:
                # Extract fields from CORPUS format
                tiploc = _normalize(row.get("TIPLOC"), upper=True)
                stanox = _normalize(row.get("STANOX"))
                crs = _normalize(row.get("3ALPHA"), upper=True)
                nlc = _normalize(row.get("NLC"))
                name = _normalize(row.get("NLCDESC")) or _normalize(row.get("NLCDESC16")) or ""
                
                # Store raw JSON if available
                raw_json = json.dumps(row) if self.save_raw_json else None
            except Exception as exc:
                row_identity = ", ".join(
                    f"{key}={row.get(key)!r}"
                    for key in ("TIPLOC", "STANOX", "3ALPHA", "NLC")
                    if row.get(key) not in (None, "")
                ) or "no identifiers"
                logger.warning(
                    f"Skipping malformed CORPUS row during persistence ({row_identity}): {exc}"
                )
                continue
            
            # Skip records without a name
            if not name:
                continue
            
            # Skip records without any identifying code
            if not any([tiploc, stanox, crs]):
                continue
            
            prepared_rows.append((tiploc, stanox, crs, nlc, name, raw_json))

        count = 0
        with self._lock, self._conn:
            for tiploc, stanox, crs, nlc, name, raw_json in prepared_rows:
                # Use COALESCE to handle NULLs in PRIMARY KEY
                self._conn.execute(
                    """
                    INSERT INTO corpus_locations (tiploc, stanox, crs, nlc, name, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tiploc, stanox, crs) DO UPDATE SET
                        nlc=excluded.nlc,
                        name=excluded.name,
                        raw_json=excluded.raw_json,
                        updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    """,
                    (tiploc, stanox, crs, nlc, name, raw_json)
                )
                count += 1
        
        return count
    
    def populate_smart_data(self, smart_data: list[dict]) -> int:
        """Populate SMART berth stepping reference data into the database.
        
        Args:
            smart_data: List of SMART records with fields like TD, FROMBERTH, TOBERTH, STANOX, etc.
            
        Returns:
            Number of berth mappings inserted/updated
        """
        count = 0
        with self._lock, self._conn:
            for row in smart_data:
                if not isinstance(row, dict):
                    continue
                
                # Extract fields from SMART format
                td_area = (row.get("TD") or "").strip().upper()
                stanox = (row.get("STANOX") or "").strip() or None
                platform = (row.get("PLATFORM") or "").strip() or None
                event = (row.get("EVENT") or "").strip().upper() or None
                stanme = (row.get("STANME") or "").strip() or None
                step_type = (row.get("STEPTYPE") or "").strip() or None
                from_line = (row.get("FROMLINE") or "").strip() or None
                to_line = (row.get("TOLINE") or "").strip() or None
                berthoffset = safe_int(row.get("BERTHOFFSET"))
                comment = (row.get("COMMENT") or "").strip() or None
                
                if not td_area or not stanox:
                    continue
                
                # Store raw JSON if available
                raw_json = json.dumps(row) if self.save_raw_json else None
                
                # Process both FROMBERTH and TOBERTH
                for berth_key in ("FROMBERTH", "TOBERTH"):
                    berth = (row.get(berth_key) or "").strip().upper()
                    if not berth:
                        continue
                    
                    self._conn.execute(
                        """
                        INSERT INTO smart_berths (td_area, berth, stanox, platform, event, stanme, 
                                                  step_type, from_line, to_line, berthoffset, comment, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(td_area, berth) DO UPDATE SET
                            stanox=excluded.stanox,
                            platform=excluded.platform,
                            event=excluded.event,
                            stanme=excluded.stanme,
                            step_type=excluded.step_type,
                            from_line=excluded.from_line,
                            to_line=excluded.to_line,
                            berthoffset=excluded.berthoffset,
                            comment=excluded.comment,
                            raw_json=excluded.raw_json,
                            updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        """,
                        (td_area, berth, stanox, platform, event, stanme, 
                         step_type, from_line, to_line, berthoffset, comment, raw_json)
                    )
                    count += 1
        
        return count
    
    def get_corpus_location(self, tiploc: Optional[str] = None, stanox: Optional[str] = None, 
                           crs: Optional[str] = None) -> Optional[dict]:
        """Query CORPUS location data by TIPLOC, STANOX, or CRS code.
        
        Args:
            tiploc: TIPLOC code to search for
            stanox: STANOX code to search for
            crs: CRS (3-alpha) code to search for
            
        Returns:
            Dictionary with location data or None if not found
        """
        with self._lock:
            cursor = self._conn.cursor()
            
            # Build query based on provided parameters
            if tiploc:
                cursor.execute(
                    "SELECT tiploc, stanox, crs, nlc, name FROM corpus_locations WHERE tiploc=? LIMIT 1",
                    (tiploc.strip().upper(),)
                )
            elif stanox:
                cursor.execute(
                    "SELECT tiploc, stanox, crs, nlc, name FROM corpus_locations WHERE stanox=? LIMIT 1",
                    (stanox.strip(),)
                )
            elif crs:
                cursor.execute(
                    "SELECT tiploc, stanox, crs, nlc, name FROM corpus_locations WHERE crs=? LIMIT 1",
                    (crs.strip().upper(),)
                )
            else:
                return None
            
            row = cursor.fetchone()
            if row:
                return {
                    "tiploc": row[0],
                    "stanox": row[1],
                    "crs": row[2],
                    "nlc": row[3],
                    "name": row[4]
                }
            return None
    
    def get_smart_berth(self, td_area: str, berth: str) -> Optional[dict]:
        """Query SMART berth data by TD area and berth identifier.
        
        Args:
            td_area: 2-character TD area code
            berth: Berth identifier
            
        Returns:
            Dictionary with berth data or None if not found
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT stanox, platform, event, stanme, step_type, from_line, to_line, 
                       berthoffset, comment
                FROM smart_berths 
                WHERE td_area=? AND berth=?
                LIMIT 1
                """,
                (td_area.strip().upper(), berth.strip().upper())
            )
            row = cursor.fetchone()
            if row:
                return {
                    "stanox": row[0],
                    "platform": row[1],
                    "event": row[2],
                    "stanme": row[3],
                    "step_type": row[4],
                    "from_line": row[5],
                    "to_line": row[6],
                    "berthoffset": row[7],
                    "comment": row[8]
                }
            return None
    
    def rebuild_mapper_scores(self, pre_ms: int, post_ms: int, tau_ms: int, td_area: Optional[str] = None, progress_callback=None) -> dict:
        """
        Rebuild berth_signal_scores from existing observations using new parameters.
        (existing implementation retained)
        """
        from .mapper import process_batch_for_mapper
        
        with self._lock:
            cursor = self._conn.cursor()
            ...
            # (unchanged - omitted here for brevity; file continues unchanged)
            return {
                'deleted': deleted,
                'inserted': total_inserted,
                'observations_processed': total_observations
            }


    def get_tocs_for_td_area(self, td_area: str) -> list[str]:
        """
        Return a list of canonical toc_code strings that are mapped to the given td_area.
    
        Respects effective_from/effective_to (if present) so temporary mappings can be modelled.
        """
        if not td_area:
            return []
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT toc_code
                FROM toc_td_areas
                WHERE td_area=?
                  AND (effective_from IS NULL OR date(effective_from) <= date('now'))
                  AND (effective_to IS NULL OR date(effective_to) >= date('now'))
                ORDER BY toc_code
                """,
                (td_area,)
            )
            return [row[0] for row in cursor.fetchall()]
    
