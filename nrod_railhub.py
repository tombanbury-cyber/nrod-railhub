#!/usr/bin/env python3
"""
vstp_trust_td_human.py

Human-readable console view by combining Network Rail feeds:
- VSTP schedule feed (VSTP_ALL)
- TRUST Train Movements feed (TRAIN_MVT_ALL_TOC)
- TD feed (TD_ALL_SIG_AREA)

Key details (matches working `stomp` CLI example):
- Plain STOMP (no TLS) to publicdatafeeds.networkrail.co.uk:61618
- STOMP 1.1
- Send correct 'host' header (vhost) at CONNECT time
- Broker appears to be ActiveMQ Artemis

Install:
  pip install stomp.py

Run:
  python3 vstp_trust_td_human.py --user 'user' --password 'pass'
  python3 vstp_trust_td_human.py --user 'user' --password 'pass' --verbose
  python3 vstp_trust_td_human.py --user 'user' --password 'pass' --headcode 2C90
"""

from __future__ import annotations

import os
import pathlib
import urllib.request

import sqlite3
import threading
from flask import Flask, request
from urllib.error import URLError, HTTPError
import base64
import gzip
import io

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import stomp

from datetime import datetime

def local_hhmm() -> str:
    # Uses the machine's local timezone (UK if your box is UK configured)
    return datetime.now().astimezone().strftime("%H:%M")

def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


NR_HOST = "publicdatafeeds.networkrail.co.uk"
NR_PORT = 61618

TOPIC_VSTP = "/topic/VSTP_ALL"
TOPIC_TRUST = "/topic/TRAIN_MVT_ALL_TOC"
TOPIC_TD = "/topic/TD_ALL_SIG_AREA"

