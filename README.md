# nrod-railhub 🚂

Real-time UK rail monitoring using Network Rail's open data feeds.  Combines VSTP (schedules), TRUST (movements), and TD (signalling) data into human-readable departure-board style output.

## Features

- 📊 **Live train tracking** - Monitor trains by headcode or UID across multiple data sources
- 🗺️ **Location enrichment** - Converts TIPLOC/STANOX codes to station names
- 💾 **SQLite persistence** - Historical event storage and analysis
- 🌐 **Web dashboard** - Browser-based train tracking interface
- 🎯 **Smart filtering** - Track specific trains, areas, or view everything
- 🖥️ **Interactive mode** - Real-time curses-based terminal dashboard

## Quick Start

### Prerequisites

- Python 3.9+
- Network Rail Data Feeds account (free): https://publicdatafeeds.networkrail.co.uk/

### Installation

```bash
# Clone repository
git clone https://github.com/tombanbury-cyber/nrod-railhub. git
cd nrod-railhub

# Install dependencies
pip install stomp.py flask

# Run (replace with your credentials)
python3 nrod_railhub.py --user your. email@example.com --password yourpassword
```

### Basic Usage

```bash
# Monitor a specific train
python3 nrod_railhub.py --user USER --password PASS --headcode 2C90

# Track all trains with web dashboard
python3 nrod_railhub.py --user USER --password PASS --db-path rail.db --web-port 8080

# Run in interactive mode with real-time terminal dashboard
python3 nrod_railhub.py --user USER --password PASS --interactive

# Filter to specific signalling area in interactive mode
python3 nrod_railhub.py --user USER --password PASS --td-area EK --interactive
```

## Configuration File

As an alternative to passing command-line arguments, you can use a YAML configuration file. This is especially useful for persistent settings like credentials and cache paths.

### Creating a Configuration File

Copy the sample configuration:

```bash
cp config.sample.yaml config.yaml
```

Edit `config.yaml` with your credentials and preferences:

```yaml
# Network Rail credentials
user: your_email@example.com
password: your_password

# Display options
width: 120
log_level: info

# Filtering
headcode: 2C90  # Optional: monitor specific train
```

### Using the Configuration File

```bash
# Use config file (command-line args still override config values)
python3 nrod_railhub.py --config config.yaml

# Override specific config values via command line
python3 nrod_railhub.py --config config.yaml --headcode 1A23 --width 150
```

**Note:** Command-line arguments always take precedence over configuration file values, allowing flexible overrides.

For a complete list of all available configuration options with descriptions, see [config.sample.yaml](config.sample.yaml).

## Interactive Mode

The `--interactive` flag launches a curses-based real-time terminal dashboard that displays:

- **Connection status** - Live STOMP connection state
- **Message rates** - Real-time message throughput
- **Console output** - Scrolling train movement updates
- **Filters** - Active headcode, UID, and area filters

### Interactive Mode Controls

| Key | Action |
|-----|--------|
| `q` | Quit the application |
| `p` | Pause/resume updates |
| `c` | Clear console output |

### Example Interactive Mode

```bash
# Monitor specific train interactively
python3 nrod_railhub.py --user USER --password PASS --headcode 2C90 --interactive

# Monitor multiple TD areas interactively
python3 nrod_railhub.py --user USER --password PASS --td-area EK --td-area AD --interactive
```

## Example Output

```
15:18  2C90  12:51→13:58  Woking → London Waterloo   +3m
      Last:  Clapham Junction (87701) plat 13

15:19  1A23  14:30→16:45  London Paddington → Bristol Temple Meads   On time
      Last: Reading (RDG)
```

## Command-Line Options

### Essential

| Option | Description |
|--------|-------------|
| `--config PATH` | Path to YAML configuration file (command-line args override config) |
| `--user` | Network Rail username (required) |
| `--password` | Network Rail password (required) |
| `--headcode XXXX` | Filter to specific headcode (e.g. 2C90) |
| `--uid XXXXX` | Filter to specific train UID |

