"""Standardized logging for the trading system.

JSON structured logs with rotation. Each engine gets a unique tag.
ERROR+ level logs trigger notification via notify_engine.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for easy parsing/searching."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "ET",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        return json.dumps(log_data)


def get_logger(name: str, tag: Optional[str] = None) -> logging.Logger:
    """Get a configured logger with JSON formatting and rotation.

    Args:
        name: Logger name (usually engine module name)
        tag: Display tag shown in logs (e.g., '[stock-test]', '[crypto-test]')

    Returns:
        Configured logger instance
    """
    # Resolve log directory relative to test repo root
    trade_root = Path(__file__).resolve().parents[1]
    log_dir = trade_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    # Prevent duplicate handlers on repeated calls
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        # File handler with rotation
        fh = logging.FileHandler(log_dir / f"{name}.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        logger.addHandler(fh)

        # Console handler (stderr) for quick visibility
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(JSONFormatter())
        logger.addHandler(ch)

    return logger


class StructuredMessage:
    """Helper to add extra fields to log messages."""

    def __init__(self, message: str, **kwargs):
        self.message = message
        self.extra_fields = kwargs

    def __str__(self):
        return self.message


def info(logger: logging.Logger, msg: str, **extra):
    """Log an INFO message with extra fields."""
    record = logger.makeRecord(
        logger.name, logging.INFO, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = extra
    logger.handle(record)


def error(logger: logging.Logger, msg: str, **extra):
    """Log an ERROR message with extra fields (triggers notify)."""
    record = logger.makeRecord(
        logger.name, logging.ERROR, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = extra
    logger.handle(record)
