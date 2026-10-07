"""Startup helper that ensures the CORPUS/SMART reference tables exist.

Reuses import_scripts/nrod_ref_import.py (run_imports) so schema/import logic
is not duplicated.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from typing import Optional

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("corpus_tiploc", "corpus_stanox", "corpus_crs", "smart_steps")
NON_EMPTY_TABLES = ("corpus_tiploc", "smart_steps")


def reference_data_present(db_path: str) -> bool:
    """Return True if all reference tables exist and the main ones contain rows."""
    if not db_path or not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            for table in REQUIRED_TABLES:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    return False
                if table in NON_EMPTY_TABLES and conn.execute(
                    f"SELECT 1 FROM {table} LIMIT 1"
                ).fetchone() is None:
                    return False
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return True


def ensure_reference_data(
    db_path: Optional[str],
    username: Optional[str],
    password: Optional[str],
    outdir: Optional[str] = None,
) -> bool:
    """Ensure CORPUS/SMART tables exist in db_path, importing them if missing.

    Never raises; returns True if reference data is available afterwards.
    """
    if not db_path:
        return False
    if reference_data_present(db_path):
        logger.info("Reference data already present in %s; skipping import", db_path)
        return True
    if not username or not password:
        logger.warning(
            "Reference tables missing in %s and no Network Rail credentials supplied; "
            "skipping automatic import (use --user/--password)", db_path,
        )
        return False
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "import_scripts"))
        try:
            from nrod_ref_import import run_imports
        finally:
            sys.path.pop(0)
        outdir = outdir or os.path.join(os.path.dirname(os.path.abspath(db_path)), "nrod_ref_downloads")
        logger.info("Importing CORPUS and SMART reference data into %s ...", db_path)
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