CORPUS_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS"
SMART_URL  = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=SMART"

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def ms_to_iso_utc(ms: Any) -> str:
    i = safe_int(ms)
    if i is None:
        return "?"
    return datetime.fromtimestamp(i / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def hhmmss_to_hhmm(x: str) -> str:
    s = (x or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        return f"{s[:2]}:{s[2:4]}"
    return ""


@dataclass
class VstpSchedule:
    uid: str = ""
    signalling_id: str = ""          # headcode / reporting number
    start_date: str = ""
    end_date: str = ""
    locations: List[Tuple[str, str, str]] = field(default_factory=list)
    # list of (tiploc, arr_hhmm, dep_hhmm)


@dataclass
class ItpsSchedule:
    """Lightweight planned schedule from the SCHEDULE (ITPS) extract."""

    uid: str = ""
    signalling_id: str = ""          # headcode / reporting number
    start_date: str = ""             # YYYY-MM-DD
    end_date: str = ""               # YYYY-MM-DD
    days_run: str = ""               # 7-char 0/1 string Mon..Sun (may be blank)
    stp_indicator: str = ""          # C/N/O/P etc.
    locations: List[Tuple[str, str, str]] = field(default_factory=list)
    # list of (tiploc, arr_hhmm, dep_hhmm)
    def summary(self) -> str:
        if not self.locations:
            return f"VSTP uid={self.uid} headcode={self.signalling_id} (no locations)"
        origin = self.locations[0][0]
        dest = self.locations[-1][0]
        dep = self.locations[0][2] or self.locations[0][1]
        arr = self.locations[-1][1] or self.locations[-1][2]
        when = self.start_date
        return f"{self.signalling_id or '?'}  {origin} {dep or ''}  →  {dest} {arr or ''}  ({when})"


@dataclass
class TrustState:
    train_id: str = ""               # TRUST train_id (unique per run)
    train_uid: str = ""              # CIF_train_uid if present
    toc_id: str = ""
    schedule_source: str = ""
    activated: bool = False
    cancelled: bool = False
    last_event_time: str = ""
    last_location: str = ""          # stanox-ish if present
    last_delay_min: Optional[int] = None


@dataclass
class TdState:
    descr: str = ""                  # headcode / reporting number
    area_id: str = ""
    from_berth: str = ""
    to_berth: str = ""
    last_time_utc: str = ""





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

class LocationResolver:
    """
    Loads Network Rail CORPUSExtract-style JSON and provides:
      - TIPLOC -> name
      - STANOX -> name
      - CRS (3-alpha) -> name

    CORPUS is documented as mapping STANOX/TIPLOC/NLC/UIC/CRS to location descriptions. :contentReference[oaicite:2]{index=2}
    """

    def __init__(self) -> None:
        self.tiploc_to_name: Dict[str, str] = {}
        self.stanox_to_name: Dict[str, str] = {}
        self.crs_to_name: Dict[str, str] = {}

    def load_or_download(
        self,
        username: str,
        password: str,
        cache_path: str,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        path = pathlib.Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if force or (not path.exists()):
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS: downloading to {path} ...")
            self._download_corpus(username, password, str(path))
        else:
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS: using cached file {path}")

        self._load_corpus_file(str(path), quiet=quiet)

    def _download_corpus(self, username: str, password: str, out_file: str) -> None:
        """
        Download CORPUS via Network Rail SupportingFileAuthenticate.

        Flow:
          1) GET CORPUS_URL with Basic Auth to publicdatafeeds.networkrail.co.uk
          2) Follow redirect(s) to AWS pre-signed URL(s) WITHOUT Authorization header
             (S3 rejects Authorization when using X-Amz-* pre-signed query auth)
          3) Transparently decompress gzip if needed
          4) Save decompressed JSON to out_file
        """
        import base64
        import gzip
        import io
        import os
        import urllib.request
        import urllib.parse
        from urllib.error import URLError, HTTPError

        def is_presigned_aws(url: str) -> bool:
            q = urllib.parse.urlparse(url).query
            return "X-Amz-Algorithm=" in q or "X-Amz-Signature=" in q

        def host_of(url: str) -> str:
            return (urllib.parse.urlparse(url).hostname or "").lower()

        nr_host = "publicdatafeeds.networkrail.co.uk"
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        url = CORPUS_URL
        raw = b""
        encoding = ""
        ctype = ""

        for hop in range(0, 8):  # follow up to 8 redirects
            h = host_of(url)
            use_auth = (h == nr_host) and (not is_presigned_aws(url))

            headers = {
                "User-Agent": "vstp_trust_td_human/1.0 (+python urllib)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
            }
            if use_auth:
                headers["Authorization"] = f"Basic {auth}"

            req = urllib.request.Request(url, headers=headers)
            try:
                # Prevent urllib from auto-following redirects so we can control headers per-hop
                opener = urllib.request.build_opener(NoRedirect())
                resp = opener.open(req, timeout=60)

                # Success (2xx)
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower().strip()
                ctype = (resp.headers.get("Content-Type") or "").lower().strip()
                break

            except HTTPError as e:
                # Redirects come as HTTPError when NoRedirect is used
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location") or e.headers.get("location")
                    if not loc:
                        raise RuntimeError(f"CORPUS redirect ({e.code}) without Location header") from e
                    # Handle relative redirects
                    url = urllib.parse.urljoin(url, loc)
                    continue

                # Non-redirect error
                snippet = ""
                try:
                    snippet = e.read(500).decode("utf-8", errors="replace")
                except Exception as e:
                    try:
                        self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                        if self._db_err_count <= 5:
                            print(f"[{utc_now_iso()}] DB: persist failed: {type(e).__name__}: {e}")
                    except Exception:
                        pass
                raise RuntimeError(f"CORPUS download HTTP error: {e.code} {e.reason} body={snippet!r}") from e

            except URLError as e:
                raise RuntimeError(f"CORPUS download failed: {e}") from e
        else:
            raise RuntimeError("CORPUS download failed: too many redirects")

        # gzip detection: header OR magic bytes OR content-type hint
        is_gzip = encoding == "gzip" or raw[:2] == b"\x1f\x8b" or "gzip" in ctype
        if is_gzip:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gf:
                    raw = gf.read()

        tmp = out_file + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, out_file)


    def _load_corpus_file(self, filename: str, quiet: bool = False) -> None:
        with open(filename, "rb") as f:
            raw = f.read()

        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            raise RuntimeError(f"CORPUS parse failed (not valid JSON?): {e}") from e

        # CORPUS can be either:
        #   - a list of dict rows
        #   - a wrapper dict, commonly {"TIPLOCDATA": [ ... ]}
        rows: Optional[List[Any]] = None

        if isinstance(payload, list):
            rows = payload
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS format: list")
        elif isinstance(payload, dict):
            if not quiet:
                print(f"[{utc_now_iso()}] CORPUS format: dict wrapper keys={list(payload.keys())}")

            # Common wrapper keys
            for key in ("TIPLOCDATA", "tiplocdata", "locations", "data"):
                v = payload.get(key)
                if isinstance(v, list):
                    rows = v
                    break

            # Single-key dict whose value is the list
            if rows is None and len(payload) == 1:
                v = next(iter(payload.values()))
                if isinstance(v, list):
                    rows = v

        if rows is None:
            raise RuntimeError(
                f"CORPUS unexpected format: expected list or wrapper dict containing a list; "
                f"got {type(payload)}"
            )

        tiploc: Dict[str, str] = {}
        stanox: Dict[str, str] = {}
        crs: Dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue

            # Fields present in CORPUSExtract rows often include:
            # TIPLOC, STANOX, 3ALPHA, NLCDESC (sometimes NLCDESC16)
            name = (row.get("NLCDESC") or row.get("NLCDESC16") or "").strip()
            if not name:
                continue

            tip = (row.get("TIPLOC") or "").strip().upper()
            stx = (row.get("STANOX") or "").strip()
            three = (row.get("3ALPHA") or "").strip().upper()

            if tip and tip not in tiploc:
                tiploc[tip] = name
            if stx and stx not in stanox:
                stanox[stx] = name
            if three and three not in crs:
                crs[three] = name

        self.tiploc_to_name = tiploc
        self.stanox_to_name = stanox
        self.crs_to_name = crs

        if not quiet:
            print(
                f"[{utc_now_iso()}] CORPUS loaded: "
                f"{len(tiploc)} TIPLOC, {len(stanox)} STANOX, {len(crs)} CRS mappings"
            )



    def name_for_tiploc(self, code: str) -> str:
        return self.tiploc_to_name.get((code or "").strip().upper(), "")

    def name_for_stanox(self, code: str) -> str:
        return self.stanox_to_name.get((code or "").strip(), "")

    def name_for_crs(self, code: str) -> str:
        return self.crs_to_name.get((code or "").strip().upper(), "")

class SmartResolver:
    """Loads SMART berth stepping reference data and provides TD+berth -> STANOX/platform/name."""

    def __init__(self) -> None:
        # Keyed by (td_area, berth) e.g. ("AD","0152") -> dict with stanox/platform/stanme
        self.berth_map: Dict[Tuple[str, str], Dict[str, str]] = {}

    def load_or_download(
        self,
        username: str,
        password: str,
        cache_path: str,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        path = pathlib.Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if force or (not path.exists()):
            if not quiet:
                print(f"[{utc_now_iso()}] SMART: downloading to {path} ...")
            # Re-use LocationResolver's downloader (same auth + redirect rules)
            lr = LocationResolver()
            lr._download_corpus(username, password, str(path).replace("CORPUS", "SMART"))  # legacy fallback
            # The above line isn't reliable if path doesn't contain CORPUS; do it properly:
            self._download_smart(username, password, str(path))
        else:
            if not quiet:
                print(f"[{utc_now_iso()}] SMART: using cached file {path}")

        self._load_smart_file(str(path), quiet=quiet)

    def _download_smart(self, username: str, password: str, out_file: str) -> None:
        """Download SMART via SupportingFileAuthenticate (same redirect/auth rules as CORPUS)."""
        import urllib.parse

        def is_presigned_aws(url: str) -> bool:
            q = urllib.parse.urlparse(url).query
            return "X-Amz-Algorithm=" in q or "X-Amz-Signature=" in q

        def host_of(url: str) -> str:
            return (urllib.parse.urlparse(url).hostname or "").lower()

        nr_host = "publicdatafeeds.networkrail.co.uk"
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        url = SMART_URL
        raw = b""
        encoding = ""
        ctype = ""

        for hop in range(0, 8):
            h = host_of(url)
            use_auth = (h == nr_host) and (not is_presigned_aws(url))

            headers = {
                "User-Agent": "vstp_trust_td_human/1.1 (+python urllib)",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
            }
            if use_auth:
                headers["Authorization"] = f"Basic {auth}"

            req = urllib.request.Request(url, headers=headers)
            try:
                opener = urllib.request.build_opener(NoRedirect())
                resp = opener.open(req, timeout=60)
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower().strip()
                ctype = (resp.headers.get("Content-Type") or "").lower().strip()
                break
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location") or e.headers.get("location")
                    if not loc:
                        raise RuntimeError(f"SMART redirect ({e.code}) without Location header") from e
                    url = urllib.parse.urljoin(url, loc)
                    continue
                snippet = ""
                try:
                    snippet = e.read(500).decode("utf-8", errors="replace")
                except Exception as e:
                    try:
                        self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                        if self._db_err_count <= 5:
                            print(f"[{utc_now_iso()}] DB: persist failed: {type(e).__name__}: {e}")
                    except Exception:
                        pass
                raise RuntimeError(f"SMART download HTTP error: {e.code} {e.reason} body={snippet!r}") from e
            except URLError as e:
                raise RuntimeError(f"SMART download failed: {e}") from e
        else:
            raise RuntimeError("SMART download failed: too many redirects")

        is_gzip = encoding == "gzip" or raw[:2] == b"\x1f\x8b" or "gzip" in ctype
        if is_gzip:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gf:
                    raw = gf.read()

        tmp = out_file + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, out_file)

    def _load_smart_file(self, filename: str, quiet: bool = False) -> None:
        with open(filename, "rb") as f:
            raw = f.read()

        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            raise RuntimeError(f"SMART parse failed (not valid JSON?): {e}") from e

        rows: Optional[List[Any]] = None
        if isinstance(payload, list):
            rows = payload
            if not quiet:
                print(f"[{utc_now_iso()}] SMART format: list")
        elif isinstance(payload, dict):
            if not quiet:
                print(f"[{utc_now_iso()}] SMART format: dict wrapper keys={list(payload.keys())}")
            for key in ("SMARTDATA", "smartdata", "data", "rows", "SMART"):
                v = payload.get(key)
                if isinstance(v, list):
                    rows = v
                    break
            if rows is None and len(payload) == 1:
                v = next(iter(payload.values()))
                if isinstance(v, list):
                    rows = v

        if rows is None:
            raise RuntimeError(
                f"SMART unexpected format: expected list or wrapper dict containing a list; got {type(payload)}"
            )

        mp: Dict[Tuple[str, str], Dict[str, str]] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            td = (row.get("TD") or "").strip().upper()
            stanox = (row.get("STANOX") or "").strip()
            if not td or not stanox:
                continue

            platform = (row.get("PLATFORM") or "").strip()
            stanme = (row.get("STANME") or "").strip()
            event = (row.get("EVENT") or "").strip().upper()

            for berth_key in ("TOBERTH", "FROMBERTH"):
                berth = (row.get(berth_key) or "").strip().upper()
                if not berth:
                    continue
                k = (td, berth)
                if k not in mp:
                    mp[k] = {
                        "stanox": stanox,
                        "platform": platform,
                        "stanme": stanme,
                        "event": event,
                    }

        self.berth_map = mp
        if not quiet:
            print(f"[{utc_now_iso()}] SMART loaded: {len(self.berth_map)} berth mappings")

    def lookup(self, td_area: str, berth: str) -> Optional[Dict[str, str]]:
        k = ((td_area or "").strip().upper(), (berth or "").strip().upper())
        return self.berth_map.get(k)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib auto-following redirects so we can manage headers safely."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ScheduleResolver:
    """Downloads and reads the daily SCHEDULE (ITPS) extract (JSON-in-gzip).

    This is the feed that contains the planned timetable, as distinct from VSTP
    (late-notice changes). The JSON file is line-oriented: one JSON record per line.
    """

    AUTH_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate"

    def __init__(self) -> None:
        pass

    def download(
        self,
        username: str,
        password: str,
        out_gz: str,
        schedule_type: str = "CIF_ALL_FULL_DAILY",
        day: str = "toc-full",
        quiet: bool = False,
    ) -> None:
        """Download the schedule gzip to out_gz (kept compressed)."""
        import base64
        import os
        import urllib.request
        import urllib.parse
        import urllib.error

        url = f"{self.AUTH_URL}?type={urllib.parse.quote(schedule_type)}&day={urllib.parse.quote(day)}"

        # Basic Auth for the authenticate endpoint.
        # NOTE: urllib will otherwise *auto-follow* 302 redirects and (depending on version)
        # can carry over headers like Authorization to the S3 URL, which can result in 400.
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[override]
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {token}",
                "User-Agent": "nrod-schedule-client/1",
            },
        )

        try:
            resp = opener.open(req, timeout=60)
            # If no redirect happened (unexpected but possible), just read it.
            data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            loc = e.headers.get("Location") or e.headers.get("location")
            if not loc:
                raise RuntimeError("SCHEDULE: redirect without Location header")
            if not quiet:
                print(f"[{utc_now_iso()}] SCHEDULE: redirect to {loc[:90]}...")
            # Follow redirect to pre-signed S3 URL without Authorization header.
            req2 = urllib.request.Request(loc, headers={"User-Agent": "nrod-schedule-client/1"})
            with urllib.request.urlopen(req2, timeout=180) as resp2:
                data = resp2.read()

        os.makedirs(os.path.dirname(out_gz) or ".", exist_ok=True)
        tmp = out_gz + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, out_gz)


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

    def render_for_headcode(self, headcode: str, td_area: str | None = None, *, width: int = 120) -> str:
        parts: List[str] = [f"[{utc_now_iso()}] {headcode}"]

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


    def render_for_td(self, td_area: str, headcode: str, *, width: int = 120) -> str:
        """Render a single-line, human-friendly view for a TD train (area+headcode)."""
        # Timetable enrichment (prefer VSTP, else ITPS SCHEDULE)
        dep = arr = None
        origin = dest = None

        vs = self.vstp_by_headcode.get(headcode)
        if vs and vs.locations:
            o_code = vs.locations[0][0]
            d_code = vs.locations[-1][0]
            dep = vs.locations[0][2] or vs.locations[0][1]
            arr = vs.locations[-1][1] or vs.locations[-1][2]
            if self.resolver:
                origin = self.resolver.name_for_tiploc(o_code) or o_code
                dest = self.resolver.name_for_tiploc(d_code) or d_code
            else:
                origin, dest = o_code, d_code
        else:
            itps = self.sched_by_headcode.get(headcode)
            if itps and itps.locations:
                o = itps.locations[0]
                d = itps.locations[-1]
                # itps.locations may be tuples like (tiploc, arr, dep) or objects with attributes
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

