# Importing NROD reference data

This repository includes helpers to import Network Rail reference files (CORPUS + SMART) and convert shapefiles to SQLite. The following files were added to help integrate the scripts already present in import_scripts/.

## Overview

The import system handles two main reference datasets:

- **CORPUS**: Location reference data (TIPLOCs, STANOX codes, CRS codes, station names)
- **SMART**: Train describer berth stepping data (TD areas, berth movements, signal associations)

Both datasets are downloaded from Network Rail's Open Data platform and stored in a SQLite database for efficient querying.

## Quickstart

1. Install dependencies

   python3 -m pip install -r requirements.txt

2. Initialize database schema

   make init-db

   (This runs scripts/init_nrod_db.py which applies database/nrod.sqlite.sql to create nrod.sqlite)

3. Import reference JSON extracts (example uses included json/ files)

   make import-ref

## Automated Imports

For automated imports from the Network Rail Open Data endpoints, set credentials via environment variables or CI secrets:

- NROD_USERNAME
- NROD_PASSWORD

See import_scripts/nrod_ref_import.py for options (`--no-download` to import local files).

### Continuous Import Service

The repository includes a background service that automatically refreshes reference data on a configurable interval (default: 24 hours):

```bash
# Using environment variables
export NR_USERNAME="your-email@example.com"
export NR_PASSWORD="your-password"
export DB_PATH="nrod_ref.sqlite"
export REF_IMPORT_INTERVAL="86400"  # 24 hours in seconds
export DATASETS="CORPUS,SMART"

# Run the service
python3 -m nrod_railhub.services.ref_import_service
```

The service will:
- Run an initial import immediately on startup
- Download and import fresh data at the specified interval
- Log import statistics and handle errors gracefully
- Respond to SIGINT/SIGTERM for clean shutdown

## SMART Data Handling

### Double-Encoded JSON

Network Rail's SMART reference data is sometimes provided as double-encoded JSON (a JSON string within JSON), particularly when downloaded directly from their API. This is a known quirk for legacy compatibility reasons.

The import system automatically detects and handles double-encoded JSON:

```python
# Example of double-encoded JSON:
# "{\"TD\":\"SC\",\"FROMBERTH\":\"531X\",\"TOBERTH\":\"532X\",...}"

# The import system will:
# 1. Parse the outer JSON layer (yields a string)
# 2. Detect that the result is a string
# 3. Parse the string again as JSON to get the actual data
```

This handling is built into:
- `import_scripts/nrod_ref_import.py` - The `read_json_file()` function
- `nrod_railhub/resolvers.py` - Both `SmartResolver` and `LocationResolver` classes

### Database Schema

SMART data is stored in the `smart_steps` table with the following key fields:

- `td` - Train Describer area (2-char code, e.g., "EK" for East Kent)
- `from_berth` / `to_berth` - Berth identifiers for train movements
- `stanox` - Station Number reference
- `stanme` - Station name
- `platform` - Platform identifier
- `event` - Event type (e.g., "A" for arrival, "D" for departure)
- `step_type` - Step classification (e.g., "B" for berth)

The database includes indexed views for efficient lookups:
- `v_berths` - Unique list of all berths
- `v_smart_steps_with_location` - Joined view with CORPUS location data

### SmartResolver

The `SmartResolver` class provides programmatic access to SMART data:

```python
from nrod_railhub.resolvers import SmartResolver

# Load from file
resolver = SmartResolver(db_path="nrod_ref.sqlite")
resolver.load_or_download(
    username="your-email",
    password="your-password",
    cache_path="~/.cache/openraildata/SMART.json",
    force=False,  # Set True to re-download
)

# Lookup berth information
berth_info = resolver.lookup(td_area="EK", berth="0152")
if berth_info:
    print(f"STANOX: {berth_info['stanox']}")
    print(f"Station: {berth_info['stanme']}")
    print(f"Platform: {berth_info['platform']}")
```

The resolver supports fallback to inferred berth mappings from the database when SMART data is unavailable.

## Notes

- Do not commit credentials or secrets. Use environment variables or CI secrets.
- Large downloads should be kept outside the repo. The importer records metadata in the DB for downloaded files.
- SMART data is refreshed occasionally by Network Rail. Use the continuous import service or periodic manual imports to stay current.
- The double-encoding detection is transparent and backwards-compatible with normally-encoded JSON files.
