"""All read-only REST API endpoints."""
import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

from dashboard.utils.db_connector import (
    get_open_positions, get_trades, get_total_trades_count,
    get_equity_curve, get_latest_equity_snapshot, get_equity_report,
    get_strategy_signals_latest, get_engine_status,
    get_recent_cycles, get_watchlist,
    get_bars_coverage, get_recent_engine_events, get_errors_by_severity,
    get_tp_events, query_all
)
from dashboard.utils.alpaca_sync import get_live_prices
from dashboard.utils.tz_convert import convert_timestamps_in_value

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────

def _position_with_live(pr):
    """Build positions from live Alpaca data as the primary source.
    
    Uses DB rows only for cost basis lookup when available. This avoids:
    1. Crypto/stock misclassification (Alpaca symbols like BTCUSD are all-alpha)
    2. Stale DB prices making unrealized P&L = $0
    3. Ghost positions from sold holdings never marked is_closed=1
    """
    # Get live Alpaca positions
    all_symbols_path = Path(__file__).resolve().parent.parent.parent / "src"
    sys.path.insert(0, str(all_symbols_path))
    from data.alpaca_rest import AlpacaRestClient
    from persistence.credentials import load_credentials
    creds_live = load_credentials()
    alpaca_client = AlpacaRestClient(creds_live.alpaca.api_key, creds_live.alpaca.secret_key, creds_live.alpaca.paper)
    alpaca_positions = alpaca_client.get_positions()

    # Build a map of live positions keyed by base ticker (e.g. BTCUSD -> BTC)
    live_map = {}
    for pos in alpaca_positions:
        qty_val = float(getattr(pos, 'qty', 0) or 0)
        if qty_val < 1e-6:
            continue
        sym_raw = getattr(pos, 'symbol', '').upper()
        avg_entry = float(getattr(pos, 'avg_entry_price', 0) or 0)
        cur_px = float(getattr(pos, 'current_market_price') or getattr(pos, 'current_price', 0) or avg_entry)
        
        # Derive base ticker: BTCUSD -> BTC, AVAXUSD -> AVAX, PANW -> PANW
        base = sym_raw
        for suffix in ['USD', 'USDT', 'USDC']:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        
        # Normalize Alpaca's asset_class to what the frontend expects:
        # Alpaca returns 'us_equity' for stocks, but frontend filters on === 'stock'
        ac = getattr(pos, 'asset_class', None)
        if ac == 'us_equity':
            ac = 'stock'
        if not ac:
            # Fallback: crypto symbols from Alpaca have USD appended and length > 4
            ac = 'crypto' if len(sym_raw) > 4 else 'stock'
        
        live_map[base] = {
            'symbol': base,
            'asset_class': ac,
            'qty': round(qty_val, 6),
            'avg_cost_live': round(avg_entry, 6) if avg_entry else None,
            'current_price': round(cur_px, 6),
            'cost_basis_live': round(avg_entry * qty_val, 2),
            'current_value_live': round(cur_px * qty_val, 2),
            'unrealized_pnl_live': round((cur_px - avg_entry) * qty_val, 2) if avg_entry else 0,
        }

    # Merge DB cost basis where available (DB may have more accurate avg_cost for partially-sold positions)
    db_rows = get_open_positions()
    db_by_base = {}
    if db_rows:
        for pos in db_rows:
            sym = pos['symbol']
            base = sym.upper()
            for suffix in ['/USD', '/USDT', '/USDC', 'USD', 'USDT', 'USDC']:
                if base.endswith(suffix):
                    base = base[:-len(suffix)]
                    break
            qty_db = float(pos.get('qty', 0) or 0)
            if qty_db > 1e-12 and base in live_map:
                db_by_base[base] = pos

    result = list(live_map.values())
    for entry in result:
        base = entry['symbol']
        if base in db_by_base:
            dp = db_by_base[base]
            db_qty = float(dp.get('qty', 0) or 0)
            db_avg = float(dp.get('avg_cost', 0) or 0)
            if db_qty > 1e-12 and db_avg > 0:
                live_avg = float(entry.get('avg_cost_live', 0) or 0)
                # Always prefer DB avg_cost for P&L calculation — it is the true
                # multi-buy cost basis. Alpaca's FIFO-depleted avg_entry_price diverges
                # from our tracked average once any prior fills are sold (FIFO recycling).
                # Use live price from Alpaca but always compute PnL against DB cost.
                use_db = True

                if use_db:
                    entry['avg_cost'] = round(db_avg, 6)
                    entry['cost_basis'] = round(db_avg * db_qty, 2)
                    entry['current_value'] = round(entry['current_price'] * db_qty, 2)
                    entry['unrealized_pnl'] = round((entry['current_price'] - db_avg) * db_qty, 2)
                    entry['unrealized_pnl_pct'] = round(((entry['current_price'] / max(db_avg, 1e-12)) - 1) * 100, 2)
                    entry['qty'] = round(db_qty, 6)
                else:
                    # Live data takes priority — drift detected
                    live_avg = entry.get('avg_cost_live', 0) or 0
                    live_cb = entry.get('cost_basis_live', 0) or (live_avg * db_qty)
                    entry['avg_cost'] = round(live_avg, 6)
                    entry['cost_basis'] = round(live_cb, 2)
                    entry['current_value'] = round(entry['current_price'] * db_qty, 2)
                    entry['unrealized_pnl'] = round((entry['current_price'] - live_avg) * db_qty, 2)
                    entry['unrealized_pnl_pct'] = round(((entry['current_price'] / max(live_avg, 1e-12)) - 1) * 100, 2)
                    entry['qty'] = round(db_qty, 6)
            else:
                live_avg = float(entry.get('avg_cost_live', 0) or 0)
                cb = float(entry.get('cost_basis_live', 0) or (live_avg * db_qty))
                entry['avg_cost'] = entry.pop('avg_cost_live', None)
                entry['cost_basis'] = entry.pop('cost_basis_live', 0)
                entry['current_value'] = entry.pop('current_value_live', 0)
                entry['unrealized_pnl'] = entry.pop('unrealized_pnl_live', 0)
                entry['unrealized_pnl_pct'] = round(((entry['current_price'] / max(live_avg, 1e-12)) - 1) * 100, 2) if live_avg > 0 else None
        else:
            # No DB match — use live data for P\u0026L percentage
            live_avg = float(entry.get('avg_cost_live', 0) or 0)
            entry['avg_cost'] = entry.pop('avg_cost_live', None)
            entry['cost_basis'] = entry.pop('cost_basis_live', 0)
            entry['current_value'] = entry.pop('current_value_live', 0)
            entry['unrealized_pnl'] = entry.pop('unrealized_pnl_live', 0)
            entry['unrealized_pnl_pct'] = round(((entry['current_price'] / max(live_avg, 1e-12)) - 1) * 100, 2) if live_avg > 0 else None
        entry['current_price_live'] = True

    return result


