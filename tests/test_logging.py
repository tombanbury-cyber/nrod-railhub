#!/usr/bin/env python3
"""Test logging configuration for nrod_railhub."""

import logging
import sys
import pytest

from nrod_railhub.logging_config import setup_logger, get_logger, LOG_LEVEL_MAP


def test_log_level_map():
    """Test that all log level strings map to valid Python logging levels."""
    assert LOG_LEVEL_MAP["verbose"] == logging.DEBUG
    assert LOG_LEVEL_MAP["info"] == logging.INFO
    assert LOG_LEVEL_MAP["warning"] == logging.WARNING
    assert LOG_LEVEL_MAP["error"] == logging.ERROR


def test_setup_logger_default():
    """Test that default logger setup uses ERROR level."""
    logger = setup_logger()
    assert logger.level == logging.ERROR
    assert logger.name == "nrod_railhub"
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_setup_logger_info():
    """Test that INFO level is set correctly."""
    logger = setup_logger("info")
    assert logger.level == logging.INFO


def test_setup_logger_warning():
    """Test that WARNING level is set correctly."""
    logger = setup_logger("warning")
    assert logger.level == logging.WARNING


def test_setup_logger_verbose():
    """Test that verbose maps to DEBUG level."""
    logger = setup_logger("verbose")
    assert logger.level == logging.DEBUG


def test_setup_logger_error():
    """Test that ERROR level is set correctly."""
    logger = setup_logger("error")
    assert logger.level == logging.ERROR


def test_setup_logger_case_insensitive():
    """Test that log level strings are case-insensitive."""
    logger = setup_logger("INFO")
    assert logger.level == logging.INFO
    
    logger = setup_logger("VeRbOsE")
    assert logger.level == logging.DEBUG


def test_setup_logger_invalid_level():
    """Test that invalid log level defaults to ERROR."""
    logger = setup_logger("invalid")
    assert logger.level == logging.ERROR


def test_get_logger():
    """Test that get_logger returns logger with correct name."""
    logger = get_logger()
    assert logger.name == "nrod_railhub"
    
    logger = get_logger("cli")
    assert logger.name == "nrod_railhub.cli"
    
    logger = get_logger("listener")
    assert logger.name == "nrod_railhub.listener"


def test_logger_no_propagation():
    """Test that logger does not propagate to root logger."""
    logger = setup_logger()
    assert logger.propagate is False


def test_handler_output_stream():
    """Test that handler writes to stdout."""
    logger = setup_logger()
    handler = logger.handlers[0]
    assert handler.stream == sys.stdout


def test_formatter_format():
    """Test that formatter has correct format."""
    logger = setup_logger()
    handler = logger.handlers[0]
    formatter = handler.formatter
    assert formatter is not None
    # Check that the format contains key elements
    assert "[%(asctime)s]" in formatter._fmt or "%(asctime)s" in formatter._fmt
    assert "%(levelname)s" in formatter._fmt
    assert "%(message)s" in formatter._fmt
