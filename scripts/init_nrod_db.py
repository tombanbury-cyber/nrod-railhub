#!/usr/bin/env python3
"""
Apply an SQL schema file to create a SQLite DB and record schema version.

Usage:
  python scripts/init_nrod_db.py --sql database/nrod.sqlite.sql --out nrod.sqlite
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import os
import datetime as _dt

# Schema version for metadata tracking
SCHEMA_VERSION = "1"


def apply_sql(schema_path: str, out_path: str) -> None:
    if os.path.exists(out_path):
        print(f"Updating existing DB: {out_path}")
    else:
        print(f"Creating DB: {out_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = sqlite3.connect(out_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(sql)
        # Add simple schema versioning table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_schema (
                name TEXT PRIMARY KEY,
                version TEXT,
                applied_at TEXT
            );
        """)
        conn.execute("INSERT OR REPLACE INTO meta_schema(name, version, applied_at) VALUES (?,?,?)",
                     ("nrod", SCHEMA_VERSION, _dt.datetime.now(_dt.timezone.utc).isoformat()))
        conn.commit()
        print("Schema applied.")
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    apply_sql(args.sql, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
