"""Track and junction persistence helpers for the visualisation layout editor.

A *track* is an ordered route (``points``) between two endpoints. Each endpoint is either a
berth socket (``berth_id`` + ``side`` of ``left``/``right``, at the midpoint of that berth edge) or
a *junction* (a node placed on another track's route). Routes may only use horizontal, vertical and
45-degree segments. Geometry is generated client-side (``web/static/track-routing.js``); the
server validates it so stored routes always satisfy the same constraints.
"""

import json
import math
from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, constr

TRACK_DDL = """
CREATE TABLE IF NOT EXISTS layout_track (
    id TEXT PRIMARY KEY,
    layout_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_berth_id TEXT,
    start_side TEXT,
    start_junction_id TEXT,
    end_berth_id TEXT,
    end_side TEXT,
    end_junction_id TEXT,
    points TEXT NOT NULL,
    FOREIGN KEY (layout_id) REFERENCES layout(id)
)
"""

JUNCTION_DDL = """
CREATE TABLE IF NOT EXISTS layout_junction (
    id TEXT PRIMARY KEY,
    layout_id TEXT NOT NULL,
    track_id TEXT NOT NULL,
    name TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    FOREIGN KEY (layout_id) REFERENCES layout(id)
)
"""

MAX_ROUTE_POINTS = 500


class Point(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class TrackEndpoint(BaseModel):
    """Either a berth socket (``berth_id`` + ``side``) or a ``junction_id``."""
    berth_id: Optional[constr(strip_whitespace=True, min_length=1)] = None
    side: Optional[Literal["left", "right"]] = None
    junction_id: Optional[constr(strip_whitespace=True, min_length=1)] = None


class TrackPayload(BaseModel):
    id: constr(strip_whitespace=True, min_length=1)
    layout_id: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1, max_length=200)
    start: TrackEndpoint
    end: TrackEndpoint
    points: list[Point] = Field(min_length=2, max_length=MAX_ROUTE_POINTS)


class JunctionPayload(BaseModel):
    id: constr(strip_whitespace=True, min_length=1)
    layout_id: constr(strip_whitespace=True, min_length=1)
    track_id: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1, max_length=200)
    x: int = Field(ge=0)
    y: int = Field(ge=0)


def ensure_tables(conn: Any) -> None:
    """Create the track/junction tables on demand for databases that predate them."""
    for table, ddl in (("layout_track", TRACK_DDL), ("layout_junction", JUNCTION_DDL)):
        if not conn.table_exists(table):
            conn.execute(ddl)
            conn.commit()


# ---------------------------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------------------------

