# nrod-railhub Architecture

## Overview

This document provides technical details about the system architecture, data flows, and design decisions.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Network Rail Data Feeds                       │
│  publicdatafeeds.networkrail.co.uk:61618 (STOMP 1.1, plain TCP) │
└────────┬─────────────────┬─────────────────┬────────────────────┘
         │                 │                 │
    VSTP_ALL         TRAIN_MVT_ALL      TD_ALL_SIG_AREA
   (schedules)        (movements)        (signalling)
         │                 │                 │
         └─────────────────┴─────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Listener  │  (STOMP client, stomp.py)
                    │  Thread-safe│
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
        ┌─────▼──────┐           ┌─────▼──────┐
        │ HumanView  │           │   RailDB   │
        │ (in-memory)│◄──────────┤  (SQLite)  │
        └─────┬──────┘           └─────┬──────┘
              │                         │
    ┌─────────┼─────────┐               │
    │         │         │               │
┌───▼───┐ ┌──▼──┐  ┌───▼────┐    ┌─────▼──────┐
│Console│ │Location│ │ Smart  │    │   Flask    │
│Output │ │Resolver│ │Resolver│    │   Web UI   │
└───────┘ └────────┘ └────────┘    └────────────┘
```

## Component Details

### 1. STOMP Connection Layer

**Technology:** `stomp.py` library, STOMP 1.1 protocol

**Connection Parameters:**
- Host: `publicdatafeeds.networkrail.co.uk`
- Port: `61618` (plain TCP, **not** TLS)
- Virtual host: Same as hostname (ActiveMQ Artemis requirement)
- Protocol: STOMP 1.1 with heartbeats (10s/10s)
- Authentication: HTTP Basic Auth (username/password)

**Topics:**
- `/topic/VSTP_ALL` - Very Short Term Planning (late schedule changes)
- `/topic/TRAIN_MVT_ALL_TOC` - TRUST train movements (all TOCs)
- `/topic/TD_ALL_SIG_AREA` - Train Describer data (all signalling areas)

**Design Decisions:**
- Use `Connection11` for STOMP 1.1 features (header escaping, version negotiation)
- Enable keepalive and heartbeats to detect connection loss
- Set `reconnect_attempts_max=5` to handle transient network issues
- Explicit `vhost` header required for Artemis broker

### 2. HumanView (In-Memory State)

**Purpose:** Join VSTP/TRUST/TD data by headcode and UID

**Data Structures:**

```python
# VSTP indices
vstp_by_uid_date: Dict[(uid, start_date), VstpSchedule]
vstp_by_headcode:  Dict[headcode, VstpSchedule]

# TRUST indices  
trust_by_train_id: Dict[train_id, TrustState]
trust_by_uid: Dict[uid, TrustState]
trust_by_headcode: Dict[headcode, TrustState]

# TD indices
td_by_headcode: Dict[(area, headcode), TdState]

# Cross-reference
headcode_by_uid: Dict[uid, headcode]
```

**Why in-memory?**
- Low latency rendering (< 1ms lookup)
- Simple joins without SQL complexity
- Natural fit for streaming data
- Memory footprint reasonable (~10MB for full day of trains)

**Limitations:**
- State lost on restart (mitigated by SQLite persistence)
- No historical queries (use RailDB for that)

### 3. RailDB (SQLite Persistence)

**Schema:**

```sql
-- Current TD state (one row per area+headcode)
CREATE TABLE td_state (
    td_area TEXT,
    headcode TEXT,
    last_time_utc TEXT,
    from_berth TEXT,
    to_berth TEXT,
    stanox TEXT,               -- Enriched via SMART
    location_name TEXT,         -- Enriched via CORPUS
    platform TEXT,              -- Enriched via SMART
    sched_dep TEXT,             -- Enriched via SCHEDULE/VSTP
    sched_arr TEXT,
    origin_name TEXT,
    dest_name TEXT,
    uid TEXT,
    PRIMARY KEY (td_area, headcode)
);

-- TD event history (append-only log)
CREATE TABLE td_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    td_area TEXT,
    headcode TEXT,
    event_type TEXT NOT NULL,
    from_berth TEXT,
    to_berth TEXT,
    raw_json TEXT
);

-- TRUST current state
CREATE TABLE trust_state (
    train_id TEXT PRIMARY KEY,
    headcode TEXT,
    uid TEXT,
    toc_id TEXT,
    last_event_time TEXT,
    last_location TEXT,
    last_delay_min INTEGER,
    raw_json TEXT
);

