# nrod-railhub 🚂

Real-time UK rail monitoring using Network Rail's open data feeds.  Combines VSTP (schedules), TRUST (movements), and TD (signalling) data into human-readable departure-board style output.

## Features

- 📊 **Live train tracking** - Monitor trains by headcode or UID across multiple data sources
- 🗺️ **Location enrichment** - Converts TIPLOC/STANOX codes to station names
- 💾 **SQLite persistence** - Historical event storage and analysis
- 🌐 **Web dashboard** - Browser-based train tracking interface
- 🎯 **Smart filtering** - Track specific trains, areas, or view everything

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

# Filter to specific signalling area
python3 nrod_railhub.py --user USER --password PASS --td-area EK --td-area AD
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
| `--user` | Network Rail username (required) |
| `--password` | Network Rail password (required) |
| `--headcode XXXX` | Filter to specific headcode (e.g. 2C90) |
| `--uid XXXXX` | Filter to specific train UID |

### Output & Display

| Option | Description |
|--------|-------------|
| `--width N` | Console output width (default:  96) |
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
4. **Join** - Combines data sources by headcode/UID
5. **Display** - Renders unified view with delays, locations, and schedules

## Data Sources Explained

| Feed | Purpose | Example |
|------|---------|---------|
| **VSTP** | Late-notice schedule changes | New service added for engineering work |
| **TRUST** | Train activation, arrivals, departures | Train 2C90 departed Woking +3 min late |
| **TD** | Signaller's view of berth occupancy | Headcode 2C90 moved from berth 0152→0154 |

## Rail Domain Glossary

- **Headcode** - 4-character train identifier (e.g. `2C90`)
- **UID** - Unique train schedule identifier (e.g. `C43876`)
- **TIPLOC** - Timing Point Location code (e.g. `CLPHMJC` = Clapham Junction)
- **STANOX** - Station Number (e.g. `87701` = Clapham Junction)
- **CRS** - 3-letter station code (e.g. `WAT` = Waterloo)
- **TD Area** - Signalling control area (e.g. `EK` = Eastleigh, `AD` = Ashford)
- **Berth** - Track circuit identifier within TD area

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

- [Network Rail Open Data Feeds](https://publicdatafeeds.networkrail.co. uk/)
- [OpenRailData Wiki](https://wiki.openraildata.com/)
- [stomp.py](https://github.com/jasonrbriggs/stomp.py) by Jason R Briggs

## Support

- 🐛 **Issues:** https://github.com/tombanbury-cyber/nrod-railhub/issues
- 📖 **Wiki:** https://wiki.openraildata.com/
- 💬 **Discussions:** https://github.com/tombanbury-cyber/nrod-railhub/discussions
