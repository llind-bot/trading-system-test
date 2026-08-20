"""SQLite connection helpers for the dashboard."""
import sqlite3
from pathlib import Path
import subprocess
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("US/Eastern")
except ImportError:
    EASTERN = None

from dashboard.config.settings import DB_PATH, POSITIONS_DB, BAR_DB


def get_conn():
    """Get a thread-local SQLite connection."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def query_one(sql, params=()):
    """Execute a query and return first row as dict."""
    conn = get_conn()
    try:
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def query_all(sql, params=()):
    """Execute a query and return all rows as list of dicts."""
    conn = get_conn()
    try:
        cursor = conn.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_open_positions():
    """Get all open positions from DB (positions DB, not trading.db)."""
    conn = sqlite3.connect(POSITIONS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM positions WHERE is_closed=0 ORDER BY symbol").fetchall()
    result = [dict(r) for r in rows]
    # Add Alpaca live prices
    from dashboard.utils.alpaca_sync import get_live_prices
    symbols = [p.get('symbol','') for p in result if p.get('symbol')]
    prices = get_live_prices(symbols) if symbols else {}
    for p in result:
        sym = p.get("symbol", "")
        if sym in prices:
            p["current_price"] = prices[sym]
    return result


def get_trades(limit=100, offset=0, symbol=None):
    """Get trade history with optional filters."""
    where = ""
    params = []
    if symbol:
        where = "WHERE symbol = ?"
        params.append(symbol)
    rows = query_all(
        f"SELECT * FROM trades {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    )
    return rows


def get_total_trades_count():
    """Get total trade count."""
    row = query_one("SELECT COUNT(*) as cnt FROM trades")
    return row["cnt"] if row else 0


def get_equity_curve(limit=500):
    """Get equity curve data, latest N entries."""
    rows = query_all(
        "SELECT * FROM equity_curve ORDER BY id DESC LIMIT ?",
        [limit]
    )
    return rows


def get_latest_equity_snapshot():
    """Get the most recent equity snapshot."""
    row = query_one("SELECT * FROM equity_curve ORDER BY id DESC LIMIT 1")
    if row:
        return row
    return row


def get_equity_report(limit=2000):
    """Get equity curve report data with computed stats.
    
    All DB timestamps are stored as UTC ('YYYY-MM-DD HH:MM:SS').
    Uses Eastern Time (EDT/EST) for period_day calculation to match
    what the frontend will display.
    """
    # Raw curve data (newest first for chart display)
    curve = query_all(
        "SELECT id, timestamp, total_equity, cash, positions_value FROM equity_curve ORDER BY id ASC LIMIT ?",
        [limit]
    )
    if not curve:
        return {"curve": [], "stats": {}, "daily": []}

    # Running peak for drawdown computation
    worst_dd = 0.0
    worst_dd_date = None
    peak = curve[0]["total_equity"]
    for pt in curve:
        eq = pt["total_equity"]
        if eq > peak:
            peak = eq
        dd = eq - peak
        if dd < worst_dd:
            worst_dd = dd
            worst_dd_date = pt["timestamp"]

    stats = {
        "peak_equity": round(peak, 2),
        "worst_drawdown": round(worst_dd, 2),
        "worst_dd_date": worst_dd_date,
        "start_equity": round(curve[0]["total_equity"], 2),
        "end_equity": round(curve[-1]["total_equity"], 2),
        "total_points": len(curve),
        "period_days": round(
            (query_one("SELECT MAX(timestamp) FROM equity_curve")["MAX(timestamp)"] -
             query_one("SELECT MIN(timestamp) FROM equity_curve")["MIN(timestamp)"]),
            days=True
        ) if False else 0,
    }

    # Compute period days in Eastern Time (DB stores UTC)
    first_ts = query_one("SELECT MIN(timestamp) as ts FROM equity_curve")
    last_ts = query_one("SELECT MAX(timestamp) as ts FROM equity_curve")
    if first_ts and last_ts and first_ts["ts"] and last_ts["ts"]:
        from datetime import datetime, timezone
        def _parse_ts(ts_str):
            # Handle both DB stored format '%Y-%m-%d %H:%M:%S' and ISO with tz
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
                try:
                    return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc) if "+" not in ts_str and "Z" not in ts_str else datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        dt_first = _parse_ts(first_ts["ts"])
        dt_last = _parse_ts(last_ts["ts"])
        if EASTERN:
            dt_first_et = dt_first.astimezone(EASTERN)
            dt_last_et = dt_last.astimezone(EASTERN)
        else:
            dt_first_et = dt_first
            dt_last_et = dt_last
        stats["period_days"] = (dt_last_et.date() - dt_first_et.date()).days

    # ── Daily aggregation with ET midnight boundaries ──
    # Equity timestamps are stored in UTC. Crypto trades 24/7, so "daily P&L"
    # must use the trader's local midnight boundary (00:00 ET → 23:59:59 ET).
    # SQLite has no timezone awareness; Python zoneinfo handles EDT/EST correctly.

    all_rows = query_all(
        "SELECT id, timestamp, total_equity FROM equity_curve ORDER BY id ASC"
    )

    # Group rows by ET date using proper conversion (no SQL hacks)
    et_days = {}  # 'YYYY-MM-DD' -> list of {id, equity}
    utc = timezone.utc
    dt_cls = datetime
    for r in all_rows:
        ts_raw = r['timestamp']
        if isinstance(ts_raw, str):
            d = dt_cls.strptime(ts_raw, '%Y-%m-%d %H:%M:%S').replace(tzinfo=utc)
        elif hasattr(ts_raw, 'tzinfo'):
            d = ts_raw
        else:
            # Already a datetime
            d = ts_raw.replace(tzinfo=utc) if ts_raw.tzinfo is None else ts_raw
        et_date = d.astimezone(EASTERN).date()
        day_key = str(et_date)
        if day_key not in et_days:
            et_days[day_key] = []
        et_days[day_key].append({'id': r['id'], 'equity': r['total_equity']})

    # Build daily summary (oldest to newest)
    daily_agg = []
    for day_key in sorted(et_days.keys()):
        entries = et_days[day_key]
        equities = [e['equity'] for e in entries]
        eid_list = [e['id'] for e in entries]
        first_id = min(eid_list)
        last_id = max(eid_list)
        open_eq = query_one("SELECT total_equity FROM equity_curve WHERE id=?", [first_id])
        close_eq = query_one("SELECT total_equity FROM equity_curve WHERE id=?", [last_id])
        daily_agg.append({
            'day': day_key,
            'low': round(min(equities), 2),
            'high': round(max(equities), 2),
            'open': open_eq['total_equity'] if open_eq else min(equities),
            'close': close_eq['total_equity'] if close_eq else max(equities),
        })

    # Add daily range to each row
    for d in daily_agg:
        d["range"] = round(d["high"] - d["low"], 2)
        d["net"] = round(d["close"] - d["open"], 2)

    return {"curve": curve, "stats": stats, "daily": daily_agg}


def get_strategy_signals_latest():
    """Get latest engine signal per (symbol, strategy) pair.

    Reads from engine_signals table (the active table for the tick-driven engine).
    Deprecated legacy name kept for backward compat with dashboard imports.
    """
    sql = """
        SELECT s.* FROM engine_signals s
        INNER JOIN (
            SELECT symbol, strategy, MAX(id) as max_id
            FROM engine_signals
            GROUP BY symbol, strategy
        ) latest ON s.symbol = latest.symbol
            AND s.strategy = latest.strategy
            AND s.id = latest.max_id
        ORDER BY s.timestamp DESC
    """
    rows = query_all(sql)
    # Normalize to frontend-expected shape (vote_result from side column)
    for row in rows:
        if 'side' in row and 'vote_result' not in row:
            row['vote_result'] = str(row['side']).upper()
    return rows


def _ps_proc_info(pattern):
    """Check ps aux for a pattern, return {pid, uptime} or None."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            # Must match the pattern AND contain an actual python process
            # (not a wrapper shell that just happened to have engine names in its cmd)
            parts = line.strip().split()
            if len(parts) < 11:
                continue
            cmdline = ' '.join(parts[10:])
            if pattern not in cmdline:
                continue
            # Require actual Python binary or '-u' flag (not /bin/bash, /bin/zsh wrappers)
            if '/bin/bash' in cmdline or '/bin/sh' in cmdline or '/bin/zsh' in cmdline:
                # Skip bash/zsh wrapper lines — they're not the engine itself
                continue
            if 'python' not in line.lower():
                continue
            pid_str = parts[1]
            if pid_str.isdigit():
                try:
                    import psutil
                    proc = psutil.Process(int(pid_str))
                    secs = int(__import__('time').time() - proc.create_time())
                    secs = abs(secs)
                    days = secs // 86400
                    hours = (secs % 86400) // 3600
                    mins = (secs % 3600) // 60
                    s = secs % 60
                    parts_list = []
                    if days > 0: parts_list.append(f"{days}d")
                    if hours > 0: parts_list.append(f"{hours}h")
                    parts_list.append(f"{mins}m {s}s")
                    return {"pid": int(pid_str), "uptime": " ".join(parts_list)}
                except Exception:
                    return {"pid": int(pid_str), "uptime": None}
    except Exception:
        pass
    return None


