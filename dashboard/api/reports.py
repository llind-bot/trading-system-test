"""Bar data coverage + freshness report endpoint.

Queries bars.db (bars_crypto + bars_stock), calculates age per symbol/timeframe,
applies market-hours exemption for US equities after hours/weekends, and returns
data in the format expected by BarsCoverageReport.jsx frontend component.

Version 2026-08-19 FIX-FRESHNESS
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("US/Eastern")
except ImportError:
    EASTERN = None

from fastapi import APIRouter
from dashboard.utils.db_connector import BAR_DB

router = APIRouter()


def _is_stock_symbol(symbol):
    return '/' not in symbol and len(symbol) <= 5


def _is_market_hours_eastern():
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    hour = now.hour
    minute = now.minute
    et_time = hour * 60 + minute
    return 570 <= et_time < 960


def get_bars_coverage_fresh():
    conn = sqlite3.connect(BAR_DB, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bars_%'"
    ).fetchall()]
    if not tables:
        conn.close()
        return []

    queries = []
    for t in sorted(tables):
        queries.append(f"SELECT symbol, timeframe, timestamp, low, high, volume FROM {t}")
    combined = " UNION ALL ".join(queries)
    rows = conn.execute(f"""
        SELECT symbol, timeframe, MIN(timestamp) as earliest, MAX(timestamp) as latest,
               COUNT(*) as bar_count, MIN(low) as min_price, MAX(high) as max_price,
               SUM(volume) as total_volume FROM (
            {combined}
        ) GROUP BY symbol, timeframe ORDER BY symbol
    """).fetchall()

    TF_THRESHOLDS = {
        '1m': 300, '5m': 900, '15m': 1800,
        '60m': 3600, '240m': 7200, '1D': 172800,
    }

    now_utc = datetime.now(timezone.utc)
    is_market_open = _is_market_hours_eastern()
    results = []

    for row in rows:
        sym = row['symbol']
        tf = row['timeframe']
        latest_ts = row['latest']
        freshness_sec = None
        if latest_ts:
            if isinstance(latest_ts, str):
                ts_clean = latest_ts.replace('T', ' ')[:19]
                dt = datetime.strptime(ts_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                freshness_sec = int((now_utc - dt).total_seconds())
            elif isinstance(latest_ts, (int, float)):
                dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
                freshness_sec = int((now_utc - dt).total_seconds())
            if freshness_sec < 0:
                freshness_sec = 0

        threshold = TF_THRESHOLDS.get(tf, 300)
        is_stale = False
        if freshness_sec is not None:
            is_stale = freshness_sec > threshold
            if _is_stock_symbol(sym) and not is_market_open:
                is_stale = False

        results.append({
            'symbol': sym,
            'timeframe': tf,
            'bar_count': row['bar_count'] or 0,
            'high': round(row['max_price'], 4) if row['max_price'] is not None else None,
            'low': round(row['min_price'], 4) if row['min_price'] is not None else None,
            'volume': row['total_volume'] or 0,
            'last_seen': str(latest_ts) if latest_ts else None,
            '_freshness_sec': freshness_sec,
            'freshness_sec': freshness_sec,
            'is_stale': is_stale,
            'stale_threshold_sec': threshold,
        })

    conn.close()
    return results