# ── Endpoints ───────────────────────────────────────────────────────

@router.get("/api/positions")
def positions():
    """Open positions with live Alpaca prices."""
    return _position_with_live(None)


@router.get("/api/trades")
def trades(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = None,
):
    """Trade history with pagination and filters.

    Filters out pending/placeholder rows, enriches zero-fill trades from Alpaca
    (with timeout to prevent hanging), normalizes symbols for consistent grouping,
    and de-duplicates by alpaca_order_id.
    """
    # Lazy-import symbol_for_db for crypto symbol normalization
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from src.persistence.transaction_logger import symbol_for_db

    raw_trades = get_trades(limit=limit, offset=offset, symbol=symbol)

    if not raw_trades:
        return {"trades": [], "total": get_total_trades_count()}

    # 1. Drop pending rows (on raw data first to avoid extra work)
    FINAL_STATUSES = {'filled', 'cancelled', 'rejected', 'expired'}
    trades = [t for t in raw_trades if t.get('status') in FINAL_STATUSES]

    # 2. Normalize symbols so buys/sells group together for P&L (AVAXUSD -> AVAX/USD)
    for t in trades:
        t['symbol'] = symbol_for_db(t.get('symbol', ''))

    # 3. Enrich zero-fill rows from Alpaca (with 5s timeout) ──
    enriched = []
    zero_fill_ids = [t['id'] for t in trades if (
        float(t.get('qty') or 0) == 0 and float(t.get('price') or 0) == 0
        and t.get('notional') is None
    )]
    # Only attempt Alpaca enrichment if there are zero-fill rows (avoids unnecessary overhead)
    if zero_fill_ids:
        try:
            all_symbols_path = Path(__file__).resolve().parent.parent.parent / "src"
            sys.path.insert(0, str(all_symbols_path))
            from data.alpaca_rest import AlpacaRestClient
            from persistence.credentials import load_credentials as load_creds
            creds = load_creds()
            client = AlpacaRestClient(creds.alpaca.api_key, creds.alpaca.secret_key, creds.alpaca.paper)
            sess = client._session()
        except Exception:
            enriched = trades
        else:
            for t in trades:
                t = dict(t)
                needs_enrich = (
                    float(t.get('qty') or 0) == 0 and float(t.get('price') or 0) == 0
                )
                if needs_enrich and t.get('status') == 'filled' and t.get('alpaca_order_id'):
                    try:
                        resp = sess.get(
                            f'{client.base_url}/v2/orders/{t["alpaca_order_id"]}',
                            timeout=5,
                        )
                        if resp.ok:
                            o = resp.json()
                            qty_val = float(o.get('filled_qty') or o.get('qty') or 0)
                            price_val = float(o.get('filled_avg_price') or 0)
                            notional_val = (
                                float(o.get('notional') or 0) or (qty_val * price_val)
                            )
                            t['qty'] = round(float(qty_val), 6) if qty_val else t.get('qty', 0)
                            t['price'] = (
                                round(float(price_val), 6) if price_val else t.get('price', 0)
                            )
                            t['notional'] = notional_val
                            t['total_cost'] = round(qty_val * (price_val or 0), 6)
                    except Exception:
                        pass
                # Compute total_cost for rows that still need it
                tc = float(t.get('total_cost') or 0)
                if t.get('amount_type') == 'dollar' and t['side'] == 'buy':
                    n = float(t.get('notional') or 0)
                    if tc <= 0 and n > 0:
                        t['total_cost'] = round(n, 2)
                elif tc <= 0 and float(t.get('qty') or 0) > 0 and float(t.get('price') or 0) > 0:
                    t['total_cost'] = round(float(t['qty']) * float(t['price']), 6)
                enriched.append(t)
    else:
        enriched = trades

    # ── 3. De-duplicate: keep only the final record per alpaca_order_id ──
    seen_by_order = {}
    for t in reversed(enriched):
        oid = t.get('alpaca_order_id') or t.get('client_order_id')
        if oid:
            seen_by_order[oid] = t
        else:
            pass  # legacy records without order ID — keep as-is
    final_trades = list(seen_by_order.values()) if seen_by_order else enriched

    # ── 4. Enrich with position cost basis for sells that have no matching buy trade ──
    # Positions opened via live sync (not place_order) won't have a buy trade row,
    # so we fall back to the positions table avg_cost for P&L computation.
    positions_by_sym = {}
    try:
        db_path = Path(__file__).resolve().parent.parent.parent / 'database' / 'trades.db'
        pos_conn = sqlite3.connect(str(db_path))
        for row in pos_conn.execute("SELECT symbol, avg_cost, is_closed FROM positions WHERE qty > 0 OR is_closed=1"):
            sym = symbol_for_db(row[0] if row[0] else '')
            positions_by_sym[sym] = {
                'avg_cost': float(row[1]) if row[1] else None,
                'is_closed': bool(row[2]),
            }
        pos_conn.close()
    except Exception:
        pass

    for t in final_trades:
        sym = t.get('symbol', '')
        cost_basis_fallback = positions_by_sym.get(sym, {}).get('avg_cost')
        if cost_basis_fallback:
            t['_cost_basis_per_share'] = round(cost_basis_fallback, 6)

    # ── 5. Attach stored P&L to trade rows — display only, no computation ──
    # P&L is computed and stored at fill time in src/persistence/transaction_logger.py
    # (compute_and_store_pnl / compute_and_store_pnl_by_algo). The API layer
    # NEVER recomputes P&L — it just displays what's on the trade row.
    # For sells without stored P&L (cost basis unresolvable), default to $0.
    for t in final_trades:
        if t['side'] == 'sell':
            pnl_d = t.get('pnl_dollars')
            pnl_p = t.get('pnl_pct')
            t['_pnl_dollars'] = float(pnl_d) if pnl_d is not None else 0.0
            t['_pnl_pct']     = float(pnl_p) if pnl_p is not None else 0.0

    return {
        "trades": final_trades,
        "total": get_total_trades_count(),
    }