# --- Compatibility shim ---
if not hasattr(HumanView, "_render_trust"):
    def _render_trust(self, trust):
        return ""
    HumanView._render_trust = _render_trust
        


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

# --- Compatibility shim: ensure HumanView has get_timetable_fields() ---
# In some builds this helper was defined at module level; bind it onto the class.
if 'HumanView' in globals() and 'get_timetable_fields' in globals():
    if not hasattr(HumanView, 'get_timetable_fields'):
        HumanView.get_timetable_fields = get_timetable_fields




class Listener(stomp.ConnectionListener):
    def __init__(self, hv: HumanView, args: argparse.Namespace, db: Optional[RailDB] = None) -> None:
        self.hv = hv
        self.args = args
        self.db = db

        self.connected_at: Optional[str] = None
        self.last_message_at: Optional[str] = None
        self.msg_count_total = 0
        self.msg_count_by_dest = defaultdict(int)
        # Per-headcode de-dup state for human output
        self._last_output: Dict[tuple[str,str], str] = {}
        self._last_output_ts: Dict[tuple[str,str], float] = {}
        self._print_lock = threading.Lock()




    def _print_train_update(self, td_area: str | None, headcode: str | None = None) -> bool:
        """
        Render + print an updated view of a train.

        Supports both call styles:
          * _print_train_update(td_area, headcode)  (TD)
          * _print_train_update(headcode)           (VSTP/TRUST convenience)
        """
        # Back-compat: called as _print_train_update(headcode)
        if headcode is None:
            headcode = td_area  # type: ignore[assignment]
            td_area = None

        if not headcode:
            return False

        # Choose renderer based on whether we have a TD area context.
        if td_area:
            text = self.hv.render_for_td(td_area, str(headcode), width=self.args.width)
        else:
            text = self.hv.render_for_headcode(str(headcode), width=self.args.width)

        if not text:
            return False

        key = (td_area or "?", str(headcode))

        now = time.time()
        with self._print_lock:
            last = self._last_output.get(key)
            last_ts = self._last_output_ts.get(key, 0.0)

            # De-dupe identical rendered output unless repeat-after has elapsed
            if self.args.only_changes and last == text and (now - last_ts) < float(self.args.repeat_after):
                return False

            self._last_output[key] = text
            self._last_output_ts[key] = now

        print(text)
        return True
    def on_connecting(self, host_and_port):
        try:
            h, p = host_and_port
        except Exception:
            h, p = "?", "?"
        print(f"[{utc_now_iso()}] Connecting TCP to {h}:{p} ...")

    def on_connected(self, frame) -> None:
        self.connected_at = utc_now_iso()
        session = "?"
        server = "?"
        version = "?"
        try:
            session = frame.headers.get("session", "?")
            server = frame.headers.get("server", "?")
            version = frame.headers.get("version", "?")
        except Exception:
            pass
        print(f"[{self.connected_at}] CONNECTED. version={version} session={session} server={server}")

    def on_disconnected(self) -> None:
        print(f"[{utc_now_iso()}] Disconnected.", file=sys.stderr)

    def on_error(self, frame) -> None:
        body = getattr(frame, "body", "")
        hdrs = getattr(frame, "headers", {})
        print(f"[{utc_now_iso()}] STOMP ERROR headers={hdrs} body={body}", file=sys.stderr)

    def on_message(self, frame) -> None:
        self.last_message_at = utc_now_iso()
        self.msg_count_total += 1

        # Track per-topic counts
        dest = ""
        try:
            dest = frame.headers.get("destination", "")
        except Exception:
            pass
        if dest:
            self.msg_count_by_dest[dest] += 1

        # Optional raw preview
        if self.args.verbose:
            short = (frame.body[:200] + "…") if frame.body and len(frame.body) > 200 else (frame.body or "")
            print(f"[{self.last_message_at}] RX {dest or '?'} ({len(frame.body or '')} bytes): {short}")

        if not frame.body:
            return

        try:
            payload = json.loads(frame.body)
        except Exception:
            if self.args.verbose:
                print(f"[{utc_now_iso()}] Non-JSON message ignored")
            return

        items = payload if isinstance(payload, list) else [payload]
        printed = False

        for item in items:
            if not isinstance(item, dict):
                continue

            # ------------------------------------------------------------
            # VSTP
            # ------------------------------------------------------------
            if "VSTPCIFMsgV1" in item:
                vs = self.hv.upsert_vstp(item)
                if not vs:
                    continue

                if self.args.trace_headcode:
                    if self.args.headcode and vs.signalling_id == self.args.headcode:
                        print(f"[{utc_now_iso()}] TRACE VSTP headcode={vs.signalling_id} uid={vs.uid} start={vs.start_date}")
                    if self.args.uid and vs.uid == self.args.uid:
                        print(f"[{utc_now_iso()}] TRACE VSTP uid={vs.uid} headcode={vs.signalling_id}")

                if self._matches(vs.signalling_id, vs.uid):
                    if self._print_train_update(vs.signalling_id):
                        printed = True

            # ------------------------------------------------------------
            # TRUST
            # ------------------------------------------------------------
            elif "header" in item and "body" in item:
                ts = self.hv.upsert_trust(item)
                if not ts:
                    continue

                body = item.get("body", {})
                trust_headcode = (
                    body.get("train_reporting_number")
                    or body.get("reporting_number")
                    or ""
                ).strip()

                # Trace TRUST visibility
                if self.args.trace_headcode:
                    if self.args.uid and ts.train_uid == self.args.uid:
                        print(f"[{utc_now_iso()}] TRACE TRUST uid={ts.train_uid} train_id={ts.train_id} time={ts.last_event_time}")
                    if self.args.headcode and (
                        trust_headcode == self.args.headcode
                        or self.hv.trust_by_headcode.get(self.args.headcode) is ts
                    ):
                        print(f"[{utc_now_iso()}] TRACE TRUST headcode={self.args.headcode} train_id={ts.train_id} uid={ts.train_uid}")

                # Decide whether to print
                hc_to_print = None

                if self.args.headcode:
                    if trust_headcode == self.args.headcode:
                        hc_to_print = self.args.headcode
                    elif ts.train_uid and self.hv.headcode_by_uid.get(ts.train_uid) == self.args.headcode:
                        hc_to_print = self.args.headcode

                elif self.args.uid and ts.train_uid == self.args.uid:
                    hc_to_print = self.hv.headcode_by_uid.get(ts.train_uid)

                elif not self.args.headcode and not self.args.uid:
                    # Unfiltered mode: print everything
                    if trust_headcode:
                        hc_to_print = trust_headcode

                if hc_to_print:
                    if self._print_train_update(hc_to_print):
                        printed = True

            # ------------------------------------------------------------
            # TD
            # ------------------------------------------------------------
            # ------------------------------------------------------------
            # TD (wrapped as CA_MSG/CC_MSG/SF_MSG/etc)
            # ------------------------------------------------------------
            td_msg = self._unwrap_td_item(item)
            if td_msg and "msg_type" in td_msg and ("descr" in td_msg or "to" in td_msg or "from" in td_msg):
                td = self.hv.upsert_td(td_msg)
                if not td:
                    continue

                if self.args.trace_headcode and self.args.headcode and td.descr == self.args.headcode:
                    print(
                        f"[{utc_now_iso()}] TRACE TD headcode={td.descr} "
                        f"area={td.area_id} {td.from_berth}->{td.to_berth} "
                        f"time={td.last_time_utc}"
                    )

                if self.args.headcode and td.descr != self.args.headcode:
                    continue

                if self.args.td_area and td.area_id and td.area_id not in self.args.td_area:
                    continue

                # Persist TD
                if self.db:
                    try:
                        if not (td.area_id and td.descr):
                            raise ValueError('missing td_area/headcode')
                        self.db.insert_td_event(td.last_time_utc or utc_now_iso(), td.area_id, td.descr, 'td', td.from_berth, td.to_berth, td_msg)
                        # Enrich via HumanView render context
                        loc = self.hv.decode_last_location(td.area_id, td.descr)
                        tten = self.hv.get_timetable_fields(td.descr)
                        self.db.upsert_td_state(td.area_id, td.descr, td.last_time_utc or utc_now_iso(), td.from_berth, td.to_berth,
                                                stanox=loc.get('stanox'), location_name=loc.get('name'), platform=loc.get('platform'),
                                                sched_dep=tten.get('dep'), sched_arr=tten.get('arr'), origin_name=tten.get('origin'), dest_name=tten.get('dest'), uid=tten.get('uid'))
                    except Exception as e:
                        # Don't kill the receiver thread; log a few DB errors for diagnosis
                        try:
                            self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                            if self._db_err_count <= 5:
                                print(f"[{utc_now_iso()}] DB: TD persist failed: {type(e).__name__}: {e}")
                        except Exception:
                            pass

                if self._print_train_update(td.area_id or '?', td.descr):
                    printed = True
                continue


        # ------------------------------------------------------------
        # If filtered and nothing printed yet, but TD exists — show state
        # ------------------------------------------------------------
        if not printed and self.args.headcode and self.args.headcode in self.hv.td_by_headcode:
            self._print_train_update(self.args.headcode)



    def _matches(self, headcode: str, uid: str) -> bool:
        if self.args.headcode and headcode and headcode == self.args.headcode:
            return True
        if self.args.uid and uid and uid.strip() == self.args.uid.strip():
            return True
        return not self.args.headcode and not self.args.uid

        
    def _unwrap_td_item(self, item: dict) -> Optional[dict]:
        """
        TD feed often wraps messages as {"CA_MSG": {...}} / {"CC_MSG": {...}} / {"SF_MSG": {...}} etc.
        Return the inner dict if found, else None.
        """
        if not isinstance(item, dict):
            return None
        if "msg_type" in item:
            return item  # already unwrapped

        # Common wrappers end in _MSG
        if len(item) == 1:
            k, v = next(iter(item.items()))
            if isinstance(k, str) and k.endswith("_MSG") and isinstance(v, dict):
                return v

        # Fallback: scan keys
        for k, v in item.items():
            if isinstance(k, str) and k.endswith("_MSG") and isinstance(v, dict) and "msg_type" in v:
                return v

        return None



