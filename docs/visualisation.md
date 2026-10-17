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

## Corridor Cards (berth-sequence cards)

Open `http://127.0.0.1:8000/cards` to configure and view cards. Each card's
**selected, ordered berth sequence is its source of truth**; the from/to stations
only help discover and label candidate sequences.

### Setup flow

1. Enter from/to stations (name, CRS, STANOX or TIPLOC) and choose *Find candidate sequences*
   (`GET /api/cards/suggest?from_station=..&to_station=..`; nothing is saved).
2. Candidates are ranked with a method, confidence and per-berth provenance:
   - `td_observed_movements`: shortest paths through observed `td_berth_movements`
     (falling back to `td_berth_events`) from SMART berths of the first station to SMART berths
     of the second, within one TD area. Confidence is `n/(n+2)` for the least-observed step `n`
     (a heuristic, not a probability). Alternatives are found by excluding single steps of the best path.
   - `historical_route_pattern`: only used when no observed path exists; berths/links not seen in
     TD movements are marked `inferred`.
   - If nothing is found, no sequence is invented and notes explain why. The count of CIF schedules
     calling at both stations in order (`schedule_support`) is shown as supporting context only;
     schedules are not mapped to berths.
3. Pick a sequence, choose display options (stations, berth labels, occupancy, signals) and save.

### Configuration storage

Cards are stored under `visualisation_cards:` in the shared YAML (`nrod.yaml` in the project root, or
the path in `NROD_RAILHUB_CONFIG`). Saving rewrites only that key and keeps other settings (written
atomically, file permissions preserved; the API never returns other settings). PyYAML does not keep
comments, so comments in the file are lost on save; an unreadable YAML file is never overwritten.
When `NROD_RAILHUB_DB` is unset, the SQLite `db_path` from the same YAML is used (else `railhub.db`);
`db_provider: postgres` is still unsupported by this service.

### API

`GET/POST /api/cards`, `GET/PUT/DELETE /api/cards/{id}`, and `GET /api/cards/{id}/state`.

### Manual editing

After choosing a candidate (or opening a saved card), berths can be reordered or removed. You can
also import a train's observed chain (`GET /api/cards/chain?td_area=..&headcode=..`, all items
`observed`), or add a berth by hand (always marked `inferred`, source `manual`). Edited sequences are
saved with method `manual_edit:<original>`. This is the fallback when SMART/CORPUS data is missing.
If `td_berth_movements` is sparse, backfill it with `RailDB.rebuild_td_berth_movements()` or let the
listener run longer.

### Live view

The page polls `/api/cards/{id}/state` every 5 seconds. It reads persisted `td_state` (the listener's
writes), so it does not depend on `POST /event`/WebSocket. The response includes a `revision`
(max `td_berth_events` id, max `td_state` time and row count); passing `?since_revision=<rev>` returns
`{"unchanged": true}` when nothing changed, and the page does a full refresh at least every 60 s so
staleness ages stay current. A train occupies the berth in `to_berth` (else `from_berth`); each train
reports `age_s`. Trains older than `max_age_s` (default 1800, `0` disables) are hidden and counted;
trains outside the sequence are only counted. Solid borders are observed, dashed amber are inferred.

### Security

If `NROD_RAILHUB_VIS_TOKEN` is set, card create/update/delete require a bearer token in the `Authorization` header
(the page prompts for it). Unset means open, as before; the service still allows any CORS origin, so
bind it to localhost. Card saves take an advisory file lock (`<config>.lock`, POSIX) as well as the
in-process lock.

### Remaining limitations and possible resolutions

- **Single TD area per sequence**: needs cross-area links (boundary berth map, or headcodes seen in two
  areas close in time) and a search over `(area, berth)` nodes.
- **Schedules don't rank routes**: map CIF/VSTP stops (TIPLOC -> STANOX) to SMART berth events and
  score candidates by matching scheduled services.
- **Heuristic confidence**: `n/(n+2)` on the least-seen step; alternatives avoid one step only. Weight
  by recency/direction, use k-shortest paths, filter by SMART line, calibrate on held-out movements.
- **Inferred gaps** come only from `headcode_route_patterns`; run `rebuild_route_patterns` regularly or
  use `topology_edges`/S-class evidence.
- **Occupancy** comes from `td_state` (not per-berth events), so cancels/clears are not modelled;
  derive from the latest event per berth instead.
- **Signals**: only reviewed mappings are listed; no live aspects until bit-to-aspect decoding in
  `td_sclass_state`/`td_signal_bytes` is confirmed.
- **Storage**: PyYAML drops comments (use `ruamel.yaml` or a DB table); SQLite only (port to
  `db_backend.connect`); the main CLI ignores the `visualisation_cards` key.
- **Push updates**: still polling; a listener-to-WebSocket/SSE publisher would remove the lag.
- **UI**: no schematic rendering of candidates, no browser tests.
- Station/platform labels need SMART (and CORPUS) data; otherwise raw berth ids are shown.

