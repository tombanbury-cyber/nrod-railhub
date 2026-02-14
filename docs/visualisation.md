# Schematic Visualization PoC

This is a proof-of-concept (PoC) for real-time train schematic visualization in NROD RailHub. It demonstrates how to display train movements on a track layout using WebSockets for live updates.

## Overview

The visualization PoC consists of:

- **SQLite Database**: Stores layouts, berths, signals, trains, and events
- **FastAPI Backend**: REST API and WebSocket server for real-time updates
- **Static Web UI**: SVG-based visualization that shows train positions and berth occupancy

## Architecture

```
┌─────────────────┐     HTTP/WebSocket     ┌──────────────────┐
│   Web Browser   │ <──────────────────> │  FastAPI Server   │
│   (ui.html)     │                        │   (app.py)       │
└─────────────────┘                        └──────────────────┘
                                                    │
                                                    │ SQLite
                                                    ▼
                                           ┌──────────────────┐
                                           │   railhub.db     │
                                           │  - layout        │
                                           │  - berth         │
                                           │  - signal        │
                                           │  - train         │
                                           │  - event         │
                                           └──────────────────┘
```

## Quick Start

### 1. Initialize the Database

```bash
cd /path/to/nrod-railhub
sqlite3 railhub.db < sql/init_db.sql
```

This creates the schema and inserts a demo layout with 8 berths and one train.

### 2. Install Dependencies

```bash
pip install fastapi uvicorn[standard] websockets
```

Or use the existing requirements.txt:

```bash
pip install -r requirements.txt
```

### 3. Run the Server

```bash
# From project root
python3 -m uvicorn app.visualisation.app:app --reload --host 127.0.0.1 --port 8000
```

Or use the helper script:

```bash
./scripts/run_demo.sh
```

### 4. Open the UI

Open your browser to:
```
http://127.0.0.1:8000/static/ui.html
```

## API Endpoints

### REST Endpoints

#### `GET /layout/{layout_id}`
Get layout information.

**Example:**
```bash
curl http://127.0.0.1:8000/layout/demo
```

**Response:**
```json
{
  "id": "demo",
  "name": "Demo Station",
  "description": "Simple demonstration layout with one platform",
  "data": {"version": "1.0", "type": "station"},
  "created_at": "2026-02-14T10:00:00.000Z"
}
```

#### `GET /berths/{layout_id}`
Get all berths for a layout.

**Example:**
```bash
curl http://127.0.0.1:8000/berths/demo
```

**Response:**
```json
[
  {
    "id": "BRTH_1",
    "layout_id": "demo",
    "name": "A1",
    "x": 50,
    "y": 100,
    "width": 60,
    "height": 30,
    "berth_type": "platform"
  },
  ...
]
```

#### `GET /trains`
Get all trains.

**Example:**
```bash
curl http://127.0.0.1:8000/trains
```

**Response:**
```json
[
  {
    "id": "T1",
    "headcode": "2C90",
    "description": "Demo Train Service",
    "toc": "GW",
    "created_at": "2026-02-14T10:00:00.000Z"
  }
]
```

#### `GET /train/{train_id}/chain`
Build a journey chain for a train from event records.

**Example:**
```bash
curl http://127.0.0.1:8000/train/T1/chain
```

**Response:**
```json
{
  "train_id": "T1",
  "chain": [
    {
      "berth_id": "BRTH_1",
      "enter_time": "2026-02-14T10:00:00Z",
      "exit_time": "2026-02-14T10:01:00Z"
    },
    {
      "berth_id": "BRTH_2",
      "enter_time": "2026-02-14T10:01:01Z",
      "exit_time": null
    }
  ]
}
```

#### `POST /event`
Create a new event and broadcast to WebSocket clients.

**Request Body:**
```json
{
  "ts": "2026-02-14T10:00:00Z",
  "source": "td",
  "train_id": "T1",
  "event_type": "berth_enter",
  "object_id": "BRTH_1",
  "payload": {}
}
```

**Example:**
```bash
curl -X POST http://127.0.0.1:8000/event \
  -H 'Content-Type: application/json' \
  -d '{"ts":"2026-02-14T10:00:00Z","source":"td","train_id":"T1","event_type":"berth_enter","object_id":"BRTH_1","payload":{}}'
```

**Response:**
```json
{
  "id": 1,
  "status": "created"
}
```

### WebSocket Endpoint

#### `WS /ws`
WebSocket connection for real-time event updates.

**Connection:**
```javascript
const ws = new WebSocket('ws://127.0.0.1:8000/ws');

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.type === 'event') {
    console.log('Event:', message.data);
  }
};
```

**Event Message Format:**
```json
{
  "type": "event",
  "data": {
    "id": 1,
    "ts": "2026-02-14T10:00:00Z",
    "source": "td",
    "train_id": "T1",
    "event_type": "berth_enter",
    "object_id": "BRTH_1",
    "payload": {}
  }
}
```

