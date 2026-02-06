#!/usr/bin/env python3
"""Berth-to-signal correlation mapper for TD feed analysis."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Any, Dict, List, Tuple
from datetime import datetime, timezone  # added for fallback last_seen_utc

from .logging_config import get_logger

logger = get_logger("mapper")

STEP_TYPES = {"CA", "CB", "CC"}
SIG_TYPES = {"SF"}

# Default mapper configuration parameters
# These can be overridden by passing parameters to process_batch_for_mapper()
# Or loaded from database via: cfg = db.get_mapper_config()

#try:
#    config = db.get_mapper_config()
#    pre_ms_current = config.get("pre_ms", 1000)
#    post_ms_current = config.get("post_ms", 5000)
#    tau_ms_current = config.get("tau_ms", 2500)
#except Exception:
#    pre_ms_current = 1000
#    post_ms_current = 5000
#    tau_ms_current = 2500

pre_ms = 1000   # milliseconds before step to search for signals
post_ms = 5000  # milliseconds after step to search for signals
tau_ms = 2500   # time constant for exponential weighting


def _ts_to_iso_ms(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    except Exception as e:
        logger.warning(f"Mapper: Failed to convert timestamp {ts_ms}: {e}, using current time")
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def exp_weight(dt_ms: int, tau_ms: int = 2500) -> float:
    """Exponential weighting function for scoring signal correlations."""
    return math.exp(-abs(dt_ms) / float(tau_ms))

def process_batch_for_mapper(
    evs: List[Dict[str, Any]],
    *,
    pre_ms: int = pre_ms,
    post_ms: int = post_ms,
    tau_ms: int = tau_ms,
) -> Tuple[List[Tuple], List[Tuple]]:
    """
    Process events and return (obs_rows, score_rows) for DB insertion.
    
    Matches step events (CA/CB/CC) with signal events (SF) in time window.
    """
    if not evs:
        logger.debug("Mapper: No events to process")
        return [], []
    
    logger.debug(f"Mapper: Processing batch of {len(evs)} events (pre={pre_ms}ms, post={post_ms}ms, tau={tau_ms}ms)")
    
    # Filter and sort signals
    signals = [e for e in evs 
               if e.get("msg_type") in SIG_TYPES 
               and e.get("address") 
               and int(e.get("msg_ts", 0)) > 0]
    signals.sort(key=lambda e: int(e["msg_ts"]))
    sig_times = [int(e["msg_ts"]) for e in signals]
    
    # Filter and sort steps
    steps = [e for e in evs 
             if e.get("msg_type") in STEP_TYPES 
             and e.get("from_berth") 
             and e.get("to_berth") 
             and int(e.get("msg_ts", 0)) > 0]
    steps.sort(key=lambda e: int(e["msg_ts"]))
    
    logger.debug(f"Mapper: Found {len(signals)} signal events and {len(steps)} step events")
    
    if not steps:
        logger.debug("Mapper: No valid step events to correlate")
        return [], []
    
    if not signals:
        logger.debug("Mapper: No valid signal events to correlate")
        return [], []
    
    obs_rows: List[Tuple] = []
    score_rows: List[Tuple] = []
    
    for st in steps:
        st_ts = int(st["msg_ts"])
        # Binary search for signals in time window
        lo = bisect_left(sig_times, st_ts - pre_ms)
        hi = bisect_right(sig_times, st_ts + post_ms)
        
        for s in signals[lo:hi]:
            s_ts = int(s["msg_ts"])
            dt = s_ts - st_ts
            w = exp_weight(dt, tau_ms)
            
            obs_rows.append((
                st.get("td_area"),
                None,  # step_event_id
                st_ts,
                st.get("from_berth"),
                st.get("to_berth"),
                st.get("descr"),
                None,  # signal_event_id
                s_ts,
                str(s.get("address")),
                s.get("data"),
                abs(int(dt)),
                float(w),
            ))
            
            # Ensure last_seen_utc is not None (DB schema requires NOT NULL)
            last_seen_utc = s.get("received_at_utc")
            if not last_seen_utc:
                last_seen_utc = _ts_to_iso_ms(s_ts)

            last_seen_ts = int(s_ts) if s_ts else None

            score_rows.append((
                st.get("td_area"),
                st.get("from_berth"),
                st.get("to_berth"),
                str(s.get("address")),
                float(w),
                last_seen_ts,
                last_seen_utc,
                s.get("data"),
            ))
    
    logger.debug(f"Mapper: Generated {len(obs_rows)} observations and {len(score_rows)} score entries")
    
    return obs_rows, score_rows