## Quick Start

### 1. Initialize the Database

By default, the visualisation API uses the project-root `railhub.db`:

```bash
cd /path/to/nrod-railhub
sqlite3 railhub.db < sql/init_db.sql
```

To run it against your main NROD RailHub SQLite database instead, point the
API at that file with `NROD_RAILHUB_DB`.

Before applying the PoC schema to an existing main database, make a backup:

```bash
cp /path/to/main.sqlite /path/to/main.sqlite.backup
```

Then apply the schema to that existing main database:

```bash
cd /path/to/nrod-railhub
sqlite3 /path/to/main.sqlite < sql/init_db.sql
```

`sql/init_db.sql` uses `CREATE TABLE IF NOT EXISTS`, so it can add the PoC
tables to an existing main database without replacing the existing NROD
RailHub tables.

It also inserts a demo layout with 8 berths and one train if they do not
already exist.

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
# From project root, using the default project-root railhub.db
python3 -m uvicorn app.visualisation.app:app --reload --host 127.0.0.1 --port 8000
```

To run against the main NROD RailHub SQLite database instead:

```bash
NROD_RAILHUB_DB=/path/to/main.sqlite \
python3 -m uvicorn app.visualisation.app:app --reload --host 127.0.0.1 --port 8000
```

Or use the helper script:

```bash
./scripts/run_demo.sh
```

Do **not** run `./scripts/run_demo.sh` against your production or main
database. The demo workflow is intended for the PoC database and can remove
and recreate the demo database file.

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

#### `GET /admin`
Open the browser-based CRUD interface for layout, berth, and signal rows.
The admin page also includes a searchable headcode list, berth-chain viewer,
and selective berth import controls for importing chain items into a layout.

#### `POST /api/layouts`
Create a layout. `PUT /api/layouts/{layout_id}` updates it, and
`DELETE /api/layouts/{layout_id}` removes the layout plus its berths/signals.

#### `POST /api/berths`
Create a berth. `PUT /api/berths/{berth_id}` updates it and
`DELETE /api/berths/{berth_id}` removes it.

#### `POST /api/berths/import-chain`
Import selected berth IDs from a displayed headcode chain into a layout. The
endpoint creates layout-scoped berth IDs using `{layout_id}:{berth_id}` and
skips berths already present on the target layout.

#### `POST /api/signals`
Create a signal. `PUT /api/signals/{signal_id}` updates it and
`DELETE /api/signals/{signal_id}` removes it.

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
Build a journey chain for a train from event records. Direct berth enter/exit
observations are returned when available, and historical route inference may
insert additional chain items when a headcode has a stronger known berth path.

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
      "exit_time": "2026-02-14T10:01:00Z",
      "inferred": false,
      "confidence": 1.0,
      "source": "observed",
      "reason": "Observed berth enter/exit events"
    },
    {
      "berth_id": "BRTH_2",
      "enter_time": null,
      "exit_time": null,
      "inferred": true,
      "confidence": 0.92,
      "source": "historical_route_pattern",
      "reason": "Inferred between BRTH_1 and BRTH_3 using historical headcode 2C90 transitions"
    },
    {
      "berth_id": "BRTH_3",
      "enter_time": "2026-02-14T10:01:01Z",
      "exit_time": null,
      "inferred": false,
      "confidence": 1.0,
      "source": "observed",
      "reason": "Observed berth enter event"
    }
  ]
}
```

#### `GET /state`
Return the current schematic snapshot. The API reads `td_state` first, then
falls back to `td_berth_events`, and finally the PoC `event` table if needed.
This is the endpoint the demo UI polls to rebuild the current view after a
page reload.

**Example:**
```bash
curl http://127.0.0.1:8000/state?area=EK
```

#### `GET /td/{td_area}/{headcode}/chain`
Return a TD-backed movement chain for one train in one TD area.

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
8. **Configuration**: Browser CRUD interface for creating/editing layouts, berths, and signals
9. **Authentication**: Secure access to the API

## Troubleshooting

### Database not found
Make sure you've initialized the database:
```bash
sqlite3 railhub.db < sql/init_db.sql
```

If you are using the main NROD RailHub database, also make sure
`NROD_RAILHUB_DB` points to that SQLite file before starting the API.

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

## Shared Main Database Limitations

Using `NROD_RAILHUB_DB` lets the visualisation API share the same SQLite file
as the main application. The visualisation now reads live TD state from
`td_state` and `td_berth_events`, while still supporting the PoC `event`
table for demo/manual injection.

The schematic UI still uses the PoC layout tables (`layout`, `berth`, `signal`,
`train`) for the demo geometry, but the live movement data comes from the main
RailHub TD tables when they are available.

## Contributing

This PoC is designed to be extended. When adding features:

1. Update the database schema in `sql/init_db.sql`
2. Add new API endpoints in `app/visualisation/app.py`
3. Update the UI in `web/static/ui.html`
4. Document changes in this file

## License

Same as NROD RailHub main project.