-- VSTP schedule cache
CREATE TABLE vstp_state (
    uid TEXT,
    headcode TEXT,
    start_date TEXT,
    end_date TEXT,
    raw_json TEXT,
    PRIMARY KEY (uid, start_date)
);
```

**WAL Mode:** Enabled for concurrent reads during writes

**Design Decisions:**
- `td_state`: UPSERT pattern, always current state
- `td_event`: INSERT only, append-only log for analysis
- Store `raw_json` for debugging and future schema evolution
- Enriched fields (location_name, platform) pre-computed for fast web queries

**Growth Rate:** ~1MB/day per active TD area (depends on traffic)

### 4. Location Resolution

#### CORPUS (TIPLOC/STANOX/CRS → Name)

**Source:** Network Rail reference data extract (JSON)

**Download Flow:**
1. GET `https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate? type=CORPUS` with Basic Auth
2. Receive 302 redirect to S3 pre-signed URL
3. GET S3 URL **without** Authorization header (S3 uses query auth)
4. Decompress gzip if needed
5. Cache to `~/.cache/openraildata/CORPUSExtract. json`

**Format:** Either `[{...}, {...}]` or `{"TIPLOCDATA": [{...}]}`

**Key Fields:**
- `TIPLOC` - 7-char code (e.g. `CLPHMJC`)
- `STANOX` - 5-digit code (e.g. `87701`)
- `3ALPHA` - CRS code (e.g. `CLJ`)
- `NLCDESC` - Human name (e.g. `CLAPHAM JUNCTION`)

#### SMART (TD Berth → STANOX/Platform)

**Source:** Berth stepping reference data

**Purpose:** Map `(TD_area, berth)` → `(STANOX, platform, location_name)`

**Download Flow:** Same as CORPUS but `type=SMART`

**Format:** `[{"TD":  "EK", "FROMBERTH": "0152", "TOBERTH": "0154", "STANOX": "12345", "PLATFORM": "2", ... }]`

**Coverage:** Not all berths are in SMART; gaps exist especially in: 
- Sidings and yards
- Text berths (4-letter codes like `PLAT`)
- Newly commissioned signalling

### 5. Schedule Resolution

#### Daily SCHEDULE Extract (ITPS Timetable)

**Source:** Full CIF schedule (JSON lines, gzipped)

**Download:** `CifFileAuthenticate? type=CIF_ALL_FULL_DAILY&day=toc-full`

**Size:** ~300MB compressed, ~2GB uncompressed

**Format:** One JSON object per line: 
```json
{"JsonScheduleV1": {"CIF_train_uid": "C12345", "schedule_segment": {... }}}
{"JsonScheduleV1": {"CIF_train_uid": "C12346", ... }}
```

**Loading Strategy:**
- Background thread (non-blocking for TD/TRUST streaming)
- Filter by service date and days_run (only load relevant schedules)
- Prefer by STP indicator:  C (Cancel) < O (Overlay) < P (Permanent)
- Falls back to VSTP if SCHEDULE not loaded

**Performance:** ~30s to parse and filter on typical hardware

### 6. Console Output Rendering

**Format:** Departure-board style

```
15:18  2C90  12:51→13:58  Woking → London Waterloo   +3m
      Last:  Clapham Junction (87701) plat 13
```

**Rendering Pipeline:**
1. Lookup VSTP/SCHEDULE for origin/dest/times
2. Lookup TRUST for delay and last location
3. Lookup TD for berth position
4. Resolve codes via CORPUS/SMART
5. Format with padding and truncation (respects `--width`)

**Deduplication:**
- Track last printed output per `(area, headcode)`
- Only print if output changed OR `--repeat-after` seconds elapsed
- Controlled by `--only-changes` flag

### 7. Web Dashboard

**Technology:** Flask (single-threaded, daemon thread)

**Routes:**
- `GET /` - TD state table with area filter
- `GET /train? area=XX&hc=YYYY` - Train detail view
- `GET /events` - Recent TD events

**Why Flask?**
- Lightweight (no heavyweight framework needed)
- Standard library in most Python environments
- Easy to extend with templates later

**Concurrency:** SQLite with WAL mode allows concurrent reads while receiver thread writes

## Data Flow:  Message to Console

```
1. STOMP message arrives
   ↓
2. Listener. on_message() parses JSON
   ↓
3. Route by message type:
   - VSTP → HumanView. upsert_vstp()
   - TRUST → HumanView.upsert_trust()
   - TD → HumanView.upsert_td()
   ↓
4. Update in-memory indices
   ↓
5. Check filter (--headcode, --uid, --td-area)
   ↓
