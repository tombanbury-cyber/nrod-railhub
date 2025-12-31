
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial documentation suite (README, CONTRIBUTING, ARCHITECTURE, DEPLOYMENT)

### Changed
- TBD

### Fixed
- TBD

## [0.3.0] - 2025-12-31

### Added
- **SQLite persistence** via `RailDB` class
  - `td_state`, `td_event`, `trust_state`, `vstp_state` tables
  - WAL mode for concurrent read/write performance
  - `--db-path` CLI argument to enable persistence
- **Web dashboard** (Flask)
  - TD state table with area filtering
  - Per-train detail view with event history
  - Recent events view (last 500)
  - `--web-port` CLI argument to enable web server
- **SMART berth resolver** (`SmartResolver`)
  - Maps (TD area, berth) → (STANOX, platform, location name)
  - `--smart-cache` and `--smart-refresh` CLI arguments
  - Enriches TD output with platform and station names
- **SCHEDULE (ITPS) timetable loading**
  - Downloads and parses daily CIF schedule extract
  - Fills in origin/dest/times when VSTP not available
  - Background thread loading (non-blocking startup)
  - `--schedule-cache`, `--schedule-refresh`, `--no-schedule` CLI arguments
- **Timetable enrichment** in console output
  - Shows planned departure/arrival times
  - Shows origin/destination station names
  - Displays "On time" / "+Xm" delay status
- **Location name resolution**
  - TIPLOC → station name via CORPUS
  - STANOX → station name via CORPUS
  - Berth → STANOX/platform via SMART
- **Deduplication** of console output
  - `--only-changes` flag (default: enabled)
  - `--repeat-after N` to allow repeats after N seconds

### Changed
- **Refactored rendering** into `render_for_td()` and `render_for_headcode()`
- **Improved TD message handling**
  - Now unwraps `CA_MSG`, `CC_MSG`, `SF_MSG`, etc.  wrappers
  - Better extraction of area/headcode/berth fields
- **Enhanced TRUST/VSTP linking**
  - Cross-reference UID ↔ headcode for better join accuracy
  - Infer headcode from VSTP when TRUST doesn't provide it
- **Console output format**
  - Departure-board style:  `HH:MM  XXXX  dep→arr  Origin → Dest  +Xm`
  - Second line shows: `Last:  Location (STANOX) plat X`
- **Status ticker** now shows per-topic message counts

### Fixed
- CORPUS/SMART download now correctly handles S3 redirects
  - Don't send `Authorization` header to S3 (causes 400 errors)
  - Properly detect gzip encoding (header + magic bytes)
- Thread-safe database operations with proper locking
- TD berth text codes (4-letter) no longer cause lookup errors

## [0.2.0] - 2025-12-15

### Added
- **CORPUS reference data** (`LocationResolver`)
  - Downloads and caches TIPLOC/STANOX/CRS mappings
  - `--corpus-cache` and `--corpus-refresh` CLI arguments
- **TD feed support**
  - Subscribe to `/topic/TD_ALL_SIG_AREA`
  - Parse train describer berth stepping messages
  - Store TD state in `HumanView. td_by_headcode`
- **TD area filtering**
  - `--td-area` CLI argument (repeatable)
  - Only show console output for specified areas
- **Trace mode** for debugging
  - `--trace-headcode` shows when VSTP/TRUST/TD mention filtered train
  - Helps diagnose visibility issues
- **Status ticker thread**
  - Prints connection status every 15s (configurable with `--status-every`)
  - Shows message counts per topic

### Changed
- **Improved STOMP connection reliability**
  - Set explicit `vhost` header for ActiveMQ Artemis compatibility
  - Enable heartbeats (10s/10s) to detect connection loss
  - Set `reconnect_attempts_max=5` for automatic reconnection
- **Better message routing**
  - Unified `on_message()` handler for all topics
  - Separate upsert methods for VSTP/TRUST/TD
- **Rendering now joins TD with VSTP/TRUST**
  - Shows berth movements alongside schedule/delay info

### Fixed
- STOMP connection now uses `Connection11` for proper STOMP 1.1 support
- Handle both wrapped and unwrapped JSON message formats
- Graceful handling of missing fields in TRUST/VSTP messages

## [0.1.0] - 2025-12-01

### Added
- **Initial release** 🎉
- **STOMP connection** to Network Rail Data Feeds
  - Plain TCP to `publicdatafeeds.networkrail. co.uk:61618`
  - STOMP 1.1 protocol with Basic Auth
