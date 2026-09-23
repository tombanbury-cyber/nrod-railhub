"""FastAPI backend for schematic visualization PoC.

This module provides a minimal REST API and WebSocket server for real-time
train visualization on schematic layouts.
"""

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .route_inference import infer_train_chain


# Database path (relative to project root)
DB_PATH = Path(__file__).parent.parent.parent / "railhub.db"

# Static files path
STATIC_PATH = Path(__file__).parent.parent.parent / "web" / "static"


class EventCreate(BaseModel):
    """Model for creating new events via POST /event."""
    ts: str
    source: str
    train_id: Optional[str] = None
    event_type: str
    object_id: str
    payload: dict = {}


class ConnectionManager:
    """Manages WebSocket connections and broadcasts events."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients."""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        
        # Clean up dead connections
        for conn in dead_connections:
            self.disconnect(conn)


# Initialize FastAPI app
app = FastAPI(
    title="NROD RailHub Visualization API",
    description="Real-time train schematic visualization",
    version="0.1.0"
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
manager = ConnectionManager()


@contextmanager
def get_conn():
    """Get database connection with row factory."""
    db_path = Path(os.environ.get("NROD_RAILHUB_DB", str(DB_PATH)))
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _format_event_row(row: sqlite3.Row, source: str) -> dict[str, Any]:
    data = _row_dict(row)
    data["source"] = source
    data["berth_id"] = data.get("to_berth") or data.get("from_berth") or data.get("object_id")
    data["train_ref"] = data.get("headcode") or data.get("train_id")
    data["display_ts"] = data.get("ts_iso") or data.get("ts")
    return data


def _fetch_td_snapshot(
    conn: sqlite3.Connection,
    td_area: str | None = None,
    headcode: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Read current schematic state from TD tables, falling back to PoC events."""
    snapshot: dict[str, Any] = {
        "source": "empty",
        "td_area": td_area,
        "headcode": headcode,
        "updated_at": None,
        "trains": [],
        "events": [],
    }

    try:
        if _table_exists(conn, "td_state"):
            clauses = []
            params: list[Any] = []
            if td_area:
                clauses.append("td_area = ?")
                params.append(td_area)
            if headcode:
                clauses.append("headcode = ?")
                params.append(headcode)

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth,
                       stanox, location_name, platform, uid
                FROM td_state
                {where}
                ORDER BY last_time_ms DESC, td_area, headcode
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["source"] = "td_state"
                snapshot["trains"] = [
                    {
                        "marker_id": f"{row['td_area']}:{row['headcode']}",
                        "td_area": row["td_area"],
                        "headcode": row["headcode"],
                        "uid": row["uid"],
                        "current_berth": row["to_berth"] or row["from_berth"],
                        "from_berth": row["from_berth"],
                        "to_berth": row["to_berth"],
                        "last_time_ms": row["last_time_ms"],
                        "last_time_iso": row["last_time_iso"],
                        "stanox": row["stanox"],
                        "location_name": row["location_name"],
                        "platform": row["platform"],
                    }
                    for row in rows
                    if (row["to_berth"] or row["from_berth"])
                ]
                snapshot["updated_at"] = rows[0]["last_time_iso"] if rows else None

        if _table_exists(conn, "td_berth_events"):
            clauses = []
            params = []
            if td_area:
                clauses.append("td_area = ?")
                params.append(td_area)
            if headcode:
                clauses.append("headcode = ?")
                params.append(headcode)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT id, ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
                FROM td_berth_events
                {where}
                ORDER BY ts_ms DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["events"] = [
                    _format_event_row(row, "td_berth_events")
                    for row in rows
                ]
                if snapshot["source"] == "empty":
                    latest_by_key: dict[tuple[str | None, str | None], sqlite3.Row] = {}
                    for row in rows:
                        key = (row["td_area"], row["headcode"])
                        if key not in latest_by_key:
                            latest_by_key[key] = row
                    snapshot["source"] = "td_berth_events"
                    snapshot["trains"] = [
                        {
                            "marker_id": f"{row['td_area']}:{row['headcode']}",
                            "td_area": row["td_area"],
                            "headcode": row["headcode"],
                            "current_berth": row["to_berth"] or row["from_berth"],
                            "from_berth": row["from_berth"],
                            "to_berth": row["to_berth"],
                            "last_time_ms": row["ts_ms"],
                            "last_time_iso": row["ts_iso"],
                            "description": row["descr"],
                        }
                        for row in latest_by_key.values()
                        if row["headcode"] and (row["to_berth"] or row["from_berth"])
                    ]
                    snapshot["updated_at"] = rows[0]["ts_iso"] if rows else None

        if not snapshot["trains"] and _table_exists(conn, "event"):
            clauses = []
            params = []
            if headcode:
                clauses.append("COALESCE(t.headcode, e.train_id) = ?")
                params.append(headcode)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT e.id, e.ts, e.source, e.train_id, e.event_type, e.object_id, e.payload, t.headcode
                FROM event e
                LEFT JOIN train t ON t.id = e.train_id
                {where}
                ORDER BY e.ts DESC, e.id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            if rows:
                snapshot["source"] = "event"
                snapshot["events"] = [
                    _format_event_row(row, "event")
                    for row in rows
                ]
                active_by_train: dict[str, dict[str, Any]] = {}
                for row in reversed(rows):
                    train_key = row["train_id"] or row["headcode"] or row["object_id"]
                    if not train_key:
                        continue
                    event_type = row["event_type"]
                    if event_type == "berth_enter":
                        active_by_train[train_key] = {
                            "marker_id": train_key,
                            "td_area": td_area or "",
                            "headcode": row["headcode"] or row["train_id"] or "",
                            "train_id": row["train_id"],
                            "current_berth": row["object_id"],
                            "from_berth": None,
                            "to_berth": row["object_id"],
                            "last_time_iso": row["ts"],
                            "last_time_ms": None,
                        }
                    elif event_type == "berth_exit":
                        current = active_by_train.get(train_key)
                        if current and current["current_berth"] == row["object_id"]:
                            active_by_train.pop(train_key, None)
                snapshot["trains"] = list(active_by_train.values())
                snapshot["updated_at"] = rows[0]["ts"] if rows else None

        snapshot["train_count"] = len(snapshot["trains"])
        snapshot["event_count"] = len(snapshot["events"])
        return snapshot
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


def _fetch_td_chain(
    conn: sqlite3.Connection,
    td_area: str,
    headcode: str,
) -> dict[str, Any]:
    """Return a detailed TD movement chain for a single headcode."""
    try:
        if not _table_exists(conn, "td_berth_events"):
            return {"td_area": td_area, "headcode": headcode, "chain": []}

        rows = conn.execute(
            """
            SELECT ts_ms, ts_iso, td_area, headcode, msg_type, from_berth, to_berth, descr
            FROM td_berth_events
            WHERE td_area = ? AND headcode = ?
            ORDER BY ts_ms ASC, id ASC
            """,
            (td_area, headcode),
        ).fetchall()
        chain = []
        for row in rows:
            berth_id = row["to_berth"] or row["from_berth"]
            if not berth_id:
                continue
            chain.append(
                {
                    "berth_id": berth_id,
                    "enter_time": row["ts_iso"],
                    "exit_time": None,
                    "inferred": False,
                    "confidence": 1.0,
                    "source": "td_berth_events",
                    "reason": f"TD {row['msg_type']} event from {row['from_berth'] or 'unknown'} to {row['to_berth'] or 'unknown'}",
                    "td_area": row["td_area"],
                    "headcode": row["headcode"],
                    "from_berth": row["from_berth"],
                    "to_berth": row["to_berth"],
                    "msg_type": row["msg_type"],
                    "ts_ms": row["ts_ms"],
                }
            )

        if not chain and _table_exists(conn, "td_state"):
            row = conn.execute(
                """
                SELECT td_area, headcode, last_time_ms, last_time_iso, from_berth, to_berth,
                       stanox, location_name, platform
                FROM td_state
                WHERE td_area = ? AND headcode = ?
                ORDER BY last_time_ms DESC
                LIMIT 1
                """,
                (td_area, headcode),
            ).fetchone()
            if row and (row["to_berth"] or row["from_berth"]):
                chain.append(
                    {
                        "berth_id": row["to_berth"] or row["from_berth"],
                        "enter_time": row["last_time_iso"],
                        "exit_time": None,
                        "inferred": False,
                        "confidence": 1.0,
                        "source": "td_state",
                        "reason": "Current TD state snapshot",
                        "td_area": row["td_area"],
                        "headcode": row["headcode"],
                        "from_berth": row["from_berth"],
                        "to_berth": row["to_berth"],
                        "last_time_ms": row["last_time_ms"],
                    }
                )

        return {"td_area": td_area, "headcode": headcode, "chain": chain}
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "NROD RailHub Visualization API",
        "endpoints": {
            "layout": "/layout/{layout_id}",
            "berths": "/berths/{layout_id}",
            "trains": "/trains",
            "train_chain": "/train/{train_id}/chain",
            "state": "/state",
            "td_chain": "/td/{td_area}/{headcode}/chain",
            "event": "POST /event",
            "websocket": "/ws"
        }
    }


@app.get("/layout/{layout_id}")
async def get_layout(layout_id: str):
    """Get layout by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM layout WHERE id = ?",
            (layout_id,)
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Layout not found")
        
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "data": json.loads(row["data"]) if row["data"] else {},
            "created_at": row["created_at"]
        }


@app.get("/berths/{layout_id}")
async def get_berths(layout_id: str):
    """Get all berths for a layout."""
    with get_conn() as conn:
        # First check if layout exists
        layout = conn.execute(
            "SELECT id FROM layout WHERE id = ?",
            (layout_id,)
        ).fetchone()
        
        if not layout:
            raise HTTPException(status_code=404, detail="Layout not found")
        
        rows = conn.execute(
            "SELECT * FROM berth WHERE layout_id = ? ORDER BY name",
            (layout_id,)
        ).fetchall()
        
        return [
            {
                "id": row["id"],
                "layout_id": row["layout_id"],
                "name": row["name"],
                "x": row["x"],
                "y": row["y"],
                "width": row["width"],
                "height": row["height"],
                "berth_type": row["berth_type"]
            }
            for row in rows
        ]


@app.get("/signals/{layout_id}")
async def get_signals(layout_id: str):
    """Get all signals for a layout."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signal WHERE layout_id = ? ORDER BY name",
            (layout_id,)
        ).fetchall()
        
        return [
            {
                "id": row["id"],
                "layout_id": row["layout_id"],
                "name": row["name"],
                "x": row["x"],
                "y": row["y"],
                "signal_type": row["signal_type"]
            }
            for row in rows
        ]


@app.get("/trains")
async def get_trains():
    """Get all trains."""
    with get_conn() as conn:
        try:
            if _table_exists(conn, "train"):
                rows = conn.execute(
                    "SELECT * FROM train ORDER BY created_at DESC"
                ).fetchall()
                if rows:
                    return [
                        {
                            "id": row["id"],
                            "headcode": row["headcode"],
                            "description": row["description"],
                            "toc": row["toc"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                    ]

            if _table_exists(conn, "td_state"):
                rows = conn.execute(
                    """
                    SELECT td_area, headcode, last_time_iso, from_berth, to_berth, location_name, platform
                    FROM td_state
                    ORDER BY last_time_ms DESC, td_area, headcode
                    """
                ).fetchall()
                return [
                    {
                        "id": f"{row['td_area']}:{row['headcode']}",
                        "headcode": row["headcode"],
                        "description": row["location_name"] or row["to_berth"] or row["from_berth"],
                        "toc": row["platform"],
                        "created_at": row["last_time_iso"],
                        "td_area": row["td_area"],
                        "current_berth": row["to_berth"] or row["from_berth"],
                    }
                    for row in rows
                    if row["headcode"]
                ]
            return []
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/train/{train_id}/chain")
async def get_train_chain(train_id: str, td_area: str | None = None, headcode: str | None = None):
    """Build train journey chain from events.
    
    Returns a list of berth occupancy periods ordered by time.
    Each chain item contains direct observations plus inference metadata.
    """
    with get_conn() as conn:
        try:
            result = infer_train_chain(conn, train_id)
            if result.get("chain"):
                return result
        except sqlite3.OperationalError:
            result = None

        td_headcode = headcode or train_id
        if td_area:
            return _fetch_td_chain(conn, td_area=td_area, headcode=td_headcode)

        row = conn.execute(
            "SELECT td_area FROM td_state WHERE headcode = ? ORDER BY last_time_ms DESC LIMIT 1",
            (td_headcode,),
        ).fetchone() if _table_exists(conn, "td_state") else None
        if row:
            return _fetch_td_chain(conn, td_area=row["td_area"], headcode=td_headcode)

        if result is not None:
            return result
        return {"train_id": train_id, "chain": []}


@app.get("/state")
async def get_state(td_area: str | None = None, headcode: str | None = None, area: str | None = None, hc: str | None = None):
    """Get the current schematic snapshot from TD or PoC tables."""
    td_area = td_area or area
    headcode = headcode or hc
    with get_conn() as conn:
        try:
            return _fetch_td_snapshot(conn, td_area=td_area, headcode=headcode)
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/td/{td_area}/{headcode}/chain")
async def get_td_chain(td_area: str, headcode: str):
    """Build a TD-backed berth chain for a headcode within one TD area."""
    with get_conn() as conn:
        try:
            return _fetch_td_chain(conn, td_area=td_area, headcode=headcode)
        except sqlite3.OperationalError as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.post("/event")
async def create_event(event: EventCreate):
    """Create a new event and broadcast to WebSocket clients."""
    try:
        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO event (ts, source, train_id, event_type, object_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts,
                    event.source,
                    event.train_id,
                    event.event_type,
                    event.object_id,
                    json.dumps(event.payload)
                )
            )
            conn.commit()
            event_id = cursor.lastrowid
        
        # Broadcast to WebSocket clients
        broadcast_data = {
            "type": "event",
            "data": {
                "id": event_id,
                "ts": event.ts,
                "source": event.source,
                "train_id": event.train_id,
                "event_type": event.event_type,
                "object_id": event.object_id,
                "payload": event.payload
            }
        }
        await manager.broadcast(broadcast_data)
        
        return {"id": event_id, "status": "created"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event updates."""
    await manager.connect(websocket)
    try:
        # Keep connection alive and handle ping/pong
        while True:
            # Wait for any message from client (ping/pong)
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0
                )
                # Echo back as keepalive
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_json({"type": "keepalive"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# Mount static files last to avoid conflicts
if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