def start_status_ticker(listener: Listener, interval: int = 15) -> threading.Thread:
    def loop():
        while True:
            time.sleep(interval)
            ca = listener.connected_at or "not-connected-yet"
            lm = listener.last_message_at or "none-yet"
            by_dest = ", ".join(
                f"{k.replace('/topic/', '')}={v}" for k, v in sorted(listener.msg_count_by_dest.items())
            ) or "no-messages"
            print(f"[{utc_now_iso()}] STATUS connected_at={ca} last_msg={lm} total={listener.msg_count_total} [{by_dest}]")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def connect_and_run(args: argparse.Namespace) -> None:

    resolver = LocationResolver()
    resolver.load_or_download(
        username=args.user,
        password=args.password,
        cache_path=args.corpus_cache,
        force=args.corpus_refresh,
        quiet=False,
    )

    smart = SmartResolver()
    smart.load_or_download(
        username=args.user,
        password=args.password,
        cache_path=args.smart_cache,
        force=args.smart_refresh,
        quiet=False,
    )
    hv = HumanView(resolver=resolver, smart=smart)

    # Optional: load planned timetable (SCHEDULE feed) so we can fill ?? fields.
    #
    # Important: the daily schedule file can be large; we load it in a background thread
    # so TD/TRUST streaming starts immediately.
    if getattr(args, "use_schedule", True):
        import threading

        def _schedule_worker() -> None:
            try:
                sched_path = pathlib.Path(args.schedule_cache).expanduser()
                sched_path.parent.mkdir(parents=True, exist_ok=True)

                if args.schedule_refresh or (not sched_path.exists()):
                    print(f"[{utc_now_iso()}] SCHEDULE: downloading to {sched_path} ...")
                    ScheduleResolver().download(
                        username=args.user,
                        password=args.password,
                        out_gz=str(sched_path),
                        schedule_type=args.schedule_type,
                        day=args.schedule_day,
                        quiet=False,
                    )
                else:
                    print(f"[{utc_now_iso()}] SCHEDULE: using cached file {sched_path}")

                hv.load_schedule_gz(
                    str(sched_path),
                    service_date=datetime.now(timezone.utc).date().isoformat(),
                    headcode_filter=args.headcode,
                    uid_filter=args.uid,
                    quiet=False,
                )
                print(f"[{utc_now_iso()}] SCHEDULE: loaded (timetable enrichment enabled)")
            except Exception as e:
                print(f"[{utc_now_iso()}] SCHEDULE: failed to load ({e}); continuing without timetable enrichment")

        threading.Thread(target=_schedule_worker, daemon=True).start()
    print(f"[{utc_now_iso()}] Starting. stomp.py version={getattr(stomp, '__version__', '?')}")
    print(f"[{utc_now_iso()}] Broker: {args.host}:{args.port}  (plain STOMP)  vhost={args.vhost}")

    conn = stomp.Connection11(
        host_and_ports=[(args.host, args.port)],
        keepalive=True,
        heartbeats=(10000, 10000),
        reconnect_attempts_max=5,
        vhost=args.vhost,
    )

    db_path = str(pathlib.Path(args.db_path).expanduser()) if args.db_path else None
    db = RailDB(db_path) if db_path else None
    listener = Listener(hv, args, db=db)
    if args.web_port and db_path:
        t = threading.Thread(target=start_web_dashboard, args=(db_path, args.web_port), daemon=True)
        t.start()
        print(f"[{utc_now_iso()}] WEB: dashboard on http://0.0.0.0:{args.web_port} using {db_path}")
    conn.set_listener("", listener)

    print(f"[{utc_now_iso()}] Connecting (wait=True) ...")
    try:
        # Artemis is picky about host/vhost; set it explicitly like the CLI does.
        conn.connect(
            login=args.user,
            passcode=args.password,
            wait=True,
            headers={"host": args.vhost},
        )
    except Exception as e:
        print(f"[{utc_now_iso()}] CONNECT FAILED 2: {type(e).__name__}: {e!r}", file=sys.stderr)
        return

    print(f"[{utc_now_iso()}] Subscribing to topics...")
    conn.subscribe(destination=TOPIC_VSTP, id="vstp", ack="auto")
    print(f"[{utc_now_iso()}]   subscribed {TOPIC_VSTP}")
    conn.subscribe(destination=TOPIC_TRUST, id="trust", ack="auto")
    print(f"[{utc_now_iso()}]   subscribed {TOPIC_TRUST}")
    conn.subscribe(destination=TOPIC_TD, id="td", ack="auto")
    print(f"[{utc_now_iso()}]   subscribed {TOPIC_TD}")

    if args.headcode:
        print(f"[{utc_now_iso()}] Filter: headcode={args.headcode}")
    if args.uid:
        print(f"[{utc_now_iso()}] Filter: uid={args.uid}")

    start_status_ticker(listener, interval=args.status_every)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[{utc_now_iso()}] Exiting...")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Combine VSTP + TRUST + TD into a human-readable stream.")
    p.add_argument("--host", default=NR_HOST, help="STOMP host (default: publicdatafeeds.networkrail.co.uk)")
    p.add_argument("--port", type=int, default=NR_PORT, help="STOMP port (default: 61618)")
    p.add_argument("--vhost", default=NR_HOST, help="STOMP vhost/host header (default: publicdatafeeds.networkrail.co.uk)")

    p.add_argument("--user", required=True, help="Network Rail Data Feeds username/email")
    p.add_argument("--password", required=True, help="Network Rail Data Feeds password")

    p.add_argument("--headcode", help="Filter output to a single headcode (e.g. 2C90)")
    p.add_argument("--uid", help="Filter output to a single CIF_train_uid (e.g. 43876)")

    p.add_argument(
        "--td-area",
        action="append",
        default=[],
        dest="td_area",
        help="Only show console output for these TD area IDs (repeatable, e.g. --td-area EK). Default: show all areas.",
    )


    p.add_argument("--verbose", action="store_true", help="Print short trace of every raw message received")
    p.add_argument("--status-every", dest="status_every", type=int, default=15,
                   help="Print status line every N seconds (default 15)")             

    p.add_argument(
        "--corpus-cache",
        default="~/.cache/openraildata/CORPUSExtract.json",
        help="Path to cached CORPUS JSON (default: ~/.cache/openraildata/CORPUSExtract.json)",
    )

    p.add_argument(
        "--corpus-refresh",
        action="store_true",
        help="Force re-download of CORPUS even if cache exists",
    )

    p.add_argument(
        "--smart-cache",
        default="~/.cache/openraildata/SMART.json",
        help="Path to cached SMART JSON (default: ~/.cache/openraildata/SMART.json)",
    )
    p.add_argument(
        "--smart-refresh",
        action="store_true",
        help="Force re-download of SMART even if cache exists",
    )


    p.add_argument(
        "--schedule-cache",
        default="~/.cache/openraildata/SCHEDULE_toc-full.json.gz",
        help="Path to cached SCHEDULE gzip (default: ~/.cache/openraildata/SCHEDULE_toc-full.json.gz)",
    )
    p.add_argument(
        "--schedule-refresh",
        action="store_true",
        help="Force re-download of the SCHEDULE extract even if cache exists",
    )
    p.add_argument(
        "--schedule-type",
        default="CIF_ALL_FULL_DAILY",
        help="SCHEDULE extract type (default CIF_ALL_FULL_DAILY)",
    )
    p.add_argument(
        "--schedule-day",
        default="toc-full",
        help="SCHEDULE extract day selector (default toc-full)",
    )
    p.add_argument(
        "--no-schedule",
        dest="use_schedule",
        action="store_false",
        help="Disable loading the SCHEDULE extract (timetable enrichment)",
    )
    p.set_defaults(use_schedule=True)
    p.set_defaults(only_changes=True)
    p.add_argument("--no-only-changes", dest="only_changes", action="store_false",
                   help="Print even when a headcode's rendered output has not changed")
    p.add_argument("--repeat-after", type=int, default=300,
                   help="Allow repeating identical output after N seconds (default: 300)")

    p.add_argument("--pretty", action="store_true", help="Pretty departure-board output (default)")
    p.add_argument("--raw", action="store_true", help="Use raw/debug output instead of pretty")
    p.add_argument("--width", type=int, default=96, help="Pretty output width (default 96)")

    p.add_argument("--trace-headcode", action="store_true",
               help="Extra debug: show when VSTP/TRUST/TD mention the filtered headcode/uid")
    p.add_argument("--db-path", default=None,
                   help="SQLite database path for state/event storage (enables DB output)")
    p.add_argument("--web-port", type=int, default=0,
                   help="If set and --db-path is provided, start tiny web dashboard on this port")
    return p.parse_args()


