#!/usr/bin/env python3
"""Command-line interface for nrod_railhub."""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import stomp
import yaml

from .models import NR_HOST, NR_PORT, TOPIC_VSTP, TOPIC_TRUST, TOPIC_TD, utc_now_iso
from .resolvers import LocationResolver, SmartResolver, ScheduleResolver, TOCResolver
from .views import HumanView
from .database import RailDB
from .listener import Listener
from .web import start_web_dashboard
from .logging_config import setup_logger, get_logger

logger = get_logger("cli")


def load_config_file(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from a YAML file.
    
    Args:
        config_path: Path to the YAML configuration file
        
    Returns:
        Dictionary containing configuration values
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    path = pathlib.Path(config_path).expanduser()
    
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
    
    if config is None:
        return {}
    
    if not isinstance(config, dict):
        raise ValueError(f"Configuration file must contain a YAML dictionary, got {type(config).__name__}")
    
    return config


def merge_config_with_args(args: argparse.Namespace, config: Dict[str, Any], parser_defaults: Dict[str, Any]) -> argparse.Namespace:
    """
    Merge YAML configuration with command-line arguments.
    
    Command-line arguments take precedence over config file values.
    Config values are used when arguments are still at their default values.
    
    Args:
        args: Parsed command-line arguments
        config: Configuration dictionary from YAML file
        parser_defaults: Dictionary of default values from parser
        
    Returns:
        Updated argparse.Namespace with merged values
    """
    for key, value in config.items():
        # Skip None values in config
        if value is None:
            continue
            
        # Convert yaml key format (can be hyphenated) to args attribute format (underscored)
        attr_name = key.replace('-', '_')
        
        # Only set the value if the attribute exists in args
        if not hasattr(args, attr_name):
            continue
            
        current_value = getattr(args, attr_name)
        default_value = parser_defaults.get(attr_name)
        
        # Special handling for lists (like td_area) - only override if empty
        if isinstance(current_value, list):
            if len(current_value) == 0 and value:
                setattr(args, attr_name, value if isinstance(value, list) else [value])
        # For other values, only override if still at default
        elif current_value == default_value:
            setattr(args, attr_name, value)
    
    return args

def start_status_ticker(listener: Listener, interval: int = 15) -> threading.Thread:
    def loop():
        while True:
            time.sleep(interval)
            ca = listener.connected_at or "not-connected-yet"
            lm = listener.last_message_at or "none-yet"
            by_dest = ", ".join(
                f"{k.replace('/topic/', '')}={v}" for k, v in sorted(listener.msg_count_by_dest.items())
            ) or "no-messages"
            logger.info(f"STATUS connected_at={ca} last_msg={lm} total={listener.msg_count_total} [{by_dest}]")

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

    # Get db_path early so we can pass it to SmartResolver for inferred berth fallback
    db_path = str(pathlib.Path(args.db_path).expanduser()) if args.db_path else None

    smart = SmartResolver(db_path=db_path)
    smart.load_or_download(
        username=args.user,
        password=args.password,
        cache_path=args.smart_cache,
        force=args.smart_refresh,
        quiet=False,
    )
    
    # Initialize TOC resolver
    toc_resolver = TOCResolver()
    logger.info(f"TOC: loaded {len(toc_resolver.TOC_DATA)} TOC codes")
    
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
                    logger.info(f"SCHEDULE: downloading to {sched_path} ...")
                    ScheduleResolver().download(
                        username=args.user,
                        password=args.password,
                        out_gz=str(sched_path),
                        schedule_type=args.schedule_type,
                        day=args.schedule_day,
                        quiet=False,
                    )
                else:
                    logger.info(f"SCHEDULE: using cached file {sched_path}")

                hv.load_schedule_gz(
                    str(sched_path),
                    service_date=datetime.now(timezone.utc).date().isoformat(),
                    headcode_filter=args.headcode,
                    uid_filter=args.uid,
                    quiet=False,
                )
                logger.info("SCHEDULE: loaded (timetable enrichment enabled)")
            except Exception as e:
                logger.error(f"SCHEDULE: failed to load ({e}); continuing without timetable enrichment")

        threading.Thread(target=_schedule_worker, daemon=True).start()
    logger.info(f"Starting. stomp.py version={getattr(stomp, '__version__', '?')}")
    logger.info(f"Broker: {args.host}:{args.port}  (plain STOMP)  vhost={args.vhost}")

    conn = stomp.Connection11(
        host_and_ports=[(args.host, args.port)],
        keepalive=True,
        heartbeats=(10000, 10000),
        reconnect_attempts_max=5,
        vhost=args.vhost,
    )

    db = RailDB(
        db_path,
        enable_mapper=args.enable_mapper,
        retain_trust_days=getattr(args, 'retain_trust_days', None),
        retain_vstp_days=getattr(args, 'retain_vstp_days', None),
        retention_check_interval_s=getattr(args, 'retention_interval', 3600),
        retention_batch_size=getattr(args, 'retention_batch_size', 1000),
    ) if db_path else None
    
    # Populate TOC reference data in database if available
    if db and toc_resolver:
        try:
            toc_resolver.populate_database(db, quiet=False)
        except Exception as e:
            logger.error(f"Failed to populate TOC reference data: {e}")
    
    # Create listener with optional output callback for interactive mode
    output_callback = None
    if args.interactive:
        # In interactive mode, we'll capture output to a queue
        import queue
        output_queue: "queue.Queue[str]" = queue.Queue(maxsize=500)
        output_callback = lambda text: output_queue.put(text) if not output_queue.full() else None
    
    listener = Listener(hv, args, db=db, output_callback=output_callback)
    if args.web_port and db_path:
        # Pass config path to web dashboard for configuration editing
        config_file_path = args.config if args.config else None
        t = threading.Thread(target=start_web_dashboard, args=(db_path, args.web_port, config_file_path), daemon=True)
        t.start()
        logger.info(f"WEB: dashboard on http://0.0.0.0:{args.web_port} using {db_path}")
    conn.set_listener("", listener)

    logger.info("Connecting (wait=True) ...")
    try:
        # Artemis is picky about host/vhost; set it explicitly like the CLI does.
        conn.connect(
            login=args.user,
            passcode=args.password,
            wait=True,
            headers={"host": args.vhost},
        )
    except Exception as e:
        logger.error(f"CONNECT FAILED 2: {type(e).__name__}: {e!r}")
        return

    logger.info("Subscribing to topics...")
    conn.subscribe(destination=TOPIC_VSTP, id="vstp", ack="auto")
    logger.info(f"  subscribed {TOPIC_VSTP}")
    conn.subscribe(destination=TOPIC_TRUST, id="trust", ack="auto")
    logger.info(f"  subscribed {TOPIC_TRUST}")
    conn.subscribe(destination=TOPIC_TD, id="td", ack="auto")
    logger.info(f"  subscribed {TOPIC_TD}")

    if args.headcode:
        logger.info(f"Filter: headcode={args.headcode}")
    if args.uid:
        logger.info(f"Filter: uid={args.uid}")

    start_status_ticker(listener, interval=args.status_every)

    # Run in interactive curses mode if requested
    if args.interactive:
        from .curses_view import run_interactive_dashboard
        stop_event = threading.Event()
        try:
            run_interactive_dashboard(
                listener=listener,
                output_queue=output_queue,  # type: ignore[name-defined]
                headcode=args.headcode,
                uid=args.uid,
                td_area=args.td_area,
            )
        except KeyboardInterrupt:
            logger.info("\nExiting interactive mode...")
        finally:
            stop_event.set()
            try:
                conn.disconnect()
            except Exception:
                pass
    else:
        # Normal console mode
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nExiting...")
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Combine VSTP + TRUST + TD into a human-readable stream.")
    
    # Configuration file option (processed first)
    p.add_argument("--config", help="Path to YAML configuration file (command-line args override config file values)")
    
    p.add_argument("--host", default=NR_HOST, help="STOMP host (default: publicdatafeeds.networkrail.co.uk)")
    p.add_argument("--port", type=int, default=NR_PORT, help="STOMP port (default: 61618)")
    p.add_argument("--vhost", default=NR_HOST, help="STOMP vhost/host header (default: publicdatafeeds.networkrail.co.uk)")

    p.add_argument("--user", required=False, help="Network Rail Data Feeds username/email")
    p.add_argument("--password", required=False, help="Network Rail Data Feeds password")

    p.add_argument("--headcode", help="Filter output to a single headcode (e.g. 2C90)")
    p.add_argument("--uid", help="Filter output to a single CIF_train_uid (e.g. 43876)")

    p.add_argument(
        "--td-area",
        action="append",
        default=[],
        dest="td_area",
        help="Only show console output for these TD area IDs (repeatable, e.g. --td-area EK). Default: show all areas.",
    )

    p.add_argument(
        "--log-level",
        default="error",
        choices=["verbose", "info", "warning", "error"],
        help="Log level (default: error). Options: verbose (debug), info, warning, error",
    )
    p.add_argument("--verbose", action="store_true", 
                   help="Enable raw STOMP message preview (also sets log-level to verbose if not specified)")
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
    p.add_argument("--interactive", action="store_true", help="Run in interactive curses mode with real-time dashboard")
    p.add_argument("--width", type=int, default=96, help="Pretty output width (default 96)")

    p.add_argument("--trace-headcode", action="store_true",
               help="Extra debug: show when VSTP/TRUST/TD mention the filtered headcode/uid")
    p.add_argument("--db-path", default="~/.cache/openraildata/railhub.db",
                   help="SQLite database path for state/event storage (enables DB output)")
    p.add_argument("--web-port", type=int, default=8088,
                   help="If set and --db-path is provided, start tiny web dashboard on this port")
    p.add_argument("--disable-mapper", dest="enable_mapper", action="store_false", default=True,
                   help="Disable berth-to-signal correlation mapper (enabled by default)")
    
    # Retention settings
    p.add_argument("--retain-trust-days", type=int, default=None,
                   help="Days to retain TRUST messages (None = no cleanup)")
    p.add_argument("--retain-vstp-days", type=int, default=None,
                   help="Days to retain VSTP schedules (None = no cleanup)")
    p.add_argument("--retention-interval", type=int, default=3600,
                   help="Seconds between retention checks (default: 3600)")
    p.add_argument("--retention-batch-size", type=int, default=1000,
                   help="Batch size for deletion (default: 1000)")
    
    # Get default values before parsing
    parser_defaults = {}
    for action in p._actions:
        if action.dest != 'help' and action.dest != 'config':
            parser_defaults[action.dest] = action.default
    
    # Parse arguments
    args = p.parse_args()
    
    # Load and merge config file if specified
    if args.config:
        try:
            config = load_config_file(args.config)
            logger.info(f"Loaded configuration from {args.config}")
            args = merge_config_with_args(args, config, parser_defaults)
        except FileNotFoundError as e:
            logger.error(f"Configuration file error: {e}")
            sys.exit(1)
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration file: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error loading configuration file: {e}")
            sys.exit(1)
    
    # Validate required fields after config merge
    if not args.user:
        p.error("--user is required (either via command-line or config file)")
    if not args.password:
        p.error("--password is required (either via command-line or config file)")
    
    return args


