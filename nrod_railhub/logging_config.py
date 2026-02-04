#!/usr/bin/env python3
"""Logging configuration for nrod_railhub."""

import logging
import sys
from typing import Optional


# Map CLI log level strings to Python logging levels
LOG_LEVEL_MAP = {
    "verbose": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logger(log_level: str = "error") -> logging.Logger:
    """
    Configure and return the application logger.
    
    Args:
        log_level: One of 'verbose', 'info', 'warning', 'error' (default: 'error')
    
    Returns:
        Configured logger instance
    """
    # Get the root logger for the application
    logger = logging.getLogger("nrod_railhub")
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Map the string level to logging constant
    level = LOG_LEVEL_MAP.get(log_level.lower(), logging.ERROR)
    logger.setLevel(level)
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    
    # Create formatter with timestamp
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )
    handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance for a specific module.
    
    Args:
        name: Module name (default: 'nrod_railhub')
    
    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f"nrod_railhub.{name}")
    return logging.getLogger("nrod_railhub")