@router.get("/api/equity/curve")
def equity_curve(limit: int = Query(500, ge=1)):
    """Equity curve data."""
    return get_equity_curve(limit=limit)


@router.get("/api/equity/snapshot")
def equity_snapshot():
    """Current equity snapshot — uses live Alpaca account data.
    
    positions_value and num_positions are derived from the same
    live Alpaca position data that the Positions tab uses,
    so both tabs always agree.
    """
    snap = get_latest_equity_snapshot()

    _src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(_src_path) not in sys.path:
        sys.path.insert(0, str(_src_path))
    from persistence.credentials import load_credentials as _load_creds
    from data.alpaca_rest import AlpacaRestClient as _AlpacaRestClient
    _creds = _load_creds()
    _client = _AlpacaRestClient(_creds.alpaca.api_key, _creds.alpaca.secret_key, _creds.alpaca.paper)

    total_equity = None
    cash = None
    buying_power = 0.0
    try:
        acc = _client.get_account()
        total_equity = float(acc.equity) if acc else None
        cash = float(getattr(acc, 'cash', 0) or 0)
        buying_power = float(getattr(acc, 'buying_power', 0) or 0)
    except Exception:
        pass

    # --- Derive positions from live Alpaca data (same as Positions tab) ---
    rows = get_open_positions()

    # Build base-ticker lookup from Alpaca: BTCUSD -> BTC, AVAXUSD -> AVAX, etc.
    live_by_base = {}
    alpaca_positions = _client.get_positions()
    for pos in alpaca_positions:
        qty = float(getattr(pos, 'qty', 0) or 0)
        if qty <= 0:
            continue
        sym_raw = getattr(pos, 'symbol', '').upper()
        sym_clean = sym_raw.replace("/", "")
        price = float(getattr(pos, 'current_market_price') or getattr(pos, 'current_price', 0))
        # Derive base ticker (BTCUSD -> BTC, etc.)
        base = sym_clean
        for suffix in ["USD", "USDT", "USDC"]:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        live_by_base[base] = {"qty": qty, "price": price}

    # Group DB rows by base ticker and sum qty only for live positions
    seen = {}
    if rows:
        for pos in rows:
            sym = pos["symbol"]
            base = sym.upper()
            for suffix in ["/USD", "/USDT", "/USDC", "USD", "USDT", "USDC"]:
                if base.endswith(suffix):
                    base = base[:-len(suffix)]
                    break
            if base not in seen:
                seen[base] = {"qty": 0}
            seen[base]["qty"] += pos["qty"]

    # Compute positions_value only from live symbols that match
    positions_value = 0.0
    num_positions = 0
    for base, entry in seen.items():
        if base not in live_by_base:
            continue
        price = live_by_base[base]["price"]
        positions_value += round(price * entry["qty"], 2)
        num_positions += 1
    
    positions_value = round(positions_value, 2)
    
    return {
        "snapshot": {
            "timestamp": convert_timestamps_in_value(str(snap["timestamp"])) if snap else None,
            "total_equity": round(total_equity, 2) if total_equity else (snap.get("total_equity", 0) if snap else 0),
            "cash": round(cash, 2) if cash else (snap.get("cash", 0) if snap else 0),
            "positions_value": positions_value,
            "day_pnl": snap.get("day_pnl", 0) if snap else 0,
            "total_pnl": snap.get("total_pnl", 0) if snap else 0,
            "num_positions": num_positions,
        },
        "account": {
            "buying_power": buying_power,
            "cash": round(cash, 2) if cash else (snap.get("cash", 0) if snap else 0),
            "portfolio_value": round(total_equity, 2) if total_equity else None,
        },
    }



@router.get("/api/equity/report")
def equity_report(limit: int = Query(2000, ge=1)):
    """Equity curve report with computed stats and daily aggregation."""
    return get_equity_report(limit=limit)


@router.get("/api/engine/status")
def engine_status():
    """Engine health and cycle status."""
    return get_engine_status()


