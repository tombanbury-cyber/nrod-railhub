#!/usr/bin/env python3
"""
nrod_ref_import.py

Download and import Network Rail Open Data supporting reference files
CORPUS and SMART into a SQLite database.

- CORPUS expected top-level keys:
    {"TIPLOCDATA":[...], "STANOXDATA":[...], "CRSDATA":[...]}

- SMART expected top-level keys:
    {"BERTHDATA":[...]}

Download endpoints (after registration/subscription):
    https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS
    https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=SMART
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
from typing import Any, Dict, Optional


URL_TEMPLATE = "https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type={type}"


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def norm(v: Any) -> Any:
    """
    Normalise common 'blank' values in CORPUS/SMART:
    - "" or " " or "\t" => None
    - leaves non-strings untouched
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return v


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def maybe_gunzip_bytes(data: bytes) -> bytes:
    # gzip magic bytes
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        return gzip.decompress(data)
    return data


def download_with_requests(url: str, username: str, password: str, out_path: str) -> None:
    import requests  # type: ignore

    with requests.get(url, auth=(username, password), stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def download_with_urllib(url: str, username: str, password: str, out_path: str) -> None:
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("User-Agent", "nrod_ref_import/1.0")

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    with open(out_path, "wb") as f:
        f.write(data)


def download_file(url: str, username: str, password: str, out_path: str) -> None:
    # Try requests first, fallback to urllib
    try:
        import requests  # noqa: F401

        download_with_requests(url, username, password, out_path)
    except Exception:
        download_with_urllib(url, username, password, out_path)


def read_json_file(path: str) -> Any:
    with open(path, "rb") as f:
        raw = f.read()
    raw = maybe_gunzip_bytes(raw)
    text = raw.decode("utf-8", errors="replace").strip()
    return json.loads(text)


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta_downloads (
            dataset        TEXT PRIMARY KEY,
            source_url     TEXT NOT NULL,
            downloaded_at  TEXT NOT NULL,
            file_path      TEXT NOT NULL,
            sha256         TEXT NOT NULL
        );

        -- CORPUS tables (reflect top-level keys)
        CREATE TABLE IF NOT EXISTS corpus_tiploc (
            tiploc     TEXT,
            stanox     TEXT,
            crs        TEXT,   -- 3ALPHA
            nlc        INTEGER,
            uic        TEXT,
            nlcdesc    TEXT,
            nlcdesc16  TEXT,
            raw_json   TEXT,
            PRIMARY KEY (tiploc, stanox, crs, nlc)
        );

        CREATE INDEX IF NOT EXISTS idx_corpus_tiploc_stanox ON corpus_tiploc(stanox);
        CREATE INDEX IF NOT EXISTS idx_corpus_tiploc_crs    ON corpus_tiploc(crs);
        CREATE INDEX IF NOT EXISTS idx_corpus_tiploc_tiploc ON corpus_tiploc(tiploc);

        CREATE TABLE IF NOT EXISTS corpus_stanox (
            stanox   TEXT PRIMARY KEY,
            name     TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS corpus_crs (
            crs      TEXT PRIMARY KEY,
            name     TEXT,
            raw_json TEXT
        );

        -- SMART stepping data
        CREATE TABLE IF NOT EXISTS smart_steps (
            td           TEXT NOT NULL,
            step_type    TEXT,
            event        TEXT,

            from_berth   TEXT,
            to_berth     TEXT,

            stanox       TEXT,
            stanme       TEXT,

            platform     TEXT,
            route        TEXT,
            from_line    TEXT,
            to_line      TEXT,
            berthoffset  TEXT,
            comment      TEXT,

            raw_json     TEXT,

            UNIQUE (td, step_type, event, from_berth, to_berth, stanox,
                    platform, route, from_line, to_line, berthoffset, comment)
        );

        CREATE INDEX IF NOT EXISTS idx_smart_td         ON smart_steps(td);
        CREATE INDEX IF NOT EXISTS idx_smart_from       ON smart_steps(from_berth);
        CREATE INDEX IF NOT EXISTS idx_smart_to         ON smart_steps(to_berth);
        CREATE INDEX IF NOT EXISTS idx_smart_stanox     ON smart_steps(stanox);
        CREATE INDEX IF NOT EXISTS idx_smart_td_stanox  ON smart_steps(td, stanox);

        -- Unique berths (implicit in SMART)
        CREATE VIEW IF NOT EXISTS v_berths AS
            SELECT DISTINCT td, from_berth AS berth
            FROM smart_steps
            WHERE from_berth IS NOT NULL
            UNION
            SELECT DISTINCT td, to_berth AS berth
            FROM smart_steps
            WHERE to_berth IS NOT NULL;

        -- Helpful joined view
        CREATE VIEW IF NOT EXISTS v_smart_steps_with_location AS
        SELECT
          s.td, s.from_berth, s.to_berth,
          s.stanox, s.stanme,
          COALESCE(st.name, tl.nlcdesc, s.stanme) AS location_name,
          tl.tiploc, tl.crs,
          s.platform, s.route, s.from_line, s.to_line, s.berthoffset, s.comment
        FROM smart_steps s
        LEFT JOIN corpus_stanox st ON st.stanox = s.stanox
        LEFT JOIN corpus_tiploc tl ON tl.stanox = s.stanox;
        """
    )
    conn.commit()


def record_download_meta(conn: sqlite3.Connection, dataset: str, url: str, path: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO meta_downloads(dataset, source_url, downloaded_at, file_path, sha256)
        VALUES (?,?,?,?,?)
        """,
        (dataset, url, utc_now_iso(), os.path.abspath(path), sha256_file(path)),
    )
    conn.commit()


def import_corpus(conn: sqlite3.Connection, corpus_json: Any) -> Dict[str, int]:
    if not isinstance(corpus_json, dict):
        raise ValueError("CORPUS JSON must be an object with TIPLOCDATA/STANOXDATA/CRSDATA keys")

    counts = {"TIPLOCDATA": 0, "STANOXDATA": 0, "CRSDATA": 0}
    cur = conn.cursor()

    # TIPLOCDATA
    tiplocs = corpus_json.get("TIPLOCDATA", [])
    if isinstance(tiplocs, list):
        for r in tiplocs:
            if not isinstance(r, dict):
                continue
            tiploc = norm(r.get("TIPLOC"))
            stanox = norm(r.get("STANOX"))
            crs = norm(r.get("3ALPHA")) or norm(r.get("CRS"))
            nlc = r.get("NLC") if isinstance(r.get("NLC"), int) else None
            uic = norm(r.get("UIC"))
            nlcdesc = norm(r.get("NLCDESC"))
            nlcdesc16 = norm(r.get("NLCDESC16"))

            cur.execute(
                """
                INSERT OR REPLACE INTO corpus_tiploc
                (tiploc, stanox, crs, nlc, uic, nlcdesc, nlcdesc16, raw_json)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    tiploc,
                    stanox,
                    crs,
                    nlc,
                    uic,
                    nlcdesc,
                    nlcdesc16,
                    json.dumps(r, ensure_ascii=False),
                ),
            )
            counts["TIPLOCDATA"] += 1
            if counts["TIPLOCDATA"] % 50000 == 0:
                conn.commit()

    # STANOXDATA
    stanox_rows = corpus_json.get("STANOXDATA", [])
    if isinstance(stanox_rows, list):
        for r in stanox_rows:
            if not isinstance(r, dict):
                continue
            stanox = norm(r.get("STANOX"))
            name = norm(r.get("UCASE") or r.get("NAME") or r.get("STANME"))
            if not stanox:
                continue
            cur.execute(
                "INSERT OR REPLACE INTO corpus_stanox (stanox, name, raw_json) VALUES (?,?,?)",
                (stanox, name, json.dumps(r, ensure_ascii=False)),
            )
            counts["STANOXDATA"] += 1

    # CRSDATA
    crs_rows = corpus_json.get("CRSDATA", [])
    if isinstance(crs_rows, list):
        for r in crs_rows:
            if not isinstance(r, dict):
                continue
            crs = norm(r.get("CRS") or r.get("3ALPHA"))
            name = norm(r.get("UCASE") or r.get("NAME"))
            if not crs:
                continue
            cur.execute(
                "INSERT OR REPLACE INTO corpus_crs (crs, name, raw_json) VALUES (?,?,?)",
                (crs, name, json.dumps(r, ensure_ascii=False)),
            )
            counts["CRSDATA"] += 1

    conn.commit()
    return counts


def import_smart(conn: sqlite3.Connection, smart_json: Any) -> int:
    if not isinstance(smart_json, dict):
        raise ValueError("SMART JSON must be an object with BERTHDATA key")

    rows = smart_json.get("BERTHDATA", [])
    if not isinstance(rows, list):
        raise ValueError("SMART JSON does not contain BERTHDATA as a list")

    cur = conn.cursor()
    n = 0

    for r in rows:
        if not isinstance(r, dict):
            continue

        td = norm(r.get("TD"))
        if not td:
            continue

        cur.execute(
            """
            INSERT OR IGNORE INTO smart_steps
            (td, step_type, event, from_berth, to_berth, stanox, stanme,
             platform, route, from_line, to_line, berthoffset, comment, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                td,
                norm(r.get("STEPTYPE")),
                norm(r.get("EVENT")),
                norm(r.get("FROMBERTH")),
                norm(r.get("TOBERTH")),
                norm(r.get("STANOX")),
                norm(r.get("STANME")),
                norm(r.get("PLATFORM")),
                norm(r.get("ROUTE")),
                norm(r.get("FROMLINE")),
                norm(r.get("TOLINE")),
                norm(r.get("BERTHOFFSET")),
                norm(r.get("COMMENT")),
                json.dumps(r, ensure_ascii=False),
            ),
        )

        n += 1
        if n % 50000 == 0:
            conn.commit()

    conn.commit()
    return n


def maybe_rebuild(conn: sqlite3.Connection) -> None:
    # Drop tables/views and recreate schema
    conn.executescript(
        """
        DROP VIEW IF EXISTS v_smart_steps_with_location;
        DROP VIEW IF EXISTS v_berths;

        DROP TABLE IF EXISTS smart_steps;
        DROP TABLE IF EXISTS corpus_tiploc;
        DROP TABLE IF EXISTS corpus_stanox;
        DROP TABLE IF EXISTS corpus_crs;
        DROP TABLE IF EXISTS meta_downloads;
        """
    )
    conn.commit()
    init_schema(conn)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Download and import Network Rail CORPUS + SMART reference data into SQLite."
    )
    ap.add_argument("--db", default="nrod_ref.sqlite", help="SQLite database path (default: nrod_ref.sqlite)")
    ap.add_argument("--username", required=True, help="Network Rail Open Data username (email)")
    ap.add_argument("--password", required=True, help="Network Rail Open Data password")
    ap.add_argument("--outdir", default="nrod_ref_downloads", help="Download directory")
    ap.add_argument("--no-download", action="store_true", help="Skip download, import from existing files in outdir")
    ap.add_argument("--datasets", default="CORPUS,SMART", help="Comma list: CORPUS,SMART (default both)")
    ap.add_argument("--rebuild", action="store_true", help="Drop and recreate tables/views before importing")
    args = ap.parse_args()

    datasets = [d.strip().upper() for d in args.datasets.split(",") if d.strip()]
    os.makedirs(args.outdir, exist_ok=True)

    conn = connect_db(args.db)
    init_schema(conn)
    if args.rebuild:
        maybe_rebuild(conn)

    for ds in datasets:
        url = URL_TEMPLATE.format(type=ds)
        out_path = os.path.join(args.outdir, f"{ds}.json")

        if not args.no_download:
            print(f"[{ds}] downloading: {url}")
            download_file(url, args.username, args.password, out_path)
            record_download_meta(conn, ds, url, out_path)
            print(f"[{ds}] saved: {out_path}")
        else:
            if not os.path.exists(out_path):
                raise FileNotFoundError(f"--no-download set but file not found: {out_path}")

        print(f"[{ds}] parsing JSON...")
        data = read_json_file(out_path)

        if ds == "CORPUS":
            counts = import_corpus(conn, data)
            print(f"[{ds}] imported TIPLOCDATA={counts['TIPLOCDATA']}, STANOXDATA={counts['STANOXDATA']}, CRSDATA={counts['CRSDATA']}")
        elif ds == "SMART":
            count = import_smart(conn, data)
            print(f"[{ds}] imported BERTHDATA rows={count}")
        else:
            print(f"[{ds}] unsupported dataset (supported: CORPUS, SMART). Skipping.")

    print(f"Done. DB: {os.path.abspath(args.db)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

