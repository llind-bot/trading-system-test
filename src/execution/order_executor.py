"""Order Executor - submits pending orders from DB to Alpaca and updates all tracking tables.

Usage:
  cd ~/trading-system-test && python3 src/execution/order_executor.py   # run once
"""
import os, sys, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent.parent  # trading-system-test
sys.path.insert(0, str(BASE))
os.environ.setdefault('ALPACA_ENV', 'paper')

with open(BASE / 'config' / '.env') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            os.environ[k] = v

from alpaca_trade_api.rest import REST
import sqlite3

DB_PATH = str(BASE / 'database' / 'trades.db')

def get_conn():
    return sqlite3.connect(DB_PATH)

rest = REST(os.environ['ALPACA_API_KEY'], os.environ['ALPACA_SECRET_KEY'],
            os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets'))


def get_pending_orders():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, symbol, side, qty, price, source, confidence, strategy FROM orders WHERE status='pending'"
        ).fetchall()
        return [dict(id=r[0], symbol=r[1], side=r[2], qty=float(r[3]), price=float(r[4]),
                      source=str(r[5]), confidence=float(r[6]) if r[6] else 0, strategy=str(r[7]))
                for r in rows]
    finally:
        conn.close()


def cancel_open_orders():
    """Cancel any stale open orders on Alpaca."""
    live = rest.list_orders(status='open')
    for o in live:
        print(f"  Cancelling stale order: {o.id} ({o.symbol} {o.side})")
        rest.cancel_order(o.id)
    time.sleep(1)


def get_market_price(symbol):
    """Get mid-market price for a symbol."""
    quotes = rest.get_quotes(symbol)
    if isinstance(quotes, list) and len(quotes) > 0:
        qd = quotes[0]
        bp = getattr(qd, 'bid_price', None) or (qd._raw.get('bp') if hasattr(qd, '_raw') else None)
        ap = getattr(qd, 'ask_price', None) or (qd._raw.get('ap') if hasattr(qd, '_raw') else None)
        if bp and ap:
            return float(bp + ap) / 2
    return None


def submit_and_wait(order, wait_seconds=60):
    """Submit order to Alpaca and poll until filled/cancelled. Returns (status, fill_price_or_None)."""
    side = 'buy' if order['side'] == 'BUY' else 'sell'
    
    # Try limit first; fall back to market if no liquidity
    mid = get_market_price(order['symbol'])
    bp = 0; as_ = 0
    try:
        quotes = rest.get_quotes(order['symbol'])
        qd = quotes[0]
        bp = qd._raw.get('bp') or 0
        as_ = qd._raw.get('as') or 0
    except:
        pass
    
    if bp and ap and as_ > 0:
        limit_price = round(mid, 2) if mid else order['price']
        resp = rest.submit_order(symbol=order['symbol'], qty=str(order['qty']), side=side,
                                  type='limit', limit_price=str(limit_price),
                                  time_in_force='day', extended_hours=True)
    else:
        print(f"  No ask-side liquidity - using market order")
        resp = rest.submit_order(symbol=order['symbol'], qty=str(order['qty']), side=side,
                                  type='market', time_in_force='day', extended_hours=False)
    
    alpaca_id = resp.id
    
    # Poll for fill
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(3)
        sr = rest.get_order(alpaca_id)
        status_str = str(sr.status).lower()
        
        if 'fill' in status_str:
            fp = getattr(sr, 'filled_avg_price', None) or (sr._raw.get('filled_avg_price') if hasattr(sr, '_raw') else None)
            return ('filled', float(fp) if fp else None)
        elif status_str in ('canceled', 'rejected'):
            return (status_str, None)
    
    # Still live
    final = rest.get_order(alpaca_id)
    return (str(final.status).lower(), None)


