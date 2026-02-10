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



def _normalize_arg_value(val):
    """Coerce string-like 'None'/'null'/'', and whitespace-only, to real None."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("none", "null"):
            return None
        return s
    return val

def _normalize_area_list(val):
    """
    Accepts None, list, or comma-separated string.
    Returns None or list[str] with any 'None'/'null'/empty entries removed.
    """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("none", "null"):
            return None
        items = [p.strip() for p in s.split(",") if p.strip() and p.strip().lower() not in ("none", "null")]
        return items or None
    if isinstance(val, (list, tuple)):
        items = [str(p).strip() for p in val if p is not None]
        items = [p for p in items if p and p.lower() not in ("none", "null")]
        return items or None
    # unknown type — leave as-is
    return val


class Listener(stomp.ConnectionListener):
    def __init__(self, hv: HumanView, args: argparse.Namespace, db: Optional[RailDB] = None, 
                 output_callback: Optional[callable] = None,
                 trust_callback: Optional[callable] = None,
                 vstp_callback: Optional[callable] = None,
                 db_callback: Optional[callable] = None) -> None:
        self.hv = hv
        self.args = args
        
        
        # Then, in Listener.__init__ (or immediately after args are attached), call:
        self.args.headcode = _normalize_arg_value( getattr(self.args, "headcode", None) )
        # td_area may be list or CSV string
        self.args.td_area = _normalize_area_list( getattr(self.args, "td_area", None) )
        
        
        self.db = db
        self.output_callback = output_callback  # Optional callback for custom output handling (TD messages)
        self.trust_callback = trust_callback  # Optional callback for TRUST messages
        self.vstp_callback = vstp_callback  # Optional callback for VSTP messages
        self.db_callback = db_callback  # Optional callback for database operations

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
            (getattr(self.args, 'headcode', None) and str(headcode) == self.args.headcode) or
            (getattr(self.args, 'uid', None) and self.hv.headcode_by_uid.get(self.args.uid) == str(headcode))
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

        # Use output callback if provided, otherwise print to console
        if self.output_callback:
            self.output_callback(text)
        else:
            print(text)
        return True
    def on_connecting(self, host_and_port):
        try:
            h, p = host_and_port
        except Exception:
            h, p = "?", "?"
        logger.info(f"Connecting TCP to {h}:{p} ...")

    def on_connected(self, frame) -> None:
        self.connected_at = utc_now_iso()

        # Defensive header extraction: frame or headers may be None
        headers = getattr(frame, "headers", {}) or {}
        session = headers.get("session", "?")
        server = headers.get("server", "?")
        version = headers.get("version", "?")

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
                    
                uid = vs.uid
                start_date = vs.start_date
                end_date = vs.end_date

                # Persist VSTP to DB if available
                if self.db:
                  
                    #logger.error(f"DB is available, Persist VSTP {vs}")
                    
                    try:
                        self.db.upsert_vstp(
                            uid=vs.uid,
                            headcode=vs.signalling_id or "",
                            start_date=vs.start_date or "",
                            end_date=vs.end_date or "",
                            raw=item
                        )
                        logger.debug(f"DB: persisted VSTP uid={vs.uid} headcode={vs.signalling_id} start={vs.start_date}")
                        # Send DB operation to db callback if available
                        if self.db_callback:
                            self.db_callback(f"VSTP upsert: uid={vs.uid} hc={vs.signalling_id or '?'}")
                    except Exception as e:
                        logger.warning(f"DB: failed to persist VSTP uid={getattr(vs, 'uid', '?')}: {e!r}")

                    # Persist full expanded schedule (header + per-location rows)
                    try:
                        # insert_vstp_schedule expects the raw VSTP message dict
                        self.db.insert_vstp_schedule(item)
                        
                        logger.debug(f"DB: persisted VSTP schedule locations uid={getattr(vs,'uid','?')} start={getattr(vs,'start_date','?')}")
                        # Send DB operation to db callback if available
                        if self.db_callback:
                            self.db_callback(f"VSTP schedule insert: uid={getattr(vs,'uid','?')}")
                    except Exception as e:
                        logger.warning(f"DB: failed to insert VSTP schedule locations uid={getattr(vs,'uid','?')}: {e!r}")

                if self.args.trace_headcode:
                    if self.args.headcode and vs.signalling_id == self.args.headcode:
                        logger.debug(f"TRACE VSTP headcode={vs.signalling_id} uid={vs.uid} start={vs.start_date}")
                    if self.args.uid and vs.uid == self.args.uid:
                        logger.debug(f"TRACE VSTP uid={vs.uid} headcode={vs.signalling_id}")

                # Send formatted VSTP message to callback if available
                if self.vstp_callback:
                    # Format origin and destination from locations
                    origin = ""
                    dest = ""
                    if vs.locations:
                        origin_tiploc = vs.locations[0][0] if vs.locations else ""
                        dest_tiploc = vs.locations[-1][0] if vs.locations else ""
                        origin = self.hv.resolver.name_for_tiploc(origin_tiploc) or origin_tiploc
                        dest = self.hv.resolver.name_for_tiploc(dest_tiploc) or dest_tiploc
                        
                    vstp_msg = f"VSTP uid={vs.uid} hc={vs.signalling_id or '?'} {origin} → {dest} ({vs.start_date})"
                    self.vstp_callback(vstp_msg)

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
                
                # Send TRUST message to trust callback if available
                if self.trust_callback:
                    msg_type = item.get("header", {}).get("msg_type", "")
                    trust_msg = f"TRUST {msg_type}: train_id={ts.train_id} hc={trust_headcode} uid={ts.train_uid or '?'} loc={ts.last_location or '?'}"
                    self.trust_callback(trust_msg)

                # Persist TRUST to DB if available
                if self.db:
                    try:
                        self.db.upsert_trust(
                            train_id=ts.train_id,
                            headcode=trust_headcode,
                            uid=ts.train_uid or "",
                            toc_id=ts.toc_id or "",
                            last_event_time=ts.last_event_time or "",
                            last_location=ts.last_location or "",
                            last_delay_min=ts.last_delay_min,
                            raw=body,
                        )
                        logger.debug(f"DB: persisted TRUST train_id={ts.train_id} headcode={trust_headcode} uid={ts.train_uid}")
                        # Send DB operation to db callback if available
                        if self.db_callback:
                            self.db_callback(f"TRUST upsert: train_id={ts.train_id} hc={trust_headcode}")
                    except Exception as e:
                        logger.warning(f"DB: failed to persist TRUST train_id={getattr(ts, 'train_id', '?')}: {e!r}")

                    # Persist full decoded TRUST message into trust_messages history table
                    try:
                        self.db.insert_trust_message(body)
                        logger.debug(f"DB: inserted TRUST message history train_id={getattr(body,'train_id',getattr(ts,'train_id','?'))} actual_ts={body.get('actual_timestamp')}")
                        # Send DB operation to db callback if available
                        if self.db_callback:
                            self.db_callback(f"TRUST insert: train_id={getattr(body,'train_id',getattr(ts,'train_id','?'))}")
                    except Exception as e:
                        # Don't kill the receiver thread; log a few DB errors for diagnosis
                        try:
                            self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                            if self._db_err_count <= 5:
                                logger.error(f"DB: TRUST message persist failed: {type(e).__name__}: {e}")
                        except Exception:
                            pass

                # Trace TRUST visibility
                if self.args.trace_headcode:
                    if self.args.uid and ts.train_uid == self.args.uid:
                        logger.debug(f"TRACE TRUST uid={ts.train_uid} train_id={ts.train_id} time={ts.last_event_time}")
                    if self.args.headcode and (
                        trust_headcode == self.args.headcode
                        or self.hv.trust_by_headcode.get(self.args.headcode) is ts
                    ):
                        logger.debug(f"TRACE TRUST headcode={self.args.headcode} train_id={ts.train_id} uid={ts.train_uid}")

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
            # TD (wrapped as CA_MSG/CC_MSG/SF_MSG etc)
            # ------------------------------------------------------------
            td_msg = self._unwrap_td_item(item)
            if td_msg and "msg_type" in td_msg and ("descr" in td_msg or "to" in td_msg or "from" in td_msg or "address" in td_msg):
                msg_type = td_msg.get("msg_type", "").upper()
                
                # Handle signal events (S-Class: SF, SG, SH) separately
                # These don't have a descr field and don't update TD state
                if msg_type in ("SF", "SG", "SH"):
                    if self.db:
                      
                      
                        #logger.error(f"TD message: {msg_type}")
                      
                        try:
                          
                            #logger.error(f"TD message try: {msg_type}")
                          
                            area_id = (td_msg.get("area_id") or "").strip()
                            address = td_msg.get("address", "")
                            
                            #logger.error(f"TD area_id: {area_id}")
                            #logger.error(f"TD address: {address}")
                            #logger.error(f"TD td_area filter: {self.args.td_area}")
                            
                            # Apply area filter if configured
                            if self.args.td_area and area_id and area_id not in self.args.td_area:
                                continue
                            
                            if area_id and address:
                                ts_ms = safe_int(td_msg.get("time")) or utc_now_ms()
                                ts_iso = ms_to_iso_utc(ts_ms)
                                #logger.error(f"attempt td event insert: {td_msg}")
                                self.db.insert_td_signal_event(
                                    ts_ms=ts_ms,
                                    ts_iso=ts_iso,
                                    area=area_id,
                                    msg_type=msg_type,
                                    address=address,
                                    data=td_msg.get("data", "")
                                )
                        except Exception as e:
                            # Don't kill the receiver thread; log a few DB errors for diagnosis
                            try:
                                #logger.error(f"attempt td event insert failed: {area_id}")
                                self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                                #if self._db_err_count <= 5:
                                logger.error(f"DB: TD signal event persist failed: {type(e).__name__}: {e}")
                            except Exception:
                                pass
                    continue
                
                # Handle berth events (C-Class: CA, CB, CC)
                # These have a descr field and update TD state
                #logger.error(f"Handle berth events: {td_msg}")
                td = self.hv.upsert_td(td_msg)
                if not td:
                    continue
                    
                #logger.error(f"Handle berth events: {td}")

                if self.args.trace_headcode and self.args.headcode and td.descr == self.args.headcode:
                    td_time_iso = ms_to_iso_utc(td.last_time_ms) if td.last_time_ms else "?"
                    logger.debug(f"TRACE TD headcode={td.descr} area={td.area_id} {td.from_berth}->{td.to_berth} time={td_time_iso}")

                #logger.error(f"foobar: {td.descr}") 
                #logger.error(f"headcode arg: {self.args.headcode}") 

                if self.args.headcode != None and td.descr != self.args.headcode:
                    #logger.error(f"continue 1 {self.args.headcode, td.descr}") 
                    continue
                    
                #logger.error(f"fred: {td.descr}")    

                #logger.error(f"td_area arg: {self.args.td_area, td.area_id}") 
                if self.args.td_area and td.area_id and td.area_id not in self.args.td_area:
                    #logger.error(f"continue 2 {self.args.td_area, td.area_id}") 
                    continue
                    
                #logger.error(f"td_area arg: {self.args.td_area, td.area_id}")     
                    
                #logger.error(f"test: {td.area_id}")        

                # Persist berth events and update TD state
                if self.db:
                    try:
                        ts_ms = safe_int(td_msg.get("time")) or utc_now_ms()
                        ts_iso = ms_to_iso_utc(ts_ms)
                        
                        # Insert berth event record
                        if msg_type in ("CA", "CB", "CC"):
                            if td.area_id and td.descr:
                                #logger.error(f"Attempt Insert berth event record: {td.area_id, td.descr}")
                                self.db.insert_td_berth_event(
                                    ts_ms=ts_ms,
                                    ts_iso=ts_iso,
                                    area=td.area_id,
                                    headcode=td.descr,
                                    msg_type=msg_type,
                                    from_berth=td.from_berth,
                                    to_berth=td.to_berth,
                                    descr=td.descr
                                )
                        
                        # Update current TD state with enriched location/schedule data
                        if msg_type in ("CA", "CB", "CC") and td.area_id and td.descr:
                            # Enrich via HumanView render context
                            loc = self.hv.decode_last_location(td.area_id, td.descr)
                            tten = self.hv.get_timetable_fields(td.descr)
                            self.db.upsert_td_state(
                                area=td.area_id,
                                headcode=td.descr,
                                last_time_ms=ts_ms,
                                last_time_iso=ts_iso,
                                from_berth=td.from_berth,
                                to_berth=td.to_berth,
                                stanox=loc.get('stanox'),
                                location_name=loc.get('name'),
                                platform=loc.get('platform'),
                                sched_dep=tten.get('dep'),
                                sched_arr=tten.get('arr'),
                                origin_name=tten.get('origin'),
                                dest_name=tten.get('dest'),
                                uid=tten.get('uid')
                            )
                    except Exception as e:
                        # Don't kill the receiver thread; log a few DB errors for diagnosis
                        try:
                            self._db_err_count = getattr(self, '_db_err_count', 0) + 1
                            if self._db_err_count <= 5:
                                logger.error(f"DB: TD berth event persist failed: {type(e).__name__}: {e}")
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