### Output & Display

| Option | Description |
|--------|-------------|
| `--interactive` | Run in interactive curses mode with real-time dashboard |
| `--width N` | Console output width (default:  96) |
| `--log-level LEVEL` | Set log level: verbose, info, warning, error (default: error) |
| `--verbose` | Show raw message previews |
| `--trace-headcode` | Debug filtered train visibility |
| `--no-only-changes` | Print even when output unchanged |
| `--repeat-after N` | Allow repeating output after N seconds (default: 300) |

### Persistence & Web

| Option | Description |
|--------|-------------|
| `--db-path PATH` | SQLite database file path |
| `--web-port PORT` | Start web dashboard on this port (requires --db-path) |

### Data Sources

| Option | Description |
|--------|-------------|
| `--td-area XX` | Filter to TD area(s), repeatable |
| `--corpus-cache PATH` | CORPUS reference data cache location |
| `--corpus-refresh` | Force re-download CORPUS |
| `--smart-cache PATH` | SMART berth data cache location |
| `--smart-refresh` | Force re-download SMART |
| `--schedule-cache PATH` | Daily timetable cache location |
| `--schedule-refresh` | Force re-download schedule |
| `--no-schedule` | Disable timetable enrichment |

## Web Dashboard

When `--web-port` is set, open `http://localhost: PORT` in your browser:

- **Home** - Latest TD state for all tracked trains
- **Filter by area** - Click area pill to filter (e.g. EK, AD, WR)
- **Train detail** - Click headcode for event history
- **Events** - Recent TD berth movements
- **Mapper** - Configure and rebuild berth-signal correlation mappings

### Mapper Configuration

The Mapper page allows you to adjust the parameters used for correlating berth step movements with signal events:

- **pre_ms**: Milliseconds to look back before a berth step event (default: 1000)
- **post_ms**: Milliseconds to look forward after a berth step event (default: 5000)
- **tau_ms**: Time constant for exponential weighting (default: 2500)

You can rebuild the correlation scores with new parameters without re-collecting data. The rebuild processes existing observations and regenerates the `berth_signal_scores` table.

## Architecture

```
Network Rail STOMP Feeds
  ├─ VSTP_ALL (schedule changes)
  ├─ TRAIN_MVT_ALL_TOC (movements)
  └─ TD_ALL_SIG_AREA (signalling)
         ↓
    Listener (stomp.py)
         ↓
    HumanView (in-memory cache)
         ↓
    ┌────────────┬──────────────┐
    ↓            ↓              ↓
 Console    RailDB (SQLite)  Web Dashboard
```

## How It Works

1. **Connect** - Establishes STOMP connection to Network Rail's broker
2. **Subscribe** - Listens to VSTP, TRUST, and TD topics
3. **Enrich** - Downloads CORPUS (station names) and SMART (berth locations)
   - Automatically handles double-encoded JSON from Network Rail API
   - Caches reference data locally for performance
   - Refreshable via `--corpus-refresh` and `--smart-refresh` flags
4. **Join** - Combines data sources by headcode/UID
5. **Display** - Renders unified view with delays, locations, and schedules

## Data Sources Explained

| Feed | Purpose | Example |
|------|---------|---------|
| **VSTP** | Late-notice schedule changes | New service added for engineering work |
| **TRUST** | Train activation, arrivals, departures | Train 2C90 departed Woking +3 min late |
| **TD** | Signaller's view of berth occupancy | Headcode 2C90 moved from berth 0152→0154 |
| **CORPUS** | Station/location reference data | Maps STANOX 87701 to "Clapham Junction" |
| **SMART** | Berth stepping reference data | Maps TD area EK berth 0152 to Gillingham platform 1 |

### Reference Data Updates

Reference data (CORPUS and SMART) can be refreshed periodically:

