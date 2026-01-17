#!/usr/bin/env python3
"""Convert one or more Network Rail shapefiles to a single SQLite database.

Usage examples:
  python convert_nwr_shp_to_sqlite.py /path/to/NWR_ELRs.shp --out nwr.sqlite
  python convert_nwr_shp_to_sqlite.py /path/to/folder_with_shps --out nwr.sqlite
  python convert_nwr_shp_to_sqlite.py /path/to/NWR_TrackModel.zip --out nwr.sqlite

Notes
- Geometry is stored as WKT in `geom_wkt` (no SpatiaLite dependency).
- Coordinates are kept in the source CRS. The PRJ text is stored in metadata.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import shapefile  # pyshp
except Exception as e:  # pragma: no cover
    print("ERROR: pyshp (shapefile) is required. Try: pip install pyshp", file=sys.stderr)
    raise


def _sanitize_table_name(name: str) -> str:
    """Make a safe SQLite table name from a filename."""
    base = os.path.splitext(os.path.basename(name))[0]
    base = base.strip().lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "layer"
    if re.match(r"^[0-9]", base):
        base = "t_" + base
    return base


def _read_text_if_exists(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _field_sql_type(field_type: str, size: int, dec: int) -> str:
    """Map DBF field types to SQLite column types."""
    # pyshp types: 'C' (char), 'N' (number), 'F' (float), 'L' (logical), 'D' (date), 'M' (memo)
    if field_type in ("N", "I"):
        # If decimal places, treat as REAL
        return "REAL" if dec and dec > 0 else "INTEGER"
    if field_type in ("F", "O"):
        return "REAL"
    if field_type == "L":
        return "INTEGER"  # 0/1
    return "TEXT"


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _to_wkt_point(pt: Tuple[float, float]) -> str:
    return f"POINT({pt[0]} {pt[1]})"


def _to_wkt_linestring(points: List[Tuple[float, float]]) -> str:
    coords = ", ".join(f"{x} {y}" for x, y in points)
    return f"LINESTRING({coords})"


def _to_wkt_polygon(ring: List[Tuple[float, float]]) -> str:
    # Ensure closed ring
    if ring and ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    coords = ", ".join(f"{x} {y}" for x, y in ring)
    return f"({coords})"


def shape_to_wkt(shape: "shapefile.Shape") -> Tuple[str, str]:
    """Return (geom_type, wkt)."""
    st = shape.shapeType

    # Point / PointZ / PointM
    if st in (1, 11, 21):
        x, y = shape.points[0]
        return "POINT", _to_wkt_point((x, y))

    # Polyline / PolylineZ / PolylineM
    if st in (3, 13, 23):
        parts = list(shape.parts) + [len(shape.points)]
        lines: List[List[Tuple[float, float]]] = []
        for i in range(len(parts) - 1):
            a, b = parts[i], parts[i + 1]
            pts = [(float(x), float(y)) for x, y in shape.points[a:b]]
            if pts:
                lines.append(pts)
        if len(lines) == 1:
            return "LINESTRING", _to_wkt_linestring(lines[0])
        inner = ", ".join(f"({', '.join(f'{x} {y}' for x,y in ln)})" for ln in lines)
        return "MULTILINESTRING", f"MULTILINESTRING({inner})"

    # Polygon / PolygonZ / PolygonM
    if st in (5, 15, 25):
        parts = list(shape.parts) + [len(shape.points)]
        rings: List[List[Tuple[float, float]]] = []
        for i in range(len(parts) - 1):
            a, b = parts[i], parts[i + 1]
            pts = [(float(x), float(y)) for x, y in shape.points[a:b]]
            if pts:
                rings.append(pts)
        # NOTE: Shapefile polygons can contain holes; pyshp doesn't always distinguish.
        # We store as MULTIPOLYGON with one polygon containing all rings.
        if len(rings) == 1:
            return "POLYGON", f"POLYGON({_to_wkt_polygon(rings[0])})"
        rings_wkt = ", ".join(_to_wkt_polygon(r) for r in rings)
        return "POLYGON", f"POLYGON({rings_wkt})"

    # Null shape
    if st == 0:
        return "NULL", "GEOMETRYCOLLECTION EMPTY"

    # Fallback
    return f"SHAPETYPE_{st}", "GEOMETRYCOLLECTION EMPTY"


def _compute_bbox(points: List[Tuple[float, float]]):
    """Return (minx, miny, maxx, maxy) for a list of (x, y) points."""
    if not points:
        return (None, None, None, None)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))


def discover_shapefiles(input_path: str, extract_dir: Optional[str] = None) -> Tuple[List[str], Optional[str]]:
    """Return list of .shp files. If input is a zip, extract to temp dir and return that dir."""
    input_path = os.path.abspath(input_path)

    if os.path.isfile(input_path) and input_path.lower().endswith(".zip"):
        if extract_dir is None:
            extract_dir = tempfile.mkdtemp(prefix="nwr_trackmodel_")
        with zipfile.ZipFile(input_path, "r") as z:
            z.extractall(extract_dir)
        shps = []
        for root, _, files in os.walk(extract_dir):
            for fn in files:
                if fn.lower().endswith(".shp"):
                    shps.append(os.path.join(root, fn))
        shps.sort()
        return shps, extract_dir

    if os.path.isdir(input_path):
        shps = []
        for root, _, files in os.walk(input_path):
            for fn in files:
                if fn.lower().endswith(".shp"):
                    shps.append(os.path.join(root, fn))
        shps.sort()
        return shps, None

    if os.path.isfile(input_path) and input_path.lower().endswith(".shp"):
        return [input_path], None

    raise FileNotFoundError(f"Unsupported input: {input_path}")


def create_meta_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            table_name   TEXT PRIMARY KEY,
            source_shp   TEXT,
            feature_count INTEGER,
            shape_type   INTEGER,
            prj_wkt      TEXT,
            imported_at  TEXT
        )
        """
    )