def get_engine_status():
    """Get status of all engine processes (multi-process architecture)."""
    engines = {}

    for name, patterns in [
        ('bar_ingest', ['bar_ingest/ingestor', 'bar_ingest.ingestor', 'engine/bar_ingest.py']),
        ('crypto_engine', ['crypto_engine.py', 'engine.crypto_engine']),
        ('stock_engine', ['stock_engine.py', 'engine.stock_engine']),
        ('order_server', ['order_server.py', 'engine.order_server']),
    ]:
        info = None
        for pattern in patterns:
            info = _ps_proc_info(pattern)
            if info:
                break
        engines[name] = {"running": True, "pid": info["pid"], "uptime": info.get("uptime")} if info else {"running": False, "pid": None, "uptime": None}

    any_running = any(e['running'] for e in engines.values())

    return {
        "architecture": "multi-process",
        "engines": engines,
        "any_running": any_running,
    }


def get_recent_cycles(limit=50):
    """Get recent evaluation cycles with event summaries."""
    conn = sqlite3.connect(BAR_DB, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""SELECT cycle_id, event_type, COUNT(*) as count, MIN(timestamp) as first_ts, MAX(timestamp) as last_ts
            FROM engine_activity GROUP BY cycle_id, event_type ORDER BY MAX(timestamp) DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()

    # Group by cycle_id
    cycles = {}
    for r in rows:
        cid = r["cycle_id"]
        if cid not in cycles:
            cycles[cid] = {"cycle_id": cid, "last_ts": r["last_ts"], "events": []}
        cycles[cid]["events"].append({
            "event_type": r["event_type"],
            "count": r["count"],
            "first_ts": r["first_ts"]
        })

    return list(cycles.values())[:30]


def get_watchlist():
    """Load watchlist from YAML config."""
    import yaml
    watchlist_path = Path(__file__).resolve().parent.parent.parent / "config" / "watchlist.yaml"
    try:
        with open(watchlist_path) as f:
            data = yaml.safe_load(f)
        return data.get("assets", [])
    except Exception as e:
        return {"error": str(e)}


def get_strategy_groups():
    """Load strategy groups from YAML config."""
    import yaml
    strat_path = Path(__file__).resolve().parent.parent.parent / "config" / "strategies.yaml"
    try:
        with open(strat_path) as f:
            data = yaml.safe_load(f)
        return data.get("strategy_groups", {})
    except Exception as e:
        return {"error": str(e)}


def get_bars_coverage(limit=50):
    """Get bar coverage stats per symbol (from bars.db bars_crypto + bars_stock)."""
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
    conn.close()
    return [dict(r) for r in rows][:limit]


def get_recent_engine_events(limit=200):
    """Get recent engine activity events."""
    conn = sqlite3.connect(BAR_DB, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM engine_activity ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows] if rows else []


def get_errors_by_severity():
    """Get error counts by severity."""
    return query_all(
        "SELECT severity, COUNT(*) as cnt FROM errors GROUP BY severity ORDER BY cnt DESC"
    )


def get_tp_events(limit=50):
    """Get recent TP events."""
    return query_all(
        "SELECT * FROM tp_events ORDER BY id DESC LIMIT ?", [limit]
    )