```bash
# Manual refresh
python3 nrod_railhub.py --user USER --password PASS --corpus-refresh --smart-refresh

# Automated refresh service (runs every 24 hours)
python3 -m nrod_railhub.services.ref_import_service
```

**Note:** Network Rail's SMART data may be double-encoded (JSON string within JSON). The application automatically detects and handles this format transparently.

## Rail Domain Glossary

- **Headcode** - 4-character train identifier (e.g. `2C90`)
- **UID** - Unique train schedule identifier (e.g. `C43876`)
- **TIPLOC** - Timing Point Location code (e.g. `CLPHMJC` = Clapham Junction)
- **STANOX** - Station Number (e.g. `87701` = Clapham Junction)
- **CRS** - 3-letter station code (e.g. `WAT` = Waterloo)
- **TD Area** - Signalling control area (e.g. `EK` = East Kent, `AD` = Ashford)
- **Berth** - Track circuit identifier within TD area
- **SMART** - Signalling Maintenance Analysis and Renewal Tool (berth reference data)
- **CORPUS** - Corporate Reference System (location reference data)

## Troubleshooting

### Connection Issues

```
[2025-12-31T15:30:00Z] CONNECT FAILED 2:  ConnectionError
```

**Solutions:**
- Check Network Rail credentials at https://publicdatafeeds.networkrail.co.uk/
- Verify firewall allows outbound port 61618
- Check Network Rail service status

### Missing Location Names

```
15:18  2C90  CLPHMJC → WATRLMN
```

**Solutions:**
- Run with `--corpus-refresh` to download latest reference data
- Some locations may not be in CORPUS (rare)

### Empty Output

**Possible causes:**
- Filtered headcode not running today
- TD area filter too restrictive
- Trains not yet activated (try again later in the day)

## Performance Tips

- Use `--headcode` or `--uid` filters to reduce processing
- Set `--no-schedule` for faster startup (disables timetable enrichment)
- Limit `--td-area` to areas you care about
- SQLite database grows ~1MB/day per active area

## Enhanced Berth Resolution

### TD Area Names
The application includes a comprehensive TD area code mapping (200+ areas) based on the [Network Rail Open Data Wiki](https://wiki.openraildata.com/index.php/List_of_Train_Describers). Examples:
- `EK` = East Kent (Gillingham)
- `ER` = Eastleigh
- `AD` = Ashford
- `WL` = Waterloo

### Inferred Berth Data
When SMART reference data doesn't include a berth, the system automatically falls back to **inferred berth-signal mappings** derived from historical TD events. This:
- Uses the `berth_signal_scores` table populated by the mapper
- Correlates berth change events (CA) with signal events (SF)
- Provides STANOX and location name even for berths not in official SMART data
- Marked with `event="INFERRED"` to distinguish from official SMART entries

**To enable inferred berth fallback:**
```bash
# Requires --db-path to be set (for database access)
python3 nrod_railhub.py --user USER --password PASS --db-path rail.db --enable-mapper
```

The mapper processes TD events in real-time to build statistical correlations between berth steps and signal addresses.

## Contributing

Contributions welcome! Areas needing help: 

- [ ] Automated tests (pytest)
- [ ] Web dashboard styling (CSS framework)
- [ ] Additional reference data sources (e.g. BPLAN)
- [ ] Performance optimization for large databases
- [ ] Docker container

See `copilot-instructions.md` for development guidelines.

## License

MIT License - see LICENSE file

## Acknowledgements

- [Network Rail Open Data Feeds](https://publicdatafeeds.networkrail.co.uk/)
- [OpenRailData Wiki](https://wiki.openraildata.com/)
- [stomp.py](https://github.com/jasonrbriggs/stomp.py) by Jason R Briggs

## Support

- 🐛 **Issues:** https://github.com/tombanbury-cyber/nrod-railhub/issues
- 📖 **Wiki:** https://wiki.openraildata.com/
- 💬 **Discussions:** https://github.com/tombanbury-cyber/nrod-railhub/discussions
