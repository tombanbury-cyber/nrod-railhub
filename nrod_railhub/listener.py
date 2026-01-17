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

from .models import utc_now_iso
from .views import HumanView
from .database import RailDB

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

        # Check if trace_headcode is enabled for this headcode
        trace = getattr(self.args, 'trace_headcode', False) and (
            (self.args.headcode and str(headcode) == self.args.headcode) or
            (self.args.uid and self.hv.headcode_by_uid.get(self.args.uid) == str(headcode))
        )

        # Choose renderer based on whether we have a TD area context.
        if td_area:
            text = self.hv.render_for_td(td_area, str(headcode), width=self.args.width, trace=trace)
        else:
            text = self.hv.render_for_headcode(str(headcode), width=self.args.width, trace=trace)

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



