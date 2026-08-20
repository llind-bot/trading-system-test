"""Unified bar freshness checker — stub for dashboard compatibility.

Returns a list of dicts with _freshness_sec, stale_threshold_sec, is_stale per (symbol, tf).
The dashboard uses this for the "bar freshness" status display. Falls back gracefully if engines aren't running.
"""
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("US/Eastern")
except ImportError:
    EASTERN = None


def _is_stock_symbol(symbol):
    return '/' not in symbol and len(symbol) <= 5


def _is_market_hours_eastern():
    if not EASTERN:
        return True
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    et_time = now.hour * 60 + now.minute
    return 570 <= et_time < 960  # 9:30-16:00 ET


def check_bar_freshness_full(trading_db: str = None, active_symbols: list = None):
    """Check bar freshness from bars_crypto + bars_stock tables.
    
    Returns list of dicts:
        {"symbol": "...", "timeframe": "...", "_freshness_sec": N, "stale_threshold_sec": M, "is_stale": bool}
    """
    results = []
    TF_THRESHOLDS = {'1m': 300, '5m': 900, '15m': 1800, '60m': 3600, '240m': 7200, '1D': 172800}
    now_utc = datetime.now(timezone.utc)
    is_market_open = _is_market_hours_eastern()

    if trading_db and Path(trading_db).exists():
        try:
            conn = sqlite3.connect(str(trading_db))
            conn.row_factory = sqlite3.Row
            # Query both bars_crypto and bars_stock (unified)
            for table in ['bars_crypto', 'bars_stock']:
                tables_exist = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchall()]
                if not tables_exist and table not in tables_exist:
                    continue
                rows = conn.execute(
                    f"SELECT symbol, timeframe, MAX(timestamp) as last_update FROM {table} GROUP BY symbol, timeframe"
                ).fetchall()
                for row in rows:
                    sym = (row["symbol"] or "") if row["symbol"] else ""
                    tf = (row["timeframe"] or "") if row["timeframe"] else ""
                    last_update_str = row["last_update"] or ""

                    # Calculate freshness from ISO timestamp in DB
                    freshness = None
                    if last_update_str:
                        ts_clean = last_update_str.replace('T', ' ')[:19]
                        try:
                            dt = datetime.strptime(ts_clean, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                            freshness = max(0, int((now_utc - dt).total_seconds()))
                        except (ValueError, TypeError):
                            if isinstance(last_update_str, (int, float)):
                                dt = datetime.fromtimestamp(last_update_str, tz=timezone.utc)
                                freshness = max(0, int((now_utc - dt).total_seconds()))

                    threshold = TF_THRESHOLDS.get(tf, 300)
                    is_stale = False
                    if freshness is not None and freshness > threshold:
                        is_stale = True
                        # Stocks outside market hours are never stale
                        if _is_stock_symbol(sym) and not is_market_open:
                            is_stale = False

                    results.append({
                        "symbol": sym,
                        "timeframe": tf,
                        "_freshness_sec": freshness,
                        "stale_threshold_sec": threshold,
                        "is_stale": is_stale,
                    })
            conn.close()
        except Exception:
            pass

    return results