def start_web_dashboard(db_path: str, port: int) -> None:
    app = Flask(__name__)
    db_path = str(pathlib.Path(db_path).expanduser())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000;")

    def q(sql: str, params=()):
        cur = conn.execute(sql, params)
        return cur.fetchall()

    @app.get("/")
    def index():

        counts = q("SELECT (SELECT COUNT(*) FROM td_state) AS td_state, (SELECT COUNT(*) FROM td_event) AS td_event, (SELECT COUNT(*) FROM trust_state) AS trust_state, (SELECT COUNT(*) FROM vstp_state) AS vstp_state")[0]

        area = request.args.get("area", "").strip()
        if area:
            rows = q("SELECT * FROM td_state WHERE td_area=? ORDER BY last_time_utc DESC LIMIT 200", (area,))
        else:
            rows = q("SELECT * FROM td_state ORDER BY last_time_utc DESC LIMIT 200")
        areas = [r[0] for r in q("SELECT DISTINCT td_area FROM td_state ORDER BY td_area")]
        html = ["<html><head><meta charset='utf-8'><title>NR RailHub</title>"
                "<style>body{font-family:system-ui,Arial;margin:20px} table{border-collapse:collapse;width:100%}"
                "th,td{border-bottom:1px solid #ddd;padding:6px 8px;font-size:14px} th{text-align:left}"
                ".pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-right:6px}</style>"
                "</head><body>"]
        html.append("<h2>NR RailHub</h2>")
        html.append(f"<p><b>DB:</b> td_state={counts['td_state']} td_event={counts['td_event']} trust_state={counts['trust_state']} vstp_state={counts['vstp_state']}</p>")
        html.append("<div>Filter: " + " ".join([f"<a class='pill' href='/?area={a}'>{a}</a>" for a in areas]) + " <a class='pill' href='/'>ALL</a></div>")
        html.append("<h3>Latest TD state" + (f" (area {area})" if area else "") + "</h3>")        
        html.append("<table><tr><th>Area</th><th>Headcode</th><th>Time</th><th>From</th><th>To</th><th>Location</th><th>Plat</th><th>Sched</th></tr>")
        for r in rows:
            sched = ""
            if r["sched_dep"] or r["sched_arr"]:
                sched = f"{r['sched_dep'] or ''}→{r['sched_arr'] or ''} {r['origin_name'] or ''}→{r['dest_name'] or ''}"
            loc = r["location_name"] or ""
            if r["stanox"]:
                loc = f"{loc} ({r['stanox']})".strip()
            html.append(f"<tr><td>{r['td_area']}</td><td><a href='/train?area={r['td_area']}&hc={r['headcode']}'>{r['headcode']}</a></td><td>{r['last_time_utc'] or ''}</td><td>{r['from_berth'] or ''}</td><td>{r['to_berth'] or ''}</td><td>{loc}</td><td>{r['platform'] or ''}</td><td>{sched}</td></tr>")
        html.append("</table>")
        html.append("<p><a href='/events'>Recent events</a></p>")
        html.append("</body></html>")
        return "\n".join(html)

    @app.get("/train")
    def train():
        area = request.args.get("area","")
        hc = request.args.get("hc","")
        st = q("SELECT * FROM td_state WHERE td_area=? AND headcode=?", (area, hc))
        ev = q("SELECT * FROM td_event WHERE td_area=? AND headcode=? ORDER BY ts_utc DESC LIMIT 200", (area, hc))
        html=["<html><head><meta charset='utf-8'><title>Train</title></head><body style='font-family:system-ui,Arial;margin:20px'>"]
        html.append(f"<h2>{area} / {hc}</h2><p><a href='/'>Back</a></p>")
        if st:
            r=st[0]
            html.append("<pre>"+str(dict(r))+"</pre>")
        html.append("<h3>Recent events</h3><table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in ev:
            html.append(f"<tr><td>{r['ts_utc']}</td><td>{r['event_type']}</td><td>{r['from_berth'] or ''}</td><td>{r['to_berth'] or ''}</td></tr>")
        html.append("</table></body></html>")
        return "\n".join(html)

    @app.get("/events")
    def events():
        rows = q("SELECT ts_utc, td_area, headcode, event_type, from_berth, to_berth FROM td_event ORDER BY ts_utc DESC LIMIT 500")
        html=["<html><head><meta charset='utf-8'><title>Events</title></head><body style='font-family:system-ui,Arial;margin:20px'>"]
        html.append("<h2>Recent TD events</h2><p><a href='/'>Back</a></p>")
        html.append("<table><tr><th>Time</th><th>Area</th><th>Headcode</th><th>Type</th><th>From</th><th>To</th></tr>")
        for r in rows:
            html.append(f"<tr><td>{r['ts_utc']}</td><td>{r['td_area']}</td><td>{r['headcode']}</td><td>{r['event_type']}</td><td>{r['from_berth']}</td><td>{r['to_berth']}</td></tr>")
        html.append("</table></body></html>")
        return "\n".join(html)

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    args = parse_args()

    if not args.pretty and not args.raw:
        args.pretty = True

    connect_and_run(args)
