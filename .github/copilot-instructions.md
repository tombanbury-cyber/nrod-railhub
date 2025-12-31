# Copilot Instructions for nrod-railhub

## Project Overview

`nrod-railhub` is a Python application that connects to Network Rail's open data feeds via STOMP to provide real-time UK rail monitoring. It combines three data sources:

- **VSTP** (Very Short Term Planning): Late-notice schedule changes
- **TRUST** (Train Running Under System TOPS): Real-time train movements
- **TD** (Train Describer): Signalling berth stepping data

The application enriches cryptic rail codes with human-readable station names and provides: 
- Live console output in departure-board format
- SQLite persistence for historical analysis
- Optional web dashboard for browsing tracked trains

## Architecture

### Core Components

1. **`Listener`** - STOMP message handler
   - Receives messages from three Network Rail topics
   - Updates `HumanView` state and `RailDB` persistence
   - Renders human-friendly console output

2. **`HumanView`** - In-memory state cache
   - Maintains indices: headcode→schedule, UID→TRUST state, (area,headcode)→TD state
   - Joins data from VSTP/TRUST/TD feeds
   - Renders departure-board style output

3. **`LocationResolver`** - TIPLOC/STANOX/CRS lookup
   - Downloads and caches CORPUS reference data
   - Maps rail codes to station names

4. **`SmartResolver`** - TD berth decoder
   - Downloads and caches SMART berth stepping data
   - Maps (TD area, berth) → (STANOX, platform, location name)

5. **`RailDB`** - SQLite persistence
   - Stores TD state/events, TRUST state, VSTP state
   - Enables historical queries and web dashboard

6. **Web Dashboard** - Flask app (optional)
   - Serves TD state, event history, per-train drill-down
   - Runs in background thread when `--web-port` is set

### Data Flow

```
Network Rail STOMP → Listener → HumanView (enrich) → Console + DB → Web Dashboard
```

## Key Rail Domain Concepts

- **Headcode**: 4-character train reporting number (e.g. `2C90`)
- **UID**: CIF train unique identifier (e.g. `C43876`)
- **TIPLOC**: Timing Point Location code (7-char, e.g. `CLPHMJC`)
- **STANOX**: Station Number (5-digit, e.g. `87701`)
- **CRS**: 3-letter station code (e. g. `WAT` for Waterloo)
- **TD Area**:  Signalling area code (2-char, e.g. `EK` for Eastleigh)
- **Berth**: Track circuit identifier within a TD area (e.g. `0152`)

## Development Guidelines

### Code Style

- Python 3.9+ with type hints on all public functions
- Use dataclasses for structured data (`@dataclass`)
- Thread safety:  use locks around shared state (`_lock`, `_print_lock`)
- Error handling: catch specific exceptions, log (don't crash receiver thread)

### Common Patterns

#### Enriching Location Data

When a new rail code needs resolving:

```python
# Resolver pattern (see LocationResolver, SmartResolver)
name = self. resolver.name_for_tiploc(code)
if name:
    display = f"{name} ({code})"
else:
    display = code  # fallback to raw code
```

#### Database Persistence

All DB operations must: 
1. Use `with self._lock, self._conn:` for thread safety
2. Handle exceptions without crashing (log first N errors)
3. Follow upsert pattern for state tables, insert for event tables

```python
def upsert_example(self, key:  str, value: str) -> None:
    with self._lock, self._conn:
        self._conn.execute(
            """
            INSERT INTO table(key, value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
```

#### Downloading Reference Data

Follow the redirect-safe pattern in `LocationResolver._download_corpus()`:
- Send `Authorization: Basic ...` only to `publicdatafeeds.networkrail.co.uk`
- **Never** send `Authorization` to S3 URLs (they use `X-Amz-*` query params)
- Handle gzip transparently (check `Content-Encoding` or magic bytes `\x1f\x8b`)
- Use atomic write:  `tmp_file` → `os.replace(tmp, final)`

### Testing

- Manual:  `python3 nrod_railhub.py --user USER --password PASS --headcode 2C90 --verbose`
- Unit tests: Not yet implemented (contributions welcome)
- Live testing:  Requires valid Network Rail credentials

## Command-Line Interface

### Essential Arguments

```bash
# Monitor specific train
--headcode 2C90          # Filter to headcode
--uid C43876             # Filter to train UID

# Credentials (required)
--user you@example.com
--password secret

# Persistence & Web UI
--db-path ~/rail. db      # Enable SQLite storage
--web-port 5555          # Start web dashboard (requires --db-path)

# Reference data
--corpus-refresh         # Re-download CORPUS
--smart-refresh          # Re-download SMART
--schedule-refresh       # Re-download daily timetable

# Output control
--verbose               # Show raw message preview
--width 120             # Console width (default 96)
--trace-headcode        # Debug filtered train visibility
--only-changes          # Only print when output changes (default)
--repeat-after 300      # Allow repeat after N seconds (default 300)
```

### Example Usage

```bash
# Basic monitoring
python3 nrod_railhub.py --user USER --password PASS

# Track specific train with web UI
python3 nrod_railhub.py --user USER --password PASS \
  --headcode 2C90 --db-path rail.db --web-port 8080

# Filter to TD area (e.g. Eastleigh)
python3 nrod_railhub.py --user USER --password PASS --td-area EK

# Verbose debug mode
python3 nrod_railhub.py --user USER --password PASS \
  --headcode 2C90 --verbose --trace-headcode
```

## Web Dashboard

When `--db-path` and `--web-port` are set, a Flask web server starts on `http://0.0.0.0:{port}`:

- **`/`** - Latest TD state (all trains or filtered by area)
- **`/train? area=XX&hc=YYYY`** - Drill-down for specific train
- **`/events`** - Recent TD events (last 500)

The dashboard uses raw HTML (no templates) for simplicity. Runs in daemon thread. 

## Known Issues & Future Work

- **Schedule loading**: Daily timetable is large (~300MB gzip); loads in background thread but can delay enrichment
- **SMART coverage**: Not all berths are in SMART; some fall back to raw `area:berth` display
- **Duplicate headcodes**: Same headcode can appear on different services; UID is more reliable for tracking
- **No automated tests**: pytest suite needed
- **Web UI**: Basic styling; could use Jinja2 templates and CSS framework

## Key Files

- `nrod_railhub.py` - Main application (all code in one file)
- `~/. cache/openraildata/` - Default cache directory for CORPUS/SMART/SCHEDULE
- `rail.db` - SQLite database (if `--db-path` is set)

## Dependencies

```bash
pip install stomp.py flask
```

- `stomp.py` - STOMP 1.1 client
- `flask` - Web dashboard (optional, only if `--web-port` is used)

## Resources

- [Network Rail Open Data Wiki](https://wiki.openraildata.com/)
- [STOMP Protocol Spec](https://stomp.github.io/stomp-specification-1.1.html)
- [stomp.py GitHub](https://github.com/jasonrbriggs/stomp. py)

## Contributing

When modifying this code:

1. **Preserve existing filters**: `--headcode`, `--uid`, `--td-area` must continue to work
2. **Thread safety**: Receiver thread must never crash; wrap DB/state updates in try/except
3. **Test with live data**: Network Rail data has edge cases; validate with real STOMP connection
4. **Update this file**: Document new CLI args, DB schema changes, or resolvers
