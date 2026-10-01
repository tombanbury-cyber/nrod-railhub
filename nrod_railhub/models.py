#!/usr/bin/env python3
"""Data models and helper functions for nrod_railhub."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple


# Constants
NR_HOST = "publicdatafeeds.networkrail.co.uk"
NR_PORT = 61618

TOPIC_VSTP = "/topic/VSTP_ALL"
TOPIC_TRUST = "/topic/TRAIN_MVT_ALL_TOC"
TOPIC_TD = "/topic/TD_ALL_SIG_AREA"

CORPUS_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS"
SMART_URL = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=SMART"


# Helper functions
def local_hhmm() -> str:
    # Uses the machine's local timezone (UK if your box is UK configured)
    return datetime.now().astimezone().strftime("%H:%M")


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


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


def utc_now_ms() -> int:
    """Return current UTC time as unix timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_to_ms(iso_str: str) -> Optional[int]:
    """Convert ISO 8601 timestamp string to unix time in milliseconds."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def hhmmss_to_hhmm(x: str) -> str:
    s = (x or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        return f"{s[:2]}:{s[2:4]}"
    return ""


def expand_td_signal_bytes(address: str, data: str) -> List[Tuple[int, int, str, str]]:
    """Expand an SG/SH S-class message into its individual byte addresses/values.

    SG/SH messages carry a starting hex `address` and a hex `data` string
    representing one or more consecutive bytes starting at that address, e.g.
    address="88", data="41689559" expands to:
        (0x88, 0x41, "88", "41")
        (0x89, 0x68, "89", "68")
        (0x8A, 0x95, "8A", "95")
        (0x8B, 0x59, "8B", "59")

    Returns a list of (address_int, value_int, address_hex, value_hex) tuples,
    or an empty list if `address`/`data` cannot be parsed.
    """
    addr_text = (address or "").strip()
    if not addr_text:
        return []
    try:
        start_addr = int(addr_text, 16)
    except ValueError:
        return []

    cleaned = (data or "").strip().replace(" ", "")
    if not cleaned or len(cleaned) % 2 != 0:
        return []
    try:
        payload = bytes.fromhex(cleaned)
    except ValueError:
        return []

    addr_width = max(2, len(addr_text))
    results: List[Tuple[int, int, str, str]] = []
    for offset, value in enumerate(payload):
        addr_int = start_addr + offset
        results.append((addr_int, value, f"{addr_int:0{addr_width}X}", f"{value:02X}"))
    return results


# Dataclasses
@dataclass
class VstpSchedule:
    uid: str = ""
    signalling_id: str = ""          # headcode / reporting number
    start_date: str = ""
    end_date: str = ""
    train_category: str = ""
    power_type: str = ""
    locations: List[Tuple[str, str, str]] = field(default_factory=list)
    # list of (tiploc, arr_hhmm, dep_hhmm)


@dataclass
class ItpsSchedule:
    """Lightweight planned schedule from the SCHEDULE (ITPS) extract."""

    uid: str = ""
    signalling_id: str = ""          # headcode / reporting number
    train_category: str = ""
    power_type: str = ""
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
    last_time_ms: int = 0            # unix timestamp in milliseconds
