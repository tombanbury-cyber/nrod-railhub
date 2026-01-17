# Importing NROD reference data

This repository includes helpers to import Network Rail reference files (CORPUS + SMART) and convert shapefiles to SQLite. The following files were added to help integrate the scripts already present in import_scripts/.

Quickstart:

1. Install dependencies

   python3 -m pip install -r requirements.txt

2. Initialize database schema

   make init-db

   (This runs scripts/init_nrod_db.py which applies database/nrod.sqlite.sql to create nrod.sqlite)

3. Import reference JSON extracts (example uses included json/ files)

   make import-ref

For automated imports from the Network Rail Open Data endpoints, set credentials via environment variables or CI secrets:

- NROD_USERNAME
- NROD_PASSWORD

See import_scripts/nrod_ref_import.py for options (`--no-download` to import local files).

Notes
- Do not commit credentials or secrets. Use environment variables or CI secrets.
- Large downloads should be kept outside the repo. The importer records metadata in the DB for downloaded files.
