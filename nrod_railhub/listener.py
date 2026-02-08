#!/usr/bin/env python3
"""STOMP message listener for nrod_railhub."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

import stomp

from .models import utc_now_iso, utc_now_ms, ms_to_iso_utc, safe_int
from .views import HumanView
from .database import RailDB
from .logging_config import get_logger

logger = get_logger("listener")

class Listener(stomp.ConnectionListener):
    def __init__(self, hv: HumanView, args: argparse.Namespace, db: Optional[RailDB] = None, 
                 output_callback: Optional[callable] = None) -> None:
        self.hv = hv
        self.args = args
        self.db = db
        self.output_callback = output_callback  # Optional callback for custom output handling

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
        """
        # (unchanged)

    def on_connecting(self, host_and_port):
        try:
            h, p = host_and_port
        except Exception:
            h, p = "?", "?"
        logger.info(f"Connecting TCP to {h}:{p} ...")

    def on_connected(self, frame) -> None:
        self.connected_at = utc_now_iso()
        # (unchanged)
        logger.info(f"CONNECTED. version={version} session={session} server={server}")

    def on_disconnected(self) -> None:
        logger.error("Disconnected.")

    def on_error(self, frame) -> None:
        body = getattr(frame, "body", "")
        hdrs = getattr(frame, "headers", {})
        logger.error(f"STOMP ERROR headers={hdrs} body={body}")

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
            logger.debug(f"RX {dest or '?'} ({len(frame.body or '')} bytes): {short}")

        if not frame.body:
            return

        try:
            payload = json.loads(frame.body)
        except Exception:
            if self.args.verbose:
                logger.debug("Non-JSON message ignored")
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

                # Persist VSTP to DB if available (existing summary behaviour)
                if self.db:
                    try:
                        self.db.upsert_vstp(
                            uid=vs.uid,
                            headcode=vs.signalling_id or "",
                            start_date=vs.start_date or "",
                            end_date=vs.end_date or "",
                            raw=item
                        )
                        logger.debug(f"DB: persisted VSTP uid={vs.uid} headcode={vs.signalling_id} start={vs.start_date}")
                    except Exception as e:
                        logger.warning(f"DB: failed to persist VSTP uid={getattr(vs, 'uid', '?')}: {e!r}")

                    # New: Persist expanded schedule (header + locations)
                    try:
                        self.db.insert_vstp_schedule(item)
                        logger.debug(f"DB: inserted VSTP schedule details uid={getattr(vs,'uid','?')} start={getattr(vs,'start_date','?')}")
                    except Exception as e:
                        try:
                            self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                            if self._db_err_count <= 5:
                                logger.error(f"DB: VSTP schedule persist failed: {type(e).__name__}: {e}")
                        except Exception:
                            pass

                if self.args.trace_headcode:
                    if self.args.headcode and vs.signalling_id == self.args.headcode:
                        logger.debug(f"TRACE VSTP headcode={vs.signalling_id} uid={vs.uid} start={vs.start_date}")
                    if self.args.uid and vs.uid == self.args.uid:
                        logger.debug(f"TRACE VSTP uid={vs.uid} headcode={vs.signalling_id}")

                if self._matches(vs.signalling_id, vs.uid):
                    if self._print_train_update(vs.signalling_id):
                        printed = True

            # ------------------------------------------------------------
            # TRUST
            # ------------------------------------------------------------
            elif "header" in item and "body" in item:
                # (unchanged existing TRUST handling)
                ...
            # Rest of on_message unchanged
            # ------------------------------------------------------------
        # (remaining unchanged)
