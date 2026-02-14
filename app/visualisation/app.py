"""FastAPI backend for schematic visualization PoC.

This module provides a minimal REST API and WebSocket server for real-time
train visualization on schematic layouts.
"""

import json
import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from contextlib import contextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


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
        rows = conn.execute(
            "SELECT * FROM train ORDER BY created_at DESC"
        ).fetchall()
        
        return [
            {
                "id": row["id"],
                "headcode": row["headcode"],
                "description": row["description"],
                "toc": row["toc"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]


@app.get("/train/{train_id}/chain")
async def get_train_chain(train_id: str):
    """Build train journey chain from events.
    
    Returns a list of berth occupancy periods ordered by time.
    Each chain item contains: berth_id, enter_time, exit_time (if exited).
    """
    with get_conn() as conn:
        # Get all events for this train, ordered by time
        rows = conn.execute(
            """
            SELECT * FROM event 
            WHERE train_id = ? AND event_type IN ('berth_enter', 'berth_exit')
            ORDER BY ts
            """,
            (train_id,)
        ).fetchall()
        
        # Build chain by pairing enter/exit events
        chain = []
        berth_states = {}  # berth_id -> enter_event
        
        for row in rows:
            event_type = row["event_type"]
            berth_id = row["object_id"]
            ts = row["ts"]
            
            if event_type == "berth_enter":
                # Start tracking this berth
                berth_states[berth_id] = {
                    "berth_id": berth_id,
                    "enter_time": ts,
                    "exit_time": None
                }
            elif event_type == "berth_exit":
                # Complete the berth occupancy
                if berth_id in berth_states:
                    berth_states[berth_id]["exit_time"] = ts
                    chain.append(berth_states[berth_id])
                    del berth_states[berth_id]
        
        # Add any berths still occupied (no exit event yet)
        for state in berth_states.values():
            chain.append(state)
        
        return {
            "train_id": train_id,
            "chain": chain
        }


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
