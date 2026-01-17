.PHONY: help init-db import-ref convert-shp test

PY ?= python3
VENV ?= .venv
REQ = requirements.txt

help:
	@echo "Targets:"
	@echo "  make init-db        Create SQLite DB(s) using database/*.sql or scripts/init_nrod_db.py"
	@echo "  make import-ref     Run nrod_ref_import.py (uses NROD_USERNAME/NROD_PASSWORD or --no-download)"
	@echo "  make convert-shp    Convert shapefiles: python import_scripts/convert_nwr_shp_to_sqlite.py"
	@echo "  make test           Run tests"

init-db:
	$(PY) scripts/init_nrod_db.py --sql database/nrod.sqlite.sql --out nrod.sqlite

import-ref:
	# Example: uses secrets or env vars: NROD_USERNAME / NROD_PASSWORD
	# When using --no-download with local files, dummy credentials are sufficient
	$(PY) import_scripts/nrod_ref_import.py --no-download --db nrod_ref.sqlite --outdir json --username unused --password unused

convert-shp:
	# Example usage, change paths as needed:
	$(PY) import_scripts/convert_nwr_shp_to_sqlite.py json/ --out nwr.sqlite

test:
	pytest -q
