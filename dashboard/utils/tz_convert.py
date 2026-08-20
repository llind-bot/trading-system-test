"""Timezone conversion for dashboard API responses.

All timestamps in the SQLite DB are stored as UTC ('YYYY-MM-DD HH:MM:SS').
This module provides a utility to convert any timestamp string or dict/list
recursively from UTC to Eastern Time (EDT/EST).

Usage:
  - Call utc_to_eastern() for single values
  - Call convert_timestamps_in_value() for nested dicts/lists
  - The REST API endpoints can call this in their response handlers
"""
import re
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("US/Eastern")
except ImportError:
    # Fallback for Python < 3.9 (shouldn't happen on Mac with brew python)
    EASTERN = None


def utc_to_eastern(dt_str):
    """Convert a UTC timestamp string to Eastern Time (EDT/EST).

    Handles 'YYYY-MM-DD HH:MM:SS' and ISO 8601 formats.
    Returns formatted as 'YYYY-MM-DD H:MM:SS AM/PM'.
    """
    if not dt_str or dt_str == "None":
        return dt_str

    try:
        # Try SQLite datetime format first
        dt = datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return dt_str

    # Ensure we have a UTC-aware datetime before converting
    if dt.tzinfo is None:
        utc_dt = dt.replace(tzinfo=timezone.utc)
    else:
        utc_dt = dt.astimezone(timezone.utc)

    if EASTERN:
        edt_dt = utc_dt.astimezone(EASTERN)
    else:
        # Manual fallback (month heuristic for old Python)
        month = utc_dt.month
        day = utc_dt.day
        import datetime as _dt_mod
        def is_edt(m, d):
            if 3 < m < 11:
                return True
            if m == 3:
                first_day_weekday = _dt_mod.datetime(utc_dt.year, 3, 1).weekday()
                second_sunday = 1 + (7 - first_day_weekday) % 7 * 2 + (6 - first_day_weekday) % 2
                return d >= second_sunday
            if m == 11:
                first_day_weekday = _dt_mod.datetime(utc_dt.year, 11, 1).weekday()
                first_sunday = 1 + (7 - first_day_weekday) % 7
                return d < first_sunday
            return False
        if is_edt(month, day):
            edt_dt = utc_dt.replace(tzinfo=timezone.utc) - __import__('datetime').timedelta(hours=4)
        else:
            edt_dt = utc_dt.replace(tzinfo=timezone.utc) - __import__('datetime').timedelta(hours=5)

    return edt_dt.strftime("%Y-%m-%d %I:%M:%S %p")


def convert_timestamps_in_value(obj):
    """Recursively walk a JSON-serializable object and convert timestamp fields."""
    if isinstance(obj, str):
        # Check if this string looks like a timestamp (has date-like pattern)
        if re.match(r'\d{4}-\d{2}-\d{2}', obj):
            return utc_to_eastern(obj)
        return obj
    elif isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            # Convert known timestamp field names; also try converting any value that looks like a date string
            if k in ("timestamp", "created_at", "updated_at", "filled_at", "submitted_at",
                      "order_placed_timestamp", "trigger_timestamp", "buy_timestamp",
                      "last_cycle_timestamp") or k.endswith("_at") or k.endswith("_ts"):
                new[k] = convert_timestamps_in_value(v)
            elif isinstance(v, (dict, list)) and any(k2 in v if isinstance(v, dict) else False for k2 in ("timestamp",)):
                # If it's a dict/list containing timestamp fields, recurse into it regardless of key name
                new[k] = convert_timestamps_in_value(v)
            else:
                new[k] = convert_timestamps_in_value(v)
        return new
    elif isinstance(obj, (list, tuple)):
        return [convert_timestamps_in_value(item) for item in obj]
    else:
        if isinstance(obj, str) and re.match(r'\d{4}-\d{2}-\d{2}', obj):
            return utc_to_eastern(obj)
        return obj


def timestamp_to_local(ts_value):
    """Convert a single timestamp value from UTC to local (Eastern) formatted string.

    Used by API endpoints to display timestamps in EDT/EST while DB stores UTC.
    """
    if ts_value is None or ts_value == "None" or not str(ts_value).strip():
        return ts_value
    s = str(ts_value).strip()
    if 'T' in s or re.match(r'\d{4}-\d{2}-\d{2}', s):
        return utc_to_eastern(s)
    return ts_value


def utc_epoch():
    """Return current UTC timestamp (Unix epoch seconds) as float."""
    from time import time
    return time()
