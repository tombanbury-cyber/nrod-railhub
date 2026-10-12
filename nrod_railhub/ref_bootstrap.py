"""Startup helper that ensures the CORPUS/SMART reference tables exist.

Reuses import_scripts/nrod_ref_import.py (run_imports) so schema/import logic
is not duplicated.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from .db_backend import DB_ERRORS, connect, is_postgres_target, redact_target

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("corpus_tiploc", "corpus_stanox", "corpus_crs", "smart_steps")
NON_EMPTY_TABLES = ("corpus_tiploc", "smart_steps")


def reference_data_present(db_path: str) -> bool:
    """Return True if all reference tables exist and the main ones contain rows.

    ``db_path`` is a SQLite file path or a PostgreSQL URL.
    """
    if not db_path:
        return False
    if not is_postgres_target(db_path) and not os.path.exists(db_path):
        return False
    try:
        conn = connect(db_path)
        try:
            for table in REQUIRED_TABLES:
                if not conn.table_exists(table):
                    return False
                if table in NON_EMPTY_TABLES and conn.execute(
                    f"SELECT 1 FROM {table} LIMIT 1"
                ).fetchone() is None:
                    return False
        finally:
            conn.close()
    except DB_ERRORS:
        return False
    return True


def ensure_reference_data(
    db_path: Optional[str],
    username: Optional[str],
    password: Optional[str],
    outdir: Optional[str] = None,
) -> bool:
    """Ensure CORPUS/SMART tables exist in db_path (SQLite file or PostgreSQL URL),
    importing them if missing.

    Never raises; returns True if reference data is available afterwards.
    """
    if not db_path:
        return False
    shown = redact_target(db_path)
    if reference_data_present(db_path):
        logger.info("Reference data already present in %s; skipping import", shown)
        return True
    if not username or not password:
        logger.warning(
            "Reference tables missing in %s and no Network Rail credentials supplied; "
            "skipping automatic import (use --user/--password)", shown,
        )
        return False
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
        try:
            from nrod_ref_import import run_imports
        finally:
            sys.path.pop(0)
        if outdir is None:
            base = (
                os.path.expanduser("~/.cache/openraildata")
                if is_postgres_target(db_path)
                else os.path.dirname(os.path.abspath(db_path))
            )
            outdir = os.path.join(base, "nrod_ref_downloads")
        logger.info("Importing CORPUS and SMART reference data into %s ...", shown)
        run_imports(
            db_path=db_path,
            datasets=["CORPUS", "SMART"],
            username=username,
            password=password,
            outdir=outdir,
            download=True,
            rebuild=False,
        )
    except Exception as e:
        logger.error("Automatic reference data import failed: %s", e)
        return False
    return reference_data_present(db_path)
