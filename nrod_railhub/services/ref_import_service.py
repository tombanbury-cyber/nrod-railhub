#!/usr/bin/env python3
"""
ref_import_service.py

Continuous background service for importing Network Rail reference data (CORPUS and SMART).
Runs imports on a configurable interval (default 24 hours).

Configuration via environment variables:
    NR_USERNAME     - Network Rail username (required)
    NR_PASSWORD     - Network Rail password (required)
    DB_PATH         - SQLite database path (default: nrod_ref.sqlite)
    OUTDIR          - Download directory (default: nrod_ref_downloads)
    REF_IMPORT_INTERVAL - Import interval in seconds (default: 86400 = 24 hours)
    DATASETS        - Comma-separated list of datasets (default: CORPUS,SMART)

Usage:
    # As a module
    python3 -m nrod_railhub.services.ref_import_service
    
    # Direct execution
    python3 nrod_railhub/services/ref_import_service.py
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Optional

# Add parent directories to path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "import_scripts"))

from nrod_ref_import import run_imports


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class RefImportService:
    """Continuous service that imports Network Rail reference data on a schedule."""
    
    def __init__(
        self,
        db_path: str,
        username: str,
        password: str,
        outdir: str = "nrod_ref_downloads",
        interval: int = 86400,  # 24 hours (86400 seconds)
        datasets: Optional[list[str]] = None,
    ):
        """
        Initialize the reference import service.
        
        Args:
            db_path: Path to SQLite database
            username: Network Rail username
            password: Network Rail password
            outdir: Directory for downloaded files
            interval: Import interval in seconds (default 24 hours)
            datasets: List of datasets to import (default: ["CORPUS", "SMART"])
        """
        self.db_path = db_path
        self.username = username
        self.password = password
        self.outdir = outdir
        self.interval = interval
        self.datasets = datasets or ["CORPUS", "SMART"]
        self.running = False
        self.shutdown_requested = False
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
    
    def _handle_shutdown_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        logger.info(f"Received signal {signum}, requesting shutdown...")
        self.shutdown_requested = True
    
    def _run_import_cycle(self) -> None:
        """Run a single import cycle for all configured datasets."""
        try:
            logger.info(f"Starting import cycle for datasets: {', '.join(self.datasets)}")
            start_time = time.time()
            
            summary = run_imports(
                db_path=self.db_path,
                datasets=self.datasets,
                username=self.username,
                password=self.password,
                outdir=self.outdir,
                download=True,
                rebuild=False,
            )
            
            duration = time.time() - start_time
            logger.info(f"Import cycle completed in {duration:.1f}s")
            
            # Log summary for each dataset
            for ds, result in summary["datasets"].items():
                if ds == "CORPUS" and "TIPLOCDATA" in result:
                    logger.info(
                        f"  {ds}: TIPLOCDATA={result['TIPLOCDATA']}, "
                        f"STANOXDATA={result['STANOXDATA']}, "
                        f"CRSDATA={result['CRSDATA']}"
                    )
                elif ds == "SMART" and "BERTHDATA" in result:
                    logger.info(f"  {ds}: BERTHDATA={result['BERTHDATA']}")
                elif "error" in result:
                    logger.warning(f"  {ds}: {result['error']}")
            
        except Exception as e:
            logger.error(f"Import cycle failed: {e}", exc_info=True)
    
    def _sleep_with_interrupt(self, seconds: int) -> bool:
        """
        Sleep for specified seconds, checking for shutdown every second.
        
        Returns:
            True if sleep completed normally, False if interrupted by shutdown
        """
        for _ in range(seconds):
            if self.shutdown_requested:
                return False
            time.sleep(1)
        return True
    
    def run(self) -> None:
        """Run the service continuously until shutdown is requested."""
        logger.info("Starting reference import service")
        logger.info(f"  DB path: {self.db_path}")
        logger.info(f"  Output directory: {self.outdir}")
        logger.info(f"  Import interval: {self.interval}s ({self.interval / 3600:.1f}h)")
        logger.info(f"  Datasets: {', '.join(self.datasets)}")
        
        self.running = True
        
        # Run initial import immediately
        self._run_import_cycle()
        
        # Main service loop
        while not self.shutdown_requested:
            logger.info(f"Next import in {self.interval}s ({self.interval / 3600:.1f}h)")
            
            # Sleep with responsive interrupt checking
            if not self._sleep_with_interrupt(self.interval):
                break  # Shutdown requested during sleep
            
            if not self.shutdown_requested:
                self._run_import_cycle()
        
        logger.info("Reference import service stopped")
        self.running = False


def main() -> int:
    """Main entry point for the service."""
    # Load configuration from environment variables
    username = os.environ.get("NR_USERNAME")
    password = os.environ.get("NR_PASSWORD")
    db_path = os.environ.get("DB_PATH", "nrod_ref.sqlite")
    outdir = os.environ.get("OUTDIR", "nrod_ref_downloads")
    interval_str = os.environ.get("REF_IMPORT_INTERVAL", "86400")
    datasets_str = os.environ.get("DATASETS", "CORPUS,SMART")
    
    # Validate required configuration
    if not username:
        logger.error("Missing required environment variable: NR_USERNAME")
        return 1
    if not password:
        logger.error("Missing required environment variable: NR_PASSWORD")
        return 1
    
    try:
        interval = int(interval_str)
        if interval < 60:
            logger.warning(f"Interval {interval}s is very short, using minimum of 60s")
            interval = 60
    except ValueError:
        logger.error(f"Invalid REF_IMPORT_INTERVAL: {interval_str} (must be integer)")
        return 1
    
    datasets = [d.strip().upper() for d in datasets_str.split(",") if d.strip()]
    if not datasets:
        logger.error("No datasets specified")
        return 1
    
    # Create and run the service
    service = RefImportService(
        db_path=db_path,
        username=username,
        password=password,
        outdir=outdir,
        interval=interval,
        datasets=datasets,
    )
    
    try:
        service.run()
        return 0
    except Exception as e:
        logger.error(f"Service failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
