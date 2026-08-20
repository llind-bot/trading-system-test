"""Standardized logging for the trading system.

JSON structured logs with rotation. Each engine gets a unique tag.
ERROR+ level logs trigger notification via notify_engine.

Extra fields are supported via kwargs: _log.info("msg", key="value")
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for easy parsing/searching."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if hasattr(record, "extra_fields") and record.extra_fields:
            log_data.update(record.extra_fields)
        return json.dumps(log_data)


def get_logger(name: str, tag: Optional[str] = None) -> logging.Logger:
    """Get a configured logger with JSON formatting and rotation.

    Supports extra fields via **kwargs in info/warning/error/debug calls.
    E.g. _log.info("connected", stream="stock") → {"message": "connected", "stream": "stock"}
    """
    trade_root = Path(__file__).resolve().parents[1]
    log_dir = trade_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        fh = logging.FileHandler(log_dir / f"{name}.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.WARNING)
        ch.setFormatter(JSONFormatter())
        logger.addHandler(ch)

    # Wrap all level methods to support **extra fields
    # Skip if already wrapped (prevent double-wrap on repeated get_logger calls)
    if getattr(logger, '_wrapped', False):
        return logger
    
    _orig_debug = logger.debug
    _orig_info = logger.info
    _orig_warning = logger.warning
    _orig_error = logger.error
    _orig_critical = logger.critical

    def _wrap(orig_method):
        def wrapped(msg, *args, **kwargs):
            if kwargs:
                record = orig_method.__self__.makeRecord(
                    orig_method.__self__.name,
                    getattr(logging, orig_method.__name__.upper()),
                    args[0] if args else "(unknown)",
                    args[1] if len(args) > 1 else 0,
                    msg if args else str(msg),
                    args[2:] if len(args) > 2 else (),
                    None,
                )
                record.extra_fields = _serialize_extra(kwargs)
                orig_method.__self__.handle(record)
            else:
                return orig_method(msg, *args, **kwargs)
        return wrapped

    logger.debug = _wrap(_orig_debug)
    logger.info = _wrap(_orig_info)
    logger.warning = _wrap(_orig_warning)
    logger.error = _wrap(_orig_error)
    logger.critical = _wrap(_orig_critical)
    logger._wrapped = True  # marker to prevent double-wrap

    return logger


def _serialize_extra(d: dict) -> dict:
    """Make a dict's values JSON-serializable."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, list) or isinstance(v, dict):
            try:
                out[k] = json.dumps(v)
            except (TypeError, ValueError):
                out[k] = str(v)
        else:
            out[k] = str(v)
    return out


class StructuredMessage:
    """Helper to add extra fields to log messages."""

    def __init__(self, message: str, **kwargs):
        self.message = message
        self.extra_fields = kwargs

    def __str__(self):
        return self.message


def info(logger: logging.Logger, msg: str, **extra):
    record = logger.makeRecord(
        logger.name, logging.INFO, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = _serialize_extra(extra)
    logger.handle(record)


def error(logger: logging.Logger, msg: str, **extra):
    record = logger.makeRecord(
        logger.name, logging.ERROR, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = _serialize_extra(extra)
    logger.handle(record)