def update_order_filled(order_id, alpaca_id, filled_price):
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE orders SET status='filled', alpaca_order_id=?, filled_at=? WHERE id=?",
                     (alpaca_id, now, order_id))
        
        sym = conn.execute("SELECT symbol FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        side_val = conn.execute("SELECT side FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        qty = conn.execute("SELECT qty FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        strat = conn.execute("SELECT strategy FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        conf = conn.execute("SELECT confidence FROM orders WHERE id=?", (order_id,)).fetchone()[0]
        
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, asset_class, side, qty, price, strategy, confidence, order_id, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now, sym, 'stock', 'BUY' if side_val == 'BUY' else 'SELL', qty, filled_price, strat, conf, alpaca_id, 'filled', 'filled_at_alpaca')
        )
        
        # Update/create position
        existing = conn.execute("SELECT * FROM positions WHERE symbol=?", (sym,)).fetchone()
        if existing:
            cols_p = [c[1] for c in conn.execute("PRAGMA table_info(positions)").fetchall()]
            old_qty = float(existing[cols_p.index('qty')])
            old_avg = float(existing[cols_p.index('avg_cost')])
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + qty * filled_price) / new_qty if new_qty > 0 else filled_price
            conn.execute("UPDATE positions SET qty=?, avg_cost=?, current_price=?, updated_at=? WHERE symbol=?",
                        (new_qty, new_avg, filled_price, now, sym))
        else:
            conn.execute(
                "INSERT INTO positions (symbol, asset_class, qty, avg_cost, current_price, is_closed, updated_at) VALUES (?,?,?,?,?,?,?)",
                (sym, 'stock', qty, filled_price, filled_price, 0, now)
            )
        conn.commit()
    finally:
        conn.close()


def update_order_error(order_id, err_msg):
    conn = get_conn()
    try:
        conn.execute("UPDATE orders SET status='error', error=? WHERE id=?", (err_msg[:500], order_id))
        conn.commit()
    finally:
        conn.close()


def main():
    print("=" * 60)
    print("ORDER EXECUTOR")
    print("=" * 60)
    
    acct = rest.get_account()
    print(f"Account: {acct.id}")
    print(f"Cash: ${float(acct.cash):,.2f} | Equity: ${float(acct.equity):,.2f}")
    print("-" * 60)
    
    # Clean up stale orders on Alpaca
    cancel_open_orders()
    
    pending = get_pending_orders()
    if not pending:
        print("No pending orders found.")
        return
    
    print(f"\nFound {len(pending)} pending order(s):\n")
    for o in pending:
        print(f"  Order #{o['id']}: {o['symbol']} {o['side']} qty={o['qty']} price=${o['price']:.2f} strategy={o['strategy']} confidence={o['confidence']}")
    
    for order in pending:
        print(f"\n{'─' * 60}")
        print(f"Processing Order #{order['id']}: {order['symbol']} {order['side']} qty={order['qty']}")
        
        status, fill_price = submit_and_wait(order)
        
        if status == 'filled' and fill_price:
            update_order_filled(order['id'], order.get('alpaca_order_id', ''), fill_price)
            print(f"  ✓ FILLED @ ${fill_price:.2f}")
            
            # Update positions table too
            pos_qty = order['qty']
            pos_avg = fill_price
            existing = get_conn().execute("SELECT * FROM positions WHERE symbol=?", (order['symbol'],)).fetchone()
            if not existing:
                now = datetime.now(timezone.utc).isoformat()
                get_conn().execute(
                    "INSERT INTO positions (symbol, asset_class, qty, avg_cost, current_price, is_closed, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (order['symbol'], 'stock', pos_qty, pos_avg, pos_avg, 0, now)
                )
                get_conn().commit()
                print(f"  ✓ Position created: {order['symbol']} qty={pos_qty} avg_cost=${pos_avg:.2f}")
        else:
            update_order_error(order['id'], f"Order not filled: {status}")
            print(f"  ✗ Order status: {status}")
    
    # Validation
    print(f"\n{'=' * 60}")
    print("VALIDATION")
    print("=" * 60)
    
    conn = get_conn()
    cols_o = [c[1] for c in conn.execute("PRAGMA table_info(orders)").fetchall()]
    print("\nOrders:")
    for oid in [o['id'] for o in pending]:
        row = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if row:
            d = dict(zip(cols_o, row))
            for k, v in d.items():
                print(f"  {k}: {v}")
    
    trade_count = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
    print(f"\nTrades: {trade_count}")
    if trade_count > 0:
        cols_t = [c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()]
        for t in conn.execute("SELECT * FROM trades").fetchall():
            d = dict(zip(cols_t, t))
            print(f"  {d}")
    
    pos_count = conn.execute("SELECT count(*) FROM positions").fetchone()[0]
    print(f"\nPositions: {pos_count}")
    if pos_count > 0:
        cols_p = [c[1] for c in conn.execute("PRAGMA table_info(positions)").fetchall()]
        for p in conn.execute("SELECT * FROM positions").fetchall():
            d = dict(zip(cols_p, p))
            print(f"  {d}")
    
    live = rest.list_orders(status='open')
    filled = rest.list_orders(status='filled')
    print(f"\nAlpaca open: {len(live)} | Alpaca filled: {len(filled)}")
    conn.close()
    print("\nDone.")


if __name__ == '__main__':
    main()
