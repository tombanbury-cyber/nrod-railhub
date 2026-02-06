#!/usr/bin/env python3
"""Logging configuration for nrod_railhub."""

import logging
import sys
from typing import Optional
from logging.handlers import RotatingFileHandler

# Map CLI log level strings to Python logging levels
LOG_LEVEL_MAP = {
    "verbose": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logger(
    log_level: str = "error",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """
    Configure and return the application logger.

    Args:
        log_level: One of 'verbose', 'info', 'warning', 'error' (default: 'error')
        log_file: Optional file path to write logs to (rotating).
        max_bytes: Max bytes per log file before rotation (default: 10MB).
        backup_count: How many rotated files to keep (default: 5).

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("nrod_railhub")

    # Clear any existing handlers so repeated calls (e.g. in tests) are deterministic
    logger.handlers.clear()

    # Map the string level to logging constant
    level = LOG_LEVEL_MAP.get(log_level.lower(), logging.ERROR)
    logger.setLevel(level)

    # Formatter with timestamp
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )

    # Console handler (stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Optional rotating file handler
    if log_file:
        file_handler = RotatingFileHandler(
            filename=log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Return a child logger under the application logger name.

    Examples:
        get_logger() -> logger named "nrod_railhub"
        get_logger("listener") -> "nrod_railhub.listener"
    """
    if name:
        return logging.getLogger(f"nrod_railhub.{name}")
    return logging.getLogger("nrod_railhub")
