#!/usr/bin/env python3
"""Command-line interface for nrod_railhub."""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import time
from datetime import datetime, timezone

import stomp

from .models import NR_HOST, NR_PORT, TOPIC_VSTP, TOPIC_TRUST, TOPIC_TD, utc_now_iso
from .resolvers import LocationResolver, SmartResolver, ScheduleResolver
from .views import HumanView
from .database import RailDB
from .listener import Listener
from .web import start_web_dashboard

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
    p.add_argument("--db-path", default="~/.cache/openraildata/railhub.db",
                   help="SQLite database path for state/event storage (enables DB output)")
    p.add_argument("--web-port", type=int, default=8088,
                   help="If set and --db-path is provided, start tiny web dashboard on this port")
    return p.parse_args()