6. Render output via HumanView. render_for_*()
   ↓
7.  Enrich with CORPUS/SMART lookups
   ↓
8. Check deduplication (last output + timestamp)
   ↓
9. Print to console
   ↓
10. (Optional) Persist to RailDB
```

**Latency:** Typically < 10ms from message receipt to console output

## Threading Model

```
Main Thread
  ├─ STOMP receiver thread (stomp.py managed)
  │    └─ Calls Listener.on_message() (synchronized via locks)
  ├─ Status ticker thread (daemon, every 15s)
  ├─ Schedule loader thread (daemon, runs once at startup)
  └─ Flask web server thread (daemon, if --web-port set)
```

**Synchronization:**
- `RailDB._lock` - Protects SQLite connection
- `Listener._print_lock` - Protects deduplication state
- `HumanView` - No lock (single writer thread, multiple readers safe)

**Why no HumanView lock?**
- Only receiver thread writes to HumanView
- Render methods are read-only
- Python GIL provides sufficient safety for dict reads during writes

## Design Decisions & Tradeoffs

### Why not use Django/FastAPI? 
- **Overkill:** This is a streaming CLI tool, not a web app
- **Complexity:** Extra dependencies, configuration overhead
- **Startup time:** Flask is instant, Django takes seconds

### Why SQLite instead of PostgreSQL?
- **Single user:** No need for client-server architecture
- **Portability:** Self-contained database file
- **Performance:** WAL mode provides excellent concurrent read/write
- **Simplicity:** No separate database server to manage

### Why single-file architecture?
- **Deployment:** Single `nrod_railhub.py` file easy to copy/run
- **Simplicity:** No package structure needed for ~1500 LOC
- **Tradeoff:** Could split into modules at ~2000 LOC

### Why no ORM?
- **Performance:** Direct SQL faster for append-heavy workload
- **Simplicity:** No abstraction layer to learn
- **Flexibility:** Raw SQL easier to optimize

### Why cache reference data?
- **CORPUS/SMART change rarely** (monthly at most)
- **Large files** (~50MB+ each)
- **Respect Network Rail bandwidth**
- **Faster startup** (no download delay)

## Performance Characteristics

| Operation | Latency | Throughput |
|-----------|---------|------------|
| STOMP message receipt | < 5ms | ~1000 msg/s |
| HumanView lookup | < 1ms | N/A |
| CORPUS/SMART resolve | < 1ms | N/A |
| Console render | < 10ms | N/A |
| SQLite insert (TD event) | 1-5ms | ~500 inserts/s |
| SQLite upsert (TD state) | 2-10ms | ~200 upserts/s |

**Bottlenecks:**
- Schedule loading (30s startup delay) - mitigated by background thread
- SQLite writes during high traffic - mitigated by WAL mode and batching

## Security Considerations

### Credentials
- Stored in command-line args (visible in `ps` output)
- **Recommendation:** Use environment variables or config file
- Network Rail credentials are low-privilege (read-only data feeds)

### SQL Injection
- All queries use parameterized statements (`?` placeholders)
- No user input directly interpolated into SQL

### STOMP Connection
- Plain TCP (no TLS) - Network Rail limitation
- Credentials sent in plain text
- **Mitigation:** Network Rail data is public anyway

### Web Dashboard
- No authentication (runs on localhost by default)
- **Recommendation:** Use reverse proxy (nginx) with auth if exposing publicly
- No CSRF protection (read-only endpoints)

## Future Improvements

### Short Term
- [ ] Environment variable support for credentials
- [ ] Configuration file (YAML/TOML)
- [ ] Graceful shutdown (flush SQLite, close connections)
- [ ] Signal handling (SIGTERM, SIGINT)

### Medium Term
- [ ] Split into modules (`nrod_railhub/listener.py`, etc.)
- [ ] Add pytest test suite
- [ ] CI/CD with GitHub Actions
- [ ] Docker container

### Long Term
- [ ] WebSocket real-time updates for web dashboard
- [ ] Historical analysis tools (delay statistics, punctuality)
- [ ] Multiple database backends (PostgreSQL, InfluxDB)
- [ ] Distributed deployment (message queue, worker pool)

## References

- [Network Rail Open Data Wiki](https://wiki.openraildata.com/)
- [STOMP 1.1 Specification](https://stomp.github.io/stomp-specification-1.1.html)
- [SQLite WAL Mode](https://www.sqlite.org/wal. html)
- [Network Rail Feed Format Reference](https://wiki.openraildata.com/index.php/Train_Movements)
