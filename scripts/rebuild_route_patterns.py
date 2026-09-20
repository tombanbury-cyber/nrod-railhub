#!/usr/bin/env python3
"""Rebuild historical berth transition evidence for the visualization API."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.visualisation.route_inference import rebuild_route_patterns


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "railhub.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the visualization SQLite database",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    try:
        summary = rebuild_route_patterns(conn)
    finally:
        conn.close()

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