@router.post("/api/engine/restart")
def engine_restart():
    """Restart the trading engine processes (new multi-process architecture).
    
    Stops bar_ingest, crypto_engine, stock_engine, order_server.
    Clears pycache. Restarts in correct order:
      1. bar_ingest (takes WS connection)
      2. crypto_engine
      3. stock_engine
      4. order_server
    """
    import os
    import subprocess
    from pathlib import Path
    trade_root = Path(__file__).resolve().parent.parent.parent
    try:
        # Step 1: Kill all existing engine processes
        for pattern in ['bar_ingest/main.py', 'crypto_engine.py', 'stock_engine.py', 'order_server.py']:
            result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    pid = pid.strip()
                    if pid:
                        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
        
        # Also kill old monolith engine just in case
        result = subprocess.run(["pgrep", "-f", "event_loop"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                pid = pid.strip()
                if pid:
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)

        # Step 2: Clear pycache
        src_path = trade_root / "src"
        subprocess.run(["find", str(src_path), "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                       capture_output=True, timeout=30)

        # Step 3: Clear bar_ingest pycache too
        subprocess.run(["find", str(trade_root / "bar_ingest"), "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                       capture_output=True, timeout=30)

        import time
        time.sleep(2)  # Let processes fully die

        # Step 4: Start new processes in correct order
        procs = []
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        # 1. bar_ingest first (takes the WS connection)
        procs.append(subprocess.Popen(
            ["python", str(trade_root / "bar_ingest" / "main.py")],
            cwd=str(trade_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ))
        time.sleep(2)

        # 2. crypto_engine
        procs.append(subprocess.Popen(
            ["python", str(trade_root / "engine" / "crypto_engine.py")],
            cwd=str(trade_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ))
        time.sleep(1)

        # 3. stock_engine
        procs.append(subprocess.Popen(
            ["python", str(trade_root / "engine" / "stock_engine.py")],
            cwd=str(trade_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ))
        time.sleep(1)

        # 4. order_server
        procs.append(subprocess.Popen(
            ["python", str(trade_root / "engine" / "order_server.py")],
            cwd=str(trade_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ))

        return {"message": "Engine restart initiated (multi-process)", "status": "restarting", "pids": [p.pid for p in procs]}
    except subprocess.TimeoutExpired:
        return {"error": "Restart timed out"}, 500
    except Exception as e:
        return {"error": str(e)}, 500


@router.get("/api/engine/cycle-history")
def engine_cycle_history(limit: int = Query(30, ge=1)):
    """Recent evaluation cycles with summaries."""
    return get_recent_cycles(limit=limit)


@router.get("/api/watchlist")
def watchlist():
    """Watchlist config from YAML."""
    wl = get_watchlist()
    if isinstance(wl, dict) and "error" in wl:
        return {"error": wl["error"]}
    return wl


@router.get("/api/reports/positions")
def report_positions():
    """Position tracker formatted data (same as position_tracker.py)."""
    return _position_with_live(None)


@router.get("/api/reports/comprehensive")
def report_comprehensive():
    """Comprehensive portfolio audit data."""
    snap = get_latest_equity_snapshot() or {}
    positions = _position_with_live(None)
    trades_recent = get_trades(limit=50, offset=0)
    tp_events = get_tp_events(limit=20)

    # Strategy breakdown
    all_signals = get_strategy_signals_latest()

    return {
        "equity_snapshot": dict(snap),
        "positions": positions,
        "recent_trades": trades_recent,
        "open_positions_count": len(positions),
        "total_trades_count": get_total_trades_count(),
        "tp_events_last_20": tp_events,
        "strategy_evaluations_latest": all_signals,
    }


@router.get("/api/reports/24h-trades")
def report_24h():
    """Last 24h trades with cost basis and P&L. Enriches zero-fill DB records with live Alpaca data."""
    # Get fills from the last 24 hours — no JOIN, just a single query
    # (FIFO pairing happens in Python below to avoid SQL Cartesian explosion)
    sql = """
        SELECT * FROM trades
        WHERE timestamp >= datetime('now', '-24 hours')
          AND status IN ('filled', 'partial_fill')
        ORDER BY id DESC
    """
    trades_24h_raw = query_all(sql)
    
    # Enrich fills that have zero qty/price by querying Alpaca REST for real order details
    enriched = []
    if trades_24h_raw:
        try:
            all_symbols_path = Path(__file__).resolve().parent.parent.parent / "src"
            sys.path.insert(0, str(all_symbols_path))
            from data.alpaca_rest import AlpacaRestClient
            from persistence.credentials import load_credentials as load_creds
            creds = load_creds()
            client = AlpacaRestClient(creds.alpaca.api_key, creds.alpaca.secret_key, creds.alpaca.paper)
            sess = client._session()  # initialize HTTP session
        except Exception:
            enriched = trades_24h_raw
        else:
            for t in trades_24h_raw:
                t = dict(t)
                alpaca_id = t.get('alpaca_order_id')
                needs_enrich = (t.get('qty', 0) == 0 or t.get('price', 0) == 0 or not t.get('notional'))
                if alpaca_id and needs_enrich:
                    try:
                        resp = sess.get(f'{client.base_url}/v2/orders/{alpaca_id}')
                        if resp.ok:
                            o = resp.json()
                            qty_val = float(o.get('filled_qty') or o.get('qty') or 0)
                            price_val = float(o.get('filled_avg_price') or 0)
                            notional_val = float(o.get('notional') or 0) or (qty_val * price_val)
                            t['qty'] = round(float(qty_val), 6) if qty_val else t.get('qty', 0)
                            t['price'] = round(float(price_val), 6) if price_val else t.get('price', 0)
                            t['filled_qty'] = float(o.get('filled_qty', 0))
                            t['notional'] = notional_val
                            t['filled_at'] = convert_timestamps_in_value(o.get('filled_at') or t.get('timestamp'))
                    except Exception:
                        pass
                # For dollar-based buys, if total_cost is still 0 but notional exists, use it as cost
                if t.get('amount_type') == 'dollar' and t['side'] == 'buy':
                    if float(t.get('total_cost', 0) or 0) <= 0 and float(t.get('notional', 0) or 0) > 0:
                        t['total_cost'] = round(float(t['notional']), 2)
                enriched.append(t)
    
    # Post-process: fill in missing total_cost for dollar-based buys and quantity trades
    for t in enriched:
        tc = float(t.get('total_cost') or 0)
        if t['side'] == 'buy' and t.get('amount_type') == 'dollar':
            # Dollar buy: total_cost should be the notional amount, even if engine left it at 0
            if tc <= 0:
                t['total_cost'] = round(float(t.get('notional', 0) or 0), 2)
        elif tc <= 0 and float(t.get('qty') or 0) > 0 and float(t.get('price') or 0) > 0:
            # Qty-based trade with missing total_cost: compute it
            t['total_cost'] = round(float(t['qty']) * float(t['price']), 6)
    
    # Display stored P\u0026L only — no inline computation.
    # P\u0026L is computed and stored at fill time in src/persistence/transaction_logger.py
    for t in enriched:
        if t['side'] == 'sell':
            pnl_d = t.get('pnl_dollars')
            pnl_p = t.get('pnl_pct')
            t['pnl_dollars'] = float(pnl_d) if pnl_d is not None else 0
            t['pnl_pct']     = float(pnl_p) if pnl_p is not None else 0
        else:
            t['pnl_dollars'] = 0
            t['pnl_pct'] = 0
    # Also add underscore-prefixed versions for frontend consumption (consistent with /api/trades)
    for t in enriched:
        t['_pnl_dollars'] = t.get('pnl_dollars', 0)
        t['_pnl_pct']     = t.get('pnl_pct', 0)
    return {"trades": enriched, "count": len(enriched)}


@router.get("/api/reports/strategy-all")
def report_strategy_all():
    """Full strategy evaluation history across all cycles (live engine_signals from trades.db (engine_signals table))."""
    import sqlite3
    SIGNALS_DB = os.path.join(os.path.dirname(__file__), "..", "..", "database", "trades.db")
    try:
        conn = sqlite3.connect(SIGNALS_DB)
        rows = conn.execute(
            "SELECT side, COUNT(*) as cnt FROM engine_signals GROUP BY side ORDER BY cnt DESC"
        ).fetchall()
        totals = [{"vote_result": r[0], "cnt": r[1]} for r in rows]

        sym_rows = conn.execute(
            "SELECT symbol, side, COUNT(*) as cnt FROM engine_signals GROUP BY symbol, side ORDER BY symbol LIMIT 200"
        ).fetchall()
        by_symbol = [{"symbol": r[0], "vote_result": r[1], "cnt": r[2]} for r in sym_rows]
        conn.close()
    except Exception:
        totals = []
        by_symbol = []

    return {"totals": totals, "by_symbol": by_symbol}


@router.get("/api/reports/bars")
def report_bars():
    """Bar data coverage stats from trades.db bars_crypto + bars_stock.
    
    Filters to active watchlist symbols only — disabled/inactive symbols are excluded.
    New active symbols automatically appear in the report when added to the config.
    """
    TRADING_DB = os.path.join(os.path.dirname(__file__), "..", "..", "database", "bars.db")

    # Determine active symbols from watchlist config FIRST
    active_symbols = set()
    try:
        wl = get_watchlist()
        if isinstance(wl, list):
            for asset in wl:
                sym = asset.get("symbol", "")
                if sym and asset.get("enabled", True):
                    active_symbols.add(sym)
    except Exception:
        pass  # fall through to legacy mode — include all DB rows

    # Freshness metadata from unified_logger (only active symbols)
    fresh_data_by_sym_tf = {}
    try:
        from src.log.unified_logger import check_bar_freshness_full
        for fd in check_bar_freshness_full(TRADING_DB, active_symbols=active_symbols if active_symbols else None):
            key = (fd.get("symbol", ""), fd.get("timeframe", ""))
            fresh_data_by_sym_tf[key] = {
                "_freshness_sec": fd.get("_freshness_sec"),
                "stale_threshold_sec": fd.get("stale_threshold_sec"),
                "is_stale": fd.get("is_stale"),
            }
    except Exception:
        pass

    conn = sqlite3.connect(TRADING_DB)
    try:
        # Check which bar tables exist
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bars_%'"
        ).fetchall()]
        
        if not tables:
            return []
        
        # Build query dynamically based on available tables
        queries = []
        for t in sorted(tables):
            queries.append(f"SELECT symbol, timeframe, timestamp, low, high, volume FROM {t}")
        combined = " UNION ALL ".join(queries)
        
        if active_symbols:
            placeholders = ",".join(["?" for _ in active_symbols])
            rows_sql = f"""
                SELECT symbol, timeframe, MIN(timestamp) as earliest, MAX(timestamp) as latest,
                       COUNT(*) as bar_count, MIN(low) as min_price, MAX(high) as max_price,
                       SUM(volume) as total_volume FROM (
                    {combined}
                ) WHERE symbol IN ({placeholders}) GROUP BY symbol, timeframe ORDER BY symbol
            """
            rows = conn.execute(rows_sql, list(active_symbols)).fetchall()
        else:
            # No filter — include all bars (legacy / no-watchlist mode)
            rows = conn.execute(f"""
                SELECT symbol, timeframe, MIN(timestamp) as earliest, MAX(timestamp) as latest,
                       COUNT(*) as bar_count, MIN(low) as min_price, MAX(high) as max_price,
                       SUM(volume) as total_volume FROM (
                    {combined}
                ) GROUP BY symbol, timeframe ORDER BY symbol
            """).fetchall()
        return [
            {
                "symbol": r[0],
                "timeframe": r[1] or r[1] or "-",
                "bar_count": r[4],
                "high": round(r[6], 6) if r[6] else None,
                "low": round(r[5], 6) if r[5] else None,
                "total_volume": r[7] or 0,
                "last_seen": r[3],
                **fresh_data_by_sym_tf.get((r[0], r[1] or ""), {}),
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/api/config")
def config():
    """Full system configuration."""
    wl = get_watchlist()
    return {
        "watchlist": wl,
        "strategy_groups": None,  # deprecated since 2026-07-19 (live engines use flat strategy lists)
    }


@router.get("/api/recent-events")
def recent_events(limit: int = Query(200, ge=1)):
    """Recent engine activity events."""
    return get_recent_engine_events(limit=limit)


@router.get("/api/errors-summary")
def errors_summary():
    """Error counts by severity."""
    return get_errors_by_severity()


@router.get("/api/reports/comprehensive/detailed")
def report_comprehensive_detailed(
    symbol: Optional[str] = None,
    asset_class: Optional[str] = None,
):
    """Detailed comprehensive report data with per-asset filtering.
    
    Returns positions, trades (with cost-basis P&L), and strategy history,
    all optionally filtered by asset_class or specific symbol.
    
    Note on asset_class normalization:
    - Frontend sends 'stock' for stock assets
    - Positions table has mixed values: 'us_equity' and 'stock'
    - Trades table uses 'stock'
    - We match positions against BOTH values, and trades against the original input
    """
    # Build position class set to match all stock variants in positions table
    if asset_class == 'stock':
        position_classes = ['us_equity', 'stock']
    elif asset_class:
        position_classes = [asset_class]
    else:
        position_classes = None
    
    # Positions — also support filtering by individual symbol
    positions = _position_with_live(None)
    if position_classes:
        positions = [p for p in positions if p.get('asset_class','') in position_classes]
    if symbol and positions:
        # Symbol match: handle both base names (BTC, AVAX) and full symbols (BTC/USD, AVAX/USD)
        # _position_with_live returns {'symbol': 'BTC'} for deduped entries
        # so we need to compare the symbol field against base name AND full form
        sym_match = []
        for p in positions:
            s = p.get('symbol', '')
            # Direct match (exact same string)
            if s == symbol:
                sym_match.append(p)
            elif '/' not in symbol and '/' in s and s.startswith(symbol):
                # Symbol param is base name (e.g. 'BTC'), position has full form ('BTC/USD')
                sym_match.append(p)
            elif '/' in symbol and '/' not in s:
                # Symbol param is full (BTC/USD), position is base only — compare base of param
                base_of_sym = symbol
                for suffix in ['/USD', '/USDT', '/USDC', 'USD', 'USDT', 'USDC']:
                    if base_of_sym.endswith(suffix):
                        base_of_sym = base_of_sym[:-len(suffix)]
                        break
                if base_of_sym == s:
                    sym_match.append(p)
            elif '/' in symbol and '/' in s:
                # Both have slashes — strip suffix from both and compare bases
                sym_base = s
                param_base = symbol
                for suffix in ['/USD', '/USDT', '/USDC']:
                    if sym_base.endswith(suffix):
                        sym_base = sym_base[:-len(suffix)]
                    if param_base.endswith(suffix):
                        param_base = param_base[:-len(suffix)]
                if sym_base == param_base:
                    sym_match.append(p)
        positions = sym_match
    
    # Closed positions with realized P&L
    closed_sql = "SELECT * FROM positions WHERE is_closed=1 AND qty > 0"
    closed_positions = query_all(closed_sql)
    if position_classes:
        closed_positions = [p for p in closed_positions 
                           if p.get('asset_class','') in position_classes]
    if symbol:
        closed_positions = [p for p in closed_positions if p['symbol'] == symbol]
    
    # All trades (for this asset or filtered)
    # Do NOT filter qty > 0 here — dollar buys are stored with qty=0
    # until enriched from Alpaca. Enrichment happens below.
    trade_sql = "SELECT * FROM trades WHERE status='filled'"
    params = []
    if asset_class:
        trade_sql += " AND asset_class = ?"
        params.append(asset_class)
    if symbol:
        # Symbol may be base name (BTC) or full (BTC/USD); match both via LIKE
        trade_sql += " AND (symbol = ? OR symbol LIKE ?)"
        params.extend([symbol, f'{symbol}/%'])
    trade_sql += " ORDER BY timestamp DESC LIMIT 1000"
    all_trades = query_all(trade_sql, params)
    
    # Enrich zero-fill trades (qty=0 AND price=0) via Alpaca REST.
    # Dollar buys often have qty=0 until backfilled from Alpaca's fill data.
    if any(
        float(t.get('qty') or 0) == 0 and float(t.get('price') or 0) == 0
        for t in all_trades
    ):
        _src_path = Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src_path) not in sys.path:
            sys.path.insert(0, str(_src_path))
        try:
            from data.alpaca_rest import AlpacaRestClient as _AlpacaRest
            from persistence.credentials import load_credentials as _load_creds
            _creds = _load_creds()
            _client = _AlpacaRest(_creds.alpaca.api_key, _creds.alpaca.secret_key, _creds.alpaca.paper)
            _sess = _client._session()
        except Exception:
            pass  # keep trades as-is if enrichment fails
        else:
            enriched = []
            for t in all_trades:
                t = dict(t)
                needs_enrich = (
                    float(t.get('qty') or 0) == 0 and float(t.get('price') or 0) == 0
                )
                if needs_enrich and t.get('status') == 'filled' and (t.get('alpaca_order_id') or t.get('client_order_id')):
                    oid = t.get('alpaca_order_id') or t.get('client_order_id')
                    try:
                        resp = _sess.get(
                            f'{_client.base_url}/v2/orders/{oid}',
                            timeout=5,
                        )
                        if resp.ok:
                            o = resp.json()
                            qty_val = float(o.get('filled_qty') or o.get('qty') or 0)
                            price_val = float(o.get('filled_avg_price') or 0)
                            notional_val = (
                                float(o.get('notional') or 0) or (qty_val * price_val)
                            )
                            t['qty'] = round(float(qty_val), 6) if qty_val else t.get('qty', 0)
                            t['price'] = round(float(price_val), 6) if price_val else t.get('price', 0)
                            t['notional'] = notional_val
                            t['total_cost'] = round(qty_val * (price_val or 0), 6)
                    except Exception:
                        pass
                enriched.append(t)
            all_trades = enriched
    
    # Build per-symbol FIFO P&L from trades
    symbol_trades = {}
    for t in all_trades:
        sym = t['symbol']
        if sym not in symbol_trades:
            symbol_trades[sym] = []
        symbol_trades[sym].append(t)
    
    # Build per-symbol P&L from **stored** trade values (computed at fill time)
    # No inline FIFO — stored pnl_dollars on each trade row is the source of truth.
    detailed_pnl = {}
    for sym, trades_list in symbol_trades.items():
        sells = [t for t in trades_list if t['side'] == 'sell']
        buys = [t for t in trades_list if t['side'] == 'buy']
        total_buy_notional = sum(t.get('total_cost', 0) or 0 for t in buys)
        sell_total = sum((t.get('notional') or 0) for t in sells)
        # Sum stored per-trade P&L
        realized = sum(float(t.get('pnl_dollars', 0) or 0) for t in sells)
        detailed_pnl[sym] = {
            'realized_pnl': round(realized, 2),
            'total_buy_notional': round(total_buy_notional, 2),
            'total_sell_notional': round(sell_total, 2),
            'total_trades': len(trades_list),
            'buy_count': len(buys),
            'sell_count': len(sells),
        }
    
    # Strategy history — read from live engine_signals in trades.db (engine_signals table)
    SIGNALS_DB = os.path.join(os.path.dirname(__file__), "..", "..", "database", "trades.db")
    strategy_history = []
    try:
        conn = sqlite3.connect(SIGNALS_DB)
        base_sql = "SELECT symbol, side as vote_result, confidence, engine, timestamp as timestamp FROM engine_signals"
        strat_params = []
        if asset_class and not symbol:
            wl_assets = get_watchlist()
            if isinstance(wl_assets, dict):
                wl_assets = wl_assets.get('assets', [])
            symbols_for_class = []
            for a in wl_assets:
                sym = a.get('symbol', '')
                if asset_class == 'crypto' and '/USD' in sym:
                    symbols_for_class.append(sym)
                elif asset_class == 'stock' and '/' not in sym:
                    symbols_for_class.append(sym)
            if symbols_for_class:
                placeholders = ','.join(['?' for _ in symbols_for_class])
                base_sql += f" WHERE symbol IN ({placeholders})"
                strat_params.extend(symbols_for_class)
        elif symbol:
            base_sql += " WHERE (symbol = ? OR symbol LIKE ?)"
            strat_params.extend([symbol, f'{symbol}/%'])
        base_sql += " ORDER BY timestamp DESC LIMIT 500"
        # Build column names for the query
        cursor = conn.execute(base_sql, strat_params)
        cols = [desc[0] for desc in cursor.description]
        strategy_history = [dict(zip(cols, row)) for row in cursor.fetchall()]
        # Normalize to match frontend expectations
        for s in strategy_history:
            s['vote_result'] = (s.get('vote_result') or '').upper()  # BUY/SELL/HOLD
            s['strategy_group'] = ''  # deprecated; use 'strategy' instead
            # Convert timestamp string to EDT for frontend display
            from dashboard.utils.tz_convert import convert_timestamps_in_value as _tz
            ts = s.get('timestamp')
            if not ts:
                s['timestamp'] = s.get('created_at', '')
            s['timestamp'] = _tz(convert_timestamps_in_value(s['timestamp']))
        conn.close()
    except Exception:
        strategy_history = []
    
    # Summarize strategy data
    strat_totals = {}
    for s in strategy_history:
        key = f"{s.get('strategy_group','')}|{s.get('vote_result','')}"
        strat_totals[key] = strat_totals.get(key, 0) + 1

    # Map stored pnl_dollars/pnl_pct → _pnl_dollars/_pnl_pct for frontend consumption.
    for t in all_trades:
        if t['side'] == 'sell':
            pnl_d = t.get('pnl_dollars')
            pnl_p = t.get('pnl_pct')
            t['_pnl_dollars'] = float(pnl_d) if pnl_d is not None else 0.0
            t['_pnl_pct']     = float(pnl_p) if pnl_p is not None else 0.0
        else:
            t['_pnl_dollars'] = 0
            t['_pnl_pct'] = 0
    
    return {
        'positions': positions,
        'closed_positions': closed_positions,
        'trades': all_trades,
        'pnl_summary': detailed_pnl,
        'strategy_history': strategy_history,
        'strategy_totals': strat_totals,
        'filters_applied': {'symbol': symbol, 'asset_class': asset_class},
    }



# ── Signal Evaluations Report (CryptoSwingDaily success metrics) ────────────────
@router.get("/api/reports/signal-evaluations")
def report_signal_evaluations():
    """Signal evaluation data for strategy success measurement.

    Merges signal_evaluations (engine signals/trade outcomes) with the
    positions table to capture live and closed positions that exist on
    Alpaca but may be missing from eval records.
    """
    TRADING_DB = os.path.join(os.path.dirname(__file__), "..", "..", "database", "bars.db")
    conn = sqlite3.connect(TRADING_DB)
    try:
        # All evals
        all_rows = conn.execute(
            "SELECT * FROM signal_evaluations ORDER BY id"
        ).fetchall()
        prg = [d for d in conn.execute("PRAGMA table_info(signal_evaluations)").fetchall()]
        cols = [c[1] for c in prg]  # c[0]=cid, c[1]=name
        all_data = [dict(zip(cols, r)) for r in all_rows]

        # CryptoSwingDaily only
        csw = conn.execute(
            "SELECT * FROM signal_evaluations WHERE strategy_group='CryptoSwingDaily' ORDER BY id"
        ).fetchall()
        csw_data = [dict(zip(cols, r)) for r in csw]

        # Strategy groups summary (from evals only — don't mix position counts here)
        grouped = conn.execute(
            "SELECT strategy_group, COUNT(*) as total, SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed, "
            "SUM(CASE WHEN status='closed' AND COALESCE(realized_pnl_pct,0)>0 THEN 1 ELSE 0 END) as wins FROM signal_evaluations GROUP BY strategy_group"
        ).fetchall()
        strats = [dict(zip(['strategy_group','total_evals','closed_trades','win_trades'], g)) for g in grouped]

        # Kill switch status (live only)
        total_closed = len([e for e in all_data if e.get('status') == 'closed'])
        csw_closed = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(realized_pnl_usd),0), MIN(COALESCE(realized_pnl_pct,999)) "
            "FROM signal_evaluations WHERE strategy_group='CryptoSwingDaily' AND status='closed'"
        ).fetchone()

        # ── Live positions from the positions table (authoritative source) ──
        all_pos_rows = conn.execute(
            "SELECT * FROM positions WHERE asset_class='crypto' ORDER BY symbol"
        ).fetchall()
        pos_cols = [d[1] for d in conn.execute("PRAGMA table_info(positions)").fetchall()]
        all_positions = [dict(zip(pos_cols, r)) for r in all_pos_rows]

        # Filter out garbage/corrupted positions (negative avg_cost beyond reason,
        # zero qty with nonzero invested) before merging.
        valid_live_positions = []
        invalid_positions = []
        for pos in all_positions:
            sym = pos.get('symbol', '')
            qty = float(pos.get('qty', 0) or 0)
            avg = float(pos.get('avg_cost', 0) or 0)
            invested = float(pos.get('total_invested', 0) or 0)
            # Skip corrupted rows
            if avg < -1e6 or (qty == 0 and abs(invested) > 0):
                invalid_positions.append(pos)
                continue
            valid_live_positions.append(pos)
        
        closed_pos_rows = [
            pos for pos in all_positions
            if float(pos.get('qty', 0) or 0) <= 0 or pos.get('is_closed', False)
        ]

        # Build a lookup: symbol (no-slash) -> eval records
        def normalize(sym):
            return sym.replace('/', '')

        eval_by_symbol_open = {}
        for e in all_data:
            ns = normalize(e.get('symbol', ''))
            if e.get('status') in ('open', 'executed') and ns:
                if ns not in eval_by_symbol_open:
                    eval_by_symbol_open[ns] = []
                eval_by_symbol_open[ns].append(e)

        # Merge live positions with eval data for each position
        merged_live = []
        for pos in valid_live_positions:
            sym = pos.get('symbol', '')
            if float(pos.get('qty', 0)) <= 0 or pos.get('is_closed', False):
                continue
            ns = normalize(sym)
            evals_for_symbol = eval_by_symbol_open.get(ns, [])
            # Take the latest valid eval entry (highest qty/price > 0)
            best_eval = None
            for e in evals_for_symbol:
                if float(e.get('entry_qty', 0) or 0) > 0 and float(e.get('entry_price', 0) or 0) > 0:
                    if best_eval is None or e.get('id', 0) > best_eval.get('id', 0):
                        best_eval = e
            merged_live.append({
                'position': pos,
                'eval': best_eval,
                'has_eval_record': best_eval is not None,
                'symbol_normalized': ns,
                'display_symbol': sym.replace('USD', '/USD') if not '/' in sym else sym,
            })

        # Closed positions merged with eval outcome data
        merged_closed = []
        closed_eval_by_symbol = {}
        for e in all_data:
            if e.get('status') == 'closed':
                ns = normalize(e.get('symbol', ''))
                if ns not in closed_eval_by_symbol:
                    closed_eval_by_symbol[ns] = []
                closed_eval_by_symbol[ns].append(e)

        for pos in closed_pos_rows:
            sym = pos.get('symbol', '')
            ns = normalize(sym)
            evals_for_closed = closed_eval_by_symbol.get(ns, [])
            merged_closed.append({
                'position': pos,
                'eval_records': evals_for_closed,
                'display_symbol': sym.replace('USD', '/USD') if not '/' in sym else sym,
            })

        conn.close()
    except Exception as e:
        all_data = []
        csw_data = []
        strats = []
        csw_closed = (0, 0, 999)
        merged_live = []
        merged_closed = []

    # Build a trades lookup for real engine outcomes (not eval backtest data).
    # Handles symbol normalization: trades store LINK/USD but positions store LINKUSD.
    TRADING_DB = os.path.join(os.path.dirname(__file__), "..", "..", "database", "bars.db")
    conn2 = sqlite3.connect(TRADING_DB)
    try:
        trade_rows = conn2.execute(
            "SELECT symbol, side, qty, price, total_cost, commission, strategy, tp_level, created_at "
            "FROM trades WHERE price > 0 ORDER BY id"
        ).fetchall()
        # Normalize trade symbols for matching against positions
        norm_trade_lookup = {}
        for tr in trade_rows:
            ns = tr[0].replace('/', '')  # LINK/USD -> LINKUSD
            if ns not in norm_trade_lookup:
                norm_trade_lookup[ns] = []
            norm_trade_lookup[ns].append(dict([
                ('symbol', tr[0]), ('side', tr[1]), ('qty', tr[2]),
                ('price', tr[3]), ('total_cost', tr[4]), ('commission', tr[5]),
                ('strategy', tr[6]), ('tp_level', tr[7]),
                ('created_at', tr[8])
            ]))

        # Build per-symbol position P&L from trades (real engine data)
        symbol_trades_pnl = {}
        for sym_ns, trades in norm_trade_lookup.items():
            buys = [t for t in trades if t['side'] == 'buy' and float(t['qty']) > 0]
            sells = [t for t in trades if t['side'] == 'sell' and float(t['qty']) < 0]
            if not sells:
                continue
            buy_qty_total = sum(float(b['qty']) for b in buys)
            buy_cost_total = sum(float(b['total_cost']) or 0 for b in buys)
            sell_qty_total = abs(sum(float(s['qty']) for s in sells))
            sell_rev_total = sum(abs(float(s['qty'])) * float(s['price']) for s in sells)
            
            avg_entry = buy_cost_total / buy_qty_total if buy_qty_total > 0 else 0
            sold_pct = sell_qty_total / buy_qty_total * 100 if buy_qty_total > 0 else 0
            realized_pnl = sell_rev_total - (avg_entry * sell_qty_total)
            
            symbol_trades_pnl[sym_ns] = {
                'realized_pnl_usd': round(realized_pnl, 2),
                'sold_pct_of_position': round(sold_pct, 1),
                'num_buys': len(buys),
                'num_sells': len(sells),
                'avg_entry_price': round(avg_entry, 6) if avg_entry > 0 else None,
            }

        # Attach trade-derived P&L to each position in merged_live
        for item in merged_live:
            ns = item['symbol_normalized']
            pnl_data = symbol_trades_pnl.get(ns)
            pos_row = item['position']
            # Compute unrealized P&L using the corrected avg_cost
            qty = float(pos_row.get('qty', 0) or 0)
            avg_cost = float(pos_row.get('avg_cost', 0) or 0)
            cur_price = float(pos_row.get('current_price', 0) or 0)
            unrealized = (cur_price - avg_cost) * qty if qty > 0 and avg_cost > 0 else 0
            
            item['unrealized_pnl'] = round(unrealized, 2)
            item['pnl_pct'] = round(((cur_price - avg_cost) / avg_cost * 100), 4) if avg_cost > 0 else None
            item['trades_summary'] = pnl_data

        # Same for closed positions (qty=0 or is_closed)
        for item in merged_closed:
            ns = item['symbol_normalized']
            pnl_data = symbol_trades_pnl.get(ns)
            pos_row = item['position']
            qty = float(pos_row.get('qty', 0) or 0)
            avg_cost = float(pos_row.get('avg_cost', 0) or 0)
            cur_price = float(pos_row.get('current_price', 0) or 0)
            unrealized = (cur_price - avg_cost) * qty if qty > 0 and avg_cost > 0 else 0
            item['unrealized_pnl'] = round(unrealized, 2)
            item['trades_summary'] = pnl_data

        conn2.close()
    except Exception:
        symbol_trades_pnl = {}

    return {
        'all_evaluations': all_data,
        'crypto_swing_daily': csw_data,
        'strategy_groups': strats,
        'kill_switch': {
            'total_trades': total_closed,
            'csw_trades': csw_closed[0] if csw_closed else 0,
            'csw_total_pnl_usd': csw_closed[1] if csw_closed else 0,
            'csw_min_pnl_pct': csw_closed[2] if csw_closed else None,
        },
        'live_positions': merged_live,
        'closed_positions_data': merged_closed,
    }