def socket_point(berth: Any, side: str) -> tuple[int, int]:
    """Midpoint of a berth's left or right edge (matches ``socketPoint`` in track-routing.js)."""
    x, y, w, h = int(berth["x"]), int(berth["y"]), int(berth["width"]), int(berth["height"])
    return (x if side == "left" else x + w, y + (h + 1) // 2)


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def validate_octilinear(points: list[tuple[int, int]]) -> None:
    """Reject zero-length segments and any segment that is not horizontal, vertical or 45 degrees."""
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            raise HTTPException(status_code=422, detail="Track route contains a zero-length segment")
        if dx != 0 and dy != 0 and abs(dx) != abs(dy):
            raise HTTPException(
                status_code=422,
                detail="Track segments must be horizontal, vertical or 45 degrees",
            )


def nearest_point_on_route(points: list[tuple[int, int]], px: int, py: int) -> tuple[int, int, float]:
    """Return the exact integer point on the route closest to (px, py) and its distance."""
    best: Optional[tuple[int, int, float]] = None
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        ux, uy = _sign(bx - ax), _sign(by - ay)
        steps = max(abs(bx - ax), abs(by - ay))
        t = ((px - ax) * ux + (py - ay) * uy) / (ux * ux + uy * uy)
        t = min(steps, max(0, math.floor(t + 0.5)))
        qx, qy = ax + t * ux, ay + t * uy
        dist = math.hypot(px - qx, py - qy)
        if best is None or dist < best[2]:
            best = (qx, qy, dist)
    assert best is not None
    return best


def point_on_route(points: list[tuple[int, int]], px: int, py: int) -> bool:
    return nearest_point_on_route(points, px, py)[2] == 0


def route_tuples(points: Any) -> list[tuple[int, int]]:
    return [(int(p["x"]), int(p["y"])) if isinstance(p, dict) else (int(p.x), int(p.y)) for p in points]


# ---------------------------------------------------------------------------------------------
# Row <-> response conversion
# ---------------------------------------------------------------------------------------------

def _endpoint(row: Any, prefix: str) -> dict[str, Any]:
    junction_id = row[f"{prefix}_junction_id"]
    if junction_id:
        return {"junction_id": junction_id}
    return {"berth_id": row[f"{prefix}_berth_id"], "side": row[f"{prefix}_side"]}


def track_response(row: Any) -> dict[str, Any]:
    try:
        points = json.loads(row["points"])
    except (TypeError, ValueError):
        points = []
    return {
        "id": row["id"],
        "layout_id": row["layout_id"],
        "name": row["name"],
        "start": _endpoint(row, "start"),
        "end": _endpoint(row, "end"),
        "points": points,
    }


def junction_response(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"], "layout_id": row["layout_id"], "track_id": row["track_id"],
        "name": row["name"], "x": int(row["x"]), "y": int(row["y"]),
    }


def list_tracks(conn: Any, layout_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM layout_track WHERE layout_id = ? ORDER BY id", (layout_id,)).fetchall()
    return [track_response(row) for row in rows]


def list_junctions(conn: Any, layout_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM layout_junction WHERE layout_id = ? ORDER BY id", (layout_id,)).fetchall()
    return [junction_response(row) for row in rows]


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def _resolve_endpoint(
    conn: Any, layout_id: str, endpoint: TrackEndpoint, label: str
) -> tuple[tuple[int, int], Optional[str], Optional[str], Optional[str]]:
    """Validate an endpoint; return (position, berth_id, side, junction_id)."""
    if endpoint.junction_id:
        if endpoint.berth_id or endpoint.side:
            raise HTTPException(status_code=422, detail=f"Track {label} must be a berth socket or a junction, not both")
        row = conn.execute(
            "SELECT * FROM layout_junction WHERE id = ? AND layout_id = ?", (endpoint.junction_id, layout_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Track {label} junction not found in layout")
        return (int(row["x"]), int(row["y"])), None, None, endpoint.junction_id
    if not endpoint.berth_id or not endpoint.side:
        raise HTTPException(status_code=422, detail=f"Track {label} needs a berth_id and side, or a junction_id")
    berth = conn.execute(
        "SELECT * FROM berth WHERE id = ? AND layout_id = ?", (endpoint.berth_id, layout_id)
    ).fetchone()
    if not berth:
        raise HTTPException(status_code=404, detail=f"Track {label} berth not found in layout")
    return socket_point(berth, endpoint.side), endpoint.berth_id, endpoint.side, None


def _hosted_by_chain(conn: Any, junction_id: Optional[str], track_id: str, seen: Optional[set] = None) -> bool:
    """True when ``junction_id`` sits (directly or transitively) on a route that depends on ``track_id``."""
    if not junction_id:
        return False
    seen = seen if seen is not None else set()
    if junction_id in seen:
        return False
    seen.add(junction_id)
    junction = conn.execute("SELECT track_id FROM layout_junction WHERE id = ?", (junction_id,)).fetchone()
    if not junction:
        return False
    host_id = junction["track_id"]
    if host_id == track_id:
        return True
    host = conn.execute(
        "SELECT start_junction_id, end_junction_id FROM layout_track WHERE id = ?", (host_id,)
    ).fetchone()
    return bool(host) and any(
        _hosted_by_chain(conn, host[key], track_id, seen) for key in ("start_junction_id", "end_junction_id")
    )


def validate_track(conn: Any, payload: TrackPayload) -> tuple[list[tuple[int, int]], tuple, tuple]:
    """Validate a track payload against stored berths/junctions; return (points, start, end)."""
    start = _resolve_endpoint(conn, payload.layout_id, payload.start, "start")
    end = _resolve_endpoint(conn, payload.layout_id, payload.end, "end")
    points = route_tuples(payload.points)
    validate_octilinear(points)
    if points[0] != start[0] or points[-1] != end[0]:
        raise HTTPException(
            status_code=422,
            detail="Track route must begin and end exactly at its endpoint sockets/junctions",
        )
    if start[1:] == end[1:] and start[1:] != (None, None, None):
        raise HTTPException(status_code=422, detail="Track start and end must differ")
    for junction_id in (start[3], end[3]):
        if _hosted_by_chain(conn, junction_id, payload.id):
            raise HTTPException(status_code=422, detail="Track cannot attach to a junction on its own route")
    return points, start, end


def track_values(payload: TrackPayload, points: list[tuple[int, int]], start: tuple, end: tuple) -> tuple:
    return (
        payload.layout_id, payload.name, start[1], start[2], start[3], end[1], end[2], end[3],
        json.dumps([{"x": x, "y": y} for x, y in points]),
    )


def validate_junction(conn: Any, payload: JunctionPayload, existing_id: Optional[str] = None) -> None:
    row = conn.execute(
        "SELECT * FROM layout_track WHERE id = ? AND layout_id = ?", (payload.track_id, payload.layout_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Junction track not found in layout")
    if not point_on_route(route_tuples(track_response(row)["points"]), payload.x, payload.y):
        raise HTTPException(status_code=422, detail="Junction must lie on its track's route")
    if existing_id:
        old = conn.execute("SELECT * FROM layout_junction WHERE id = ?", (existing_id,)).fetchone()
        moved = old and (int(old["x"]), int(old["y"])) != (payload.x, payload.y)
        if old and (moved or old["track_id"] != payload.track_id) and attached_track_ids(conn, existing_id):
            raise HTTPException(
                status_code=409, detail="Junction has connected tracks; delete them before moving the junction"
            )


# ---------------------------------------------------------------------------------------------
# Cascades and consistency
# ---------------------------------------------------------------------------------------------

def attached_track_ids(conn: Any, junction_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM layout_track WHERE start_junction_id = ? OR end_junction_id = ?",
        (junction_id, junction_id),
    ).fetchall()
    return [row["id"] for row in rows]


def delete_tracks(conn: Any, track_ids: list[str]) -> tuple[list[str], list[str]]:
    """Delete tracks, the junctions on them, and tracks attached to those junctions (recursively)."""
    deleted_tracks: list[str] = []
    deleted_junctions: list[str] = []
    queue = list(track_ids)
    while queue:
        track_id = queue.pop()
        if track_id in deleted_tracks:
            continue
        deleted_tracks.append(track_id)
        for row in conn.execute("SELECT id FROM layout_junction WHERE track_id = ?", (track_id,)).fetchall():
            deleted_junctions.append(row["id"])
            queue.extend(attached_track_ids(conn, row["id"]))
    for junction_id in deleted_junctions:
        conn.execute("DELETE FROM layout_junction WHERE id = ?", (junction_id,))
    for track_id in deleted_tracks:
        conn.execute("DELETE FROM layout_track WHERE id = ?", (track_id,))
    return deleted_tracks, deleted_junctions


def delete_junction(conn: Any, junction_id: str) -> tuple[list[str], list[str]]:
    """Delete a junction and the tracks attached to it."""
    deleted_tracks, deleted_junctions = delete_tracks(conn, attached_track_ids(conn, junction_id))
    if junction_id not in deleted_junctions:
        conn.execute("DELETE FROM layout_junction WHERE id = ?", (junction_id,))
        deleted_junctions.append(junction_id)
    return deleted_tracks, deleted_junctions


def delete_tracks_for_berth(conn: Any, berth_id: str) -> None:
    rows = conn.execute(
        "SELECT id FROM layout_track WHERE start_berth_id = ? OR end_berth_id = ?", (berth_id, berth_id)
    ).fetchall()
    if rows:
        delete_tracks(conn, [row["id"] for row in rows])


def rename_berth_refs(conn: Any, old_id: str, new_id: str) -> None:
    for prefix in ("start", "end"):
        conn.execute(
            f"UPDATE layout_track SET {prefix}_berth_id = ? WHERE {prefix}_berth_id = ?", (new_id, old_id)
        )


def delete_layout_tracks(conn: Any, layout_id: str) -> None:
    conn.execute("DELETE FROM layout_junction WHERE layout_id = ?", (layout_id,))
    conn.execute("DELETE FROM layout_track WHERE layout_id = ?", (layout_id,))


def reproject_junctions(conn: Any, track_id: str, points: list[tuple[int, int]]) -> None:
    """Keep junctions on an edited track's route by moving stray ones to the nearest route point."""
    for row in conn.execute("SELECT * FROM layout_junction WHERE track_id = ?", (track_id,)).fetchall():
        x, y, dist = nearest_point_on_route(points, int(row["x"]), int(row["y"]))
        if dist:
            conn.execute("UPDATE layout_junction SET x = ?, y = ? WHERE id = ?", (x, y, row["id"]))