- **VSTP feed support**
  - Subscribe to `/topic/VSTP_ALL`
  - Parse Very Short Term Planning schedule changes
  - Store schedules by UID and headcode
- **TRUST feed support**
  - Subscribe to `/topic/TRAIN_MVT_ALL_TOC`
  - Parse train activation, movement, cancellation messages
  - Track train state (activated, cancelled, delay, location)
- **Headcode filtering**
  - `--headcode` CLI argument to track specific train
  - `--uid` CLI argument to track by CIF train UID
- **Console output**
  - Human-readable train status updates
  - Timestamp + headcode + basic schedule info
- **CLI arguments**
  - `--user`, `--password` for Network Rail credentials
  - `--host`, `--port`, `--vhost` for custom STOMP broker
  - `--verbose` for raw message preview
  - `--width` for output width control
- **In-memory state management**
  - `VstpSchedule` dataclass for schedules
  - `TrustState` dataclass for train movements
  - `HumanView` class for joining data sources

### Technical
- Python 3.9+ with type hints
- Dependencies: `stomp. py` (STOMP client)
- Single-file architecture (~1000 LOC)
- Thread-safe message handling

## [0.0.1] - 2025-11-15

### Added
- Initial project structure
- Basic STOMP connection test
- Proof of concept:  receive raw VSTP messages

---

## Version Number Scheme

- **Major (X.0.0)**: Breaking changes, major architecture overhaul
- **Minor (0.X.0)**: New features, backward-compatible
- **Patch (0.0.X)**: Bug fixes, documentation updates

## Upgrade Notes

### 0.2.0 → 0.3.0

- **Database:** New `--db-path` argument enables persistence (optional, backward compatible)
- **Web UI:** New `--web-port` argument starts Flask server (optional)
- **Reference data:** SMART download added (automatic, uses `~/.cache/openraildata/SMART. json`)
- **Performance:** Schedule loading runs in background thread (startup faster)
- **No breaking changes** - all 0.2.0 CLI arguments still work

### 0.1.0 → 0.2.0

- **CORPUS required:** Application now downloads CORPUS reference data on first run
- **TD feed:** New TD messages appear in console output (no action needed)
- **CLI arguments:** New optional arguments (`--td-area`, `--trace-headcode`, `--corpus-cache`)
- **No breaking changes** - 0.1.0 CLI usage still works

## Roadmap

### 0.4.0 (Planned)
- [ ] Automated tests (pytest)
- [ ] Configuration file support (YAML/TOML)
- [ ] Environment variable support for credentials
- [ ] Graceful shutdown handling
- [ ] Web UI:  Jinja2 templates and CSS styling
- [ ] Web UI: Real-time updates (WebSocket or SSE)

### 0.5.0 (Planned)
- [ ] Multi-headcode dashboard view
- [ ] Historical analysis tools (delay statistics)
- [ ] Export functionality (CSV, JSON, GPX)
- [ ] Email/SMS alerts for specific trains
- [ ] Performance optimization (batched DB writes)

### 1.0.0 (Future)
- [ ] Stable API
- [ ] Comprehensive test coverage (>80%)
- [ ] Production-ready deployment (Docker, systemd)
- [ ] Full documentation (Sphinx)
- [ ] Performance benchmarks
- [ ] Security audit

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to propose changes.

When adding an entry: 
1. Add to `[Unreleased]` section under appropriate heading (Added/Changed/Fixed/Removed)
2. Use present tense ("Add feature" not "Added feature")
3. Link to relevant issues/PRs if applicable
4. Include migration notes if breaking change

## Links

- [Repository](https://github.com/tombanbury-cyber/nrod-railhub)
- [Issue Tracker](https://github.com/tombanbury-cyber/nrod-railhub/issues)
- [Network Rail Open Data](https://publicdatafeeds.networkrail.co.uk/)

[Unreleased]: https://github.com/tombanbury-cyber/nrod-railhub/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/tombanbury-cyber/nrod-railhub/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tombanbury-cyber/nrod-railhub/compare/v0.1.0... v0.2.0
[0.1.0]: https://github.com/tombanbury-cyber/nrod-railhub/compare/v0.0.1...v0.1.0
[0.0.1]:  https://github.com/tombanbury-cyber/nrod-railhub/releases/tag/v0.0.1