def import_shapefile(conn: sqlite3.Connection, shp_path: str, table_name: Optional[str] = None) -> None:
    table = table_name or _sanitize_table_name(shp_path)

    r = shapefile.Reader(shp_path)
    fields = [f for f in r.fields if f[0] != "DeletionFlag"]

    # Build CREATE TABLE
    col_defs: List[str] = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
    field_names: List[str] = []
    for name, ftype, size, dec in fields:
        col = _sanitize_table_name(name)
        # Preserve original field name in a map? We'll keep sanitised, but usually names are already safe.
        if col in ("id", "geom_wkt", "geom_type", "minx", "miny", "maxx", "maxy", "num_points", "num_parts"):
            col = f"f_{col}"
        field_names.append(col)
        col_defs.append(f"{_quote_ident(col)} {_field_sql_type(ftype, size, dec)}")

    # Geometry & helpers
    col_defs.extend(
        [
            "geom_type TEXT",
            "geom_wkt  TEXT",
            "minx REAL",
            "miny REAL",
            "maxx REAL",
            "maxy REAL",
            "num_points INTEGER",
            "num_parts INTEGER",
        ]
    )

    conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")
    conn.execute(f"CREATE TABLE {_quote_ident(table)} ({', '.join(col_defs)})")

    insert_cols = field_names + ["geom_type", "geom_wkt", "minx", "miny", "maxx", "maxy", "num_points", "num_parts"]
    placeholders = ",".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO {_quote_ident(table)} ({', '.join(_quote_ident(c) for c in insert_cols)}) VALUES ({placeholders})"

    # Insert rows
    cur = conn.cursor()
    for sr in r.iterShapeRecords():
        rec = list(sr.record)
        shp = sr.shape
        geom_type, wkt = shape_to_wkt(shp)
        # bbox: [minx, miny, maxx, maxy]
        # Some shape types (e.g., POINT) don't expose a bbox attribute in pyshp.
        if hasattr(shp, "bbox") and shp.bbox and len(shp.bbox) == 4:
            minx, miny, maxx, maxy = map(float, shp.bbox)
        else:
            # Compute from points when bbox isn't available.
            minx, miny, maxx, maxy = _compute_bbox(shp.points)
        num_points = len(shp.points) if shp.points else 0
        num_parts = len(shp.parts) if shp.parts else 0

        # Normalise logicals to 0/1
        values: List[Any] = []
        for v in rec:
            if isinstance(v, bool):
                values.append(1 if v else 0)
            else:
                values.append(v)
        values.extend([geom_type, wkt, minx, miny, maxx, maxy, num_points, num_parts])
        cur.execute(sql, values)

    # Indexes
    cols_existing = set(field_names)
    for cand in ("elr", "asset_id", "track_id"):
        if cand in cols_existing:
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{cand} ON {_quote_ident(table)}({_quote_ident(cand)})")

    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_bbox ON {_quote_ident(table)}(minx, miny, maxx, maxy)"
    )

    # Metadata
    prj_path = os.path.splitext(shp_path)[0] + ".prj"
    prj_wkt = _read_text_if_exists(prj_path)
    conn.execute(
        "INSERT OR REPLACE INTO datasets(table_name, source_shp, feature_count, shape_type, prj_wkt, imported_at) VALUES (?,?,?,?,?,?)",
        (
            table,
            os.path.basename(shp_path),
            len(r),
            r.shapeType,
            prj_wkt,
            _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        ),
    )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Convert shapefile(s) into a single SQLite database")
    p.add_argument("input", help=".shp, directory, or .zip containing shapefiles")
    p.add_argument("--out", required=True, help="Output SQLite file")
    p.add_argument("--prefix", default="", help="Optional prefix added to all table names")
    p.add_argument("--keep-extracted", action="store_true", help="If input is a zip, keep the extracted temp folder")
    args = p.parse_args(argv)

    extract_dir: Optional[str] = None
    try:
        shps, extract_dir = discover_shapefiles(args.input)
        if not shps:
            raise RuntimeError("No .shp files found")

        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        if os.path.exists(out_path):
            os.remove(out_path)

        conn = sqlite3.connect(out_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            create_meta_tables(conn)

            for shp_path in shps:
                table = args.prefix + _sanitize_table_name(shp_path)
                print(f"Importing {os.path.basename(shp_path)} -> {table} ...")
                import_shapefile(conn, shp_path, table_name=table)
                conn.commit()

            conn.commit()
            print(f"Done. Wrote: {out_path}")
        finally:
            conn.close()

    finally:
        if extract_dir and not args.keep_extracted:
            shutil.rmtree(extract_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