## Event Types

- `berth_enter`: Train enters a berth
- `berth_exit`: Train exits a berth
- `signal_on`: Signal turns on (future use)
- `signal_off`: Signal turns off (future use)

## Testing

### Manual Testing via UI

The web UI includes test buttons to send events:
- Click "Enter BRTH_X" to simulate a train entering a berth
- Click "Exit BRTH_X" to simulate a train exiting a berth
- Click "Run Full Sequence" to see a train move through multiple berths
- Click "Clear All" to reset all berth occupancy

### Manual Testing via curl

Enter a berth:
```bash
curl -X POST http://127.0.0.1:8000/event \
  -H 'Content-Type: application/json' \
  -d '{"ts":"2026-02-14T10:00:00Z","source":"td","train_id":"T1","event_type":"berth_enter","object_id":"BRTH_1","payload":{}}'
```

Exit a berth:
```bash
curl -X POST http://127.0.0.1:8000/event \
  -H 'Content-Type: application/json' \
  -d '{"ts":"2026-02-14T10:01:00Z","source":"td","train_id":"T1","event_type":"berth_exit","object_id":"BRTH_1","payload":{}}'
```

## Database Schema

### `layout` table
Stores schematic layout configurations.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PRIMARY KEY | Unique layout identifier |
| name | TEXT NOT NULL | Human-readable name |
| description | TEXT | Description of the layout |
| data | TEXT | JSON metadata |
| created_at | TEXT | Creation timestamp |

### `berth` table
Defines berth positions on layouts.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PRIMARY KEY | Unique berth identifier |
| layout_id | TEXT | Foreign key to layout |
| name | TEXT NOT NULL | Display name (e.g., "A1") |
| x | INTEGER | X coordinate in pixels |
| y | INTEGER | Y coordinate in pixels |
| width | INTEGER | Width in pixels |
| height | INTEGER | Height in pixels |
| berth_type | TEXT | Type: normal, platform, siding |

### `signal` table
Defines signal positions on layouts.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PRIMARY KEY | Unique signal identifier |
| layout_id | TEXT | Foreign key to layout |
| name | TEXT NOT NULL | Display name |
| x | INTEGER | X coordinate in pixels |
| y | INTEGER | Y coordinate in pixels |
| signal_type | TEXT | Type: auto, controlled, shunt |

### `train` table
Stores train information.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PRIMARY KEY | Unique train identifier |
| headcode | TEXT | Train reporting number |
| description | TEXT | Service description |
| toc | TEXT | Train Operating Company |
| created_at | TEXT | Creation timestamp |

### `event` table
Logs all berth and signal events.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-incrementing ID |
| ts | TEXT NOT NULL | Event timestamp (ISO8601) |
| source | TEXT NOT NULL | Event source (td, trust, manual) |
| train_id | TEXT | Foreign key to train |
| event_type | TEXT NOT NULL | Event type |
| object_id | TEXT NOT NULL | Berth or signal ID |
| payload | TEXT | Additional JSON data |
| created_at | TEXT | Creation timestamp |

## UI Features

- **Real-time Updates**: WebSocket connection shows events as they happen
- **Visual Feedback**: 
  - Empty berths: Green boxes
  - Occupied berths: Yellow boxes
  - Train positions: Red circles above berths
- **Event Log**: Shows recent events with timestamps
- **Manual Controls**: Test buttons to simulate train movements
- **Connection Status**: Visual indicator for WebSocket connection

## Future Enhancements

This is a minimal PoC. Potential improvements:

1. **Multiple Trains**: Track multiple trains simultaneously
2. **Complex Layouts**: Support multi-track, junction, and station layouts
3. **Signal Integration**: Show signal states and aspects
4. **Historical Playback**: Replay past events
5. **Integration**: Connect to live TD/TRUST data from NROD feeds
6. **Performance**: Optimize for large layouts with many berths
7. **Styling**: Enhanced visual design with CSS animations
8. **Configuration**: UI for creating/editing layouts
9. **Authentication**: Secure access to the API

## Troubleshooting

### Database not found
Make sure you've initialized the database:
```bash
sqlite3 railhub.db < sql/init_db.sql
```

### WebSocket connection failed
- Check that the server is running on port 8000
- Ensure no firewall is blocking the connection
- Check browser console for error messages

### Events not appearing
- Verify WebSocket is connected (green indicator)
- Check the Network tab in browser DevTools
- Verify events are being inserted into the database:
  ```bash
  sqlite3 railhub.db "SELECT * FROM event ORDER BY id DESC LIMIT 10;"
  ```

## Contributing

This PoC is designed to be extended. When adding features:

1. Update the database schema in `sql/init_db.sql`
2. Add new API endpoints in `app/visualisation/app.py`
3. Update the UI in `web/static/ui.html`
4. Document changes in this file

## License

Same as NROD RailHub main project.
