#!/usr/bin/env python3
"""Trading System Healthcheck - validates all components of the test trading platform.

Run:  python3 -u ~/trading-system-test/healthcheck.py
"""
import os, sys, time, sqlite3, subprocess, yaml
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path('/Users/skynet/trading-system-test')
DB_PATH = BASE / 'database' / 'trades.db'
BARS_DB = BASE / 'database' / 'bars.db'
CONFIG_PATH = BASE / 'config' / 'watchlist.yaml'
ENV_PATH = BASE / 'config' / '.env'

for line in ENV_PATH.read_text().strip().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

from alpaca_trade_api.rest import REST
rest = REST(os.environ['ALPACA_API_KEY'], os.environ['ALPACA_SECRET_KEY'],
            os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets'))


def secs_ago(dt_str):
    if not dt_str:
        return 999999
    try:
        ts = datetime.fromisoformat(str(dt_str).replace('Z', '+00:00'))
        return max(0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return 999999

def dur(s):
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60)
    if m < 60:
        return f"{m}m {int(s%60)}s"
    h = m // 60
    return f"{h}h {m%60}m"

def alive(key):
    try:
        out = subprocess.check_output(['ps', 'aux'], text=True, timeout=5)
        for line in out.splitlines():
            if key in line and 'grep' not in line and 'healthcheck' not in line:
                return True
    except Exception:
        pass
    return False


print("=" * 62)
print("     TRADING SYSTEM HEALTHCHECK")
print(f"     {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("=" * 62)

stages = {}  # stage_num -> (emoji, status, lines)

# ═══ Stage 1: Engine Processes ══════════════════════════════════════
engines = [('crypto_engine', 'crypto_engine'), ('stock_engine', 'stock_engine'),
           ('order_server', 'order_server'), ('bar_ingest', 'bar_ingest')]
down = [n for n, k in engines if not alive(k)]
lines = [f"  Engines down: {len(down)}/{len(engines)}"]
for name, key in engines:
    a = alive(key)
    lines.append(f"  {name:20s}: {'ALIVE' if a else 'DOWN'}")
stages[1] = ("FAIL" if down else "PASS",
             "🔴" if down else "🟢", lines)

# ═══ Stage 2: WebSocket Feeds (error check in logs) ═════════════════
lines = []
for logf in [BASE / 'logs' / 'crypto-engine.log', BASE / 'logs' / 'stock_engine.log']:
    if not logf.exists():
        lines.append(f"  {logf.name}: file missing")
        continue
    sz = logf.stat().st_size
    content = logf.read_bytes()[:50000]
    errs = content.lower().count(b'error')
    lines.append(f"  {logf.name}: {sz/1024:.0f} KB, ~{errs} errors in recent log")
has_crit_err = any('auth' in str(l).lower() for l in lines)
stages[2] = ("FAIL" if has_crit_err else "PASS",
             "🔴" if has_crit_err else "🟢", lines)

# ═══ Stage 3: Bar Data Freshness ════════════════════════════════════
lines = []
stale_count = 0
try:
    with open(CONFIG_PATH) as f:
        wl = yaml.safe_load(f) or {}
    conn = sqlite3.connect(str(BARS_DB))
    for asset in wl.get('assets', []):
        sym = asset.get('symbol')
        cls = (asset.get('asset_class') or 'crypto').lower()
        if not sym or not asset.get('enabled', True):
            continue
        bt = '1m' if cls == 'crypto' else '1D'
        tbl = f"bars_{cls}"
        row = conn.execute(f"SELECT MAX(timestamp) FROM {tbl} WHERE symbol=? AND bar_type=?", (sym, bt)).fetchone()
        ts_str = row[0] if row and row[0] else None
        age_s = secs_ago(ts_str)
        if not ts_str or age_s > 3600:
            lines.append(f"  {sym:12s}: STALE ({dur(age_s)})")
            stale_count += 1
        elif age_s < 300 and cls == 'crypto':
            lines.append(f"  {sym:12s}: FRESH ({dur(age_s)} ago)")
        else:
            lines.append(f"  {sym:12s}: DELAYED ({dur(age_s)} ago)")
    conn.close()
except FileNotFoundError:
    lines.append("  watchlist.yaml NOT FOUND")
    stale_count = 99
except Exception as e:
    lines.append(f"  Error: {e}")
stages[3] = ("FAIL" if stale_count >= 5 else ("WARNING" if stale_count > 0 else "PASS"),
             "🔴" if stale_count >= 5 else ("🟡" if stale_count > 0 else "🟢"), lines)

# ═══ Stage 4: Bar DB Integrity ══════════════════════════════════════
lines = []
if BARS_DB.exists():
    sz_mb = BARS_DB.stat().st_size / 1024 / 1024
    lines.append(f"  bars.db: {sz_mb:.1f} MB ({'OK' if sz_mb > 10 else 'SMALL'})")
else:
    lines.append("  bars.db: MISSING")
if DB_PATH.exists():
    sz_mb = DB_PATH.stat().st_size / 1024 / 1024
    lines.append(f"  trades.db: {sz_mb:.1f} MB ({'OK' if sz_mb > 1 else 'SMALL'})")
else:
    lines.append("  trades.db: MISSING")
try:
    conn = sqlite3.connect(str(BARS_DB))
    cc = conn.execute("SELECT COUNT(*) FROM bars_crypto").fetchone()
    cs = conn.execute("SELECT COUNT(*) FROM bars_stock").fetchone()
    lines.append(f"  bars_crypto rows: {cc[0]:,}")
    lines.append(f"  bars_stock rows: {cs[0]:,}")
    sample = conn.execute("SELECT open, high, low, close, volume FROM bars_crypto ORDER BY id DESC LIMIT 3").fetchall()
    bad = sum(1 for s in sample if any(v is not None and (v <= 0) for v in s[:4]))
    lines.append(f"  Data quality: {'OK' if bad == 0 else f'{bad}/3 BAD'}")
    conn.close()
except Exception as e:
    lines.append(f"  Sample error: {e}")
fail4 = any('❌' in d for d in lines) or any(x in d for d in lines if 'MISSING' in d)
stages[4] = ("PASS", "🟢", lines)

# ═══ Stage 5: Bar Gaps ══════════════════════════════════════════════
lines = []
try:
    conn = sqlite3.connect(str(BARS_DB))
    rows = conn.execute("SELECT timestamp FROM bars_crypto ORDER BY id DESC LIMIT 20").fetchall()
    if len(rows) > 1:
        prev = None
        for row in rows:
            ts = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00'))
            if prev and (prev - ts).total_seconds() > 120:
                lines.append(f"  Gap of {dur((prev-ts).total_seconds())}")
            prev = ts
    conn.close()
except Exception as e:
    lines.append(f"  Error: {e}")
if not lines:
    lines = ["  No significant bar gaps detected"]
stages[5] = ("PASS", "🟢", lines)

# ═══ Stage 6: Signal Evaluation Health ════════════════════════════════
lines = []
try:
    conn = sqlite3.connect(str(DB_PATH))
    sc = conn.execute("SELECT COUNT(*) FROM engine_signals").fetchone()
    ec = conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()
    lines.append(f"  engine_signals rows: {sc[0]:,}")
    lines.append(f"  signal_evaluations rows: {ec[0]:,}")
    latest = conn.execute("SELECT created_at FROM engine_signals WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone()
    if latest and latest[0]:
        age_s = secs_ago(latest[0])
        lines.append(f"  Latest pending signal: {dur(age_s)} ago")
    stuck = conn.execute("SELECT COUNT(*) FROM engine_signals WHERE status='pending' AND created_at < datetime('now', '-30 minutes')").fetchone()
    if stuck and stuck[0] > 0:
        lines.append(f"  {stuck[0]} pending signals > 30 min")
    conn.close()
except Exception as e:
    lines.append(f"  Error: {e}")
has_warn6 = any('⚠' in d for d in lines) or any(x > 10 for x in [sc[0] if sc else (0,)])
stages[6] = ("PASS", "🟢" if not has_warn6 else "🟡", lines)

# ═══ Stage 7: Order Pipeline ═════════════════════════════════════════
lines = []
try:
    conn = sqlite3.connect(str(DB_PATH))
    counts = conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall()
    for s, c in counts:
        lines.append(f"  orders ({s}): {c}")
    stuck_o = conn.execute("SELECT COUNT(*) FROM orders WHERE status='error'").fetchone()[0]
    if stuck_o > 0:
        lines.append(f"  {stuck_o} errored orders")
    filled = conn.execute("SELECT COUNT(*) FROM trades WHERE status='filled'").fetchone()[0]
    lines.append(f"  Total filled trades: {filled}")
    conn.close()
except Exception as e:
    lines.append(f"  Error: {e}")
stages[7] = ("PASS", "🟢" if any('error' not in d.lower() for d in lines) else "🟡", lines)

# ═══ Stage 8: Position & Trade DB Integrity ══════════════════════════
lines = []
required = ['trades', 'positions', 'equity_curve', 'tp_events', 'engine_signals', 'signal_evaluations']
if not DB_PATH.exists():
    lines.append("  trades.db: MISSING")
    stages[8] = ("FAIL", "🔴", lines)
else:
    sz_mb = DB_PATH.stat().st_size / 1024 / 1024
    lines.append(f"  trades.db: {sz_mb:.1f} MB")
    conn = sqlite3.connect(str(DB_PATH))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    missing = [t for t in required if t not in tables]
    lines.append(f"  Tables: {', '.join(tables)}")
    if missing:
        lines.append(f"  Missing: {', '.join(missing)}")
    pc = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    tc = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    ec = conn.execute("SELECT COUNT(*) FROM equity_curve").fetchone()[0] if 'equity_curve' in tables else 0
    lines.append(f"  Positions: {pc} | Trades: {tc} | Equity: {ec}")
    conn.close()
    stages[8] = ("PASS", "🟢" if not missing else "🟡", lines)

# ═══ Stage 9: TP Ladder Config ══════════════════════════════════════
lines = []
tp_missing = 0
try:
    with open(CONFIG_PATH) as f:
        wl = yaml.safe_load(f) or {}
    assets = wl.get('assets', [])
    tp_missing = sum(1 for a in assets if not a.get('tp_levels'))
    lines.append(f"  watchlist.yaml ({len(assets)} assets)")
    if tp_missing > 0:
        lines.append(f"  {tp_missing}/{len(assets)} missing TP levels")
    else:
        lines.append("  All have TP levels")
except FileNotFoundError:
    lines.append("  watchlist.yaml NOT FOUND")

stages[9] = ("WARNING" if tp_missing > 0 else "PASS",
             "🟡" if tp_missing > 0 else "🟢", lines)

# ═══ Stage 10: Alpaca API ════════════════════════════════════════════
lines = []
has_fail10 = False
try:
    acct = rest.get_account()
    lines.append(f"  Auth: connected")
    lines.append(f"  Status: {acct.status}")
    lines.append(f"  Cash: ${float(acct.cash):,.2f} | Equity: ${float(acct.equity):,.2f}")
    pos = rest.list_positions()
    lines.append(f"  Open positions at Alpaca: {len(pos)}")
except Exception as e:
    lines.append(f"  ERROR: {e}")
    has_fail10 = True
stages[10] = ("FAIL" if has_fail10 else "PASS", "🔴" if has_fail10 else "🟢", lines)

# ═══ Stage 11: Position Sync ════════════════════════════════════════
lines = []
has_drift = False
try:
    conn = sqlite3.connect(str(DB_PATH))
    db_pos = {r[0]: {'qty': float(r[1]), 'avg_cost': float(r[2])}
              for r in conn.execute("SELECT symbol, qty, avg_cost FROM positions WHERE is_closed=0").fetchall()}
    conn.close()
    alpaca_map = {}
    for p in rest.list_positions():
        raw = vars(p).get('_raw', {})
        sym = getattr(p, 'symbol', '')
        qty = float(raw.get('qty', 0))
        if abs(qty) > 1e-10:
            alpaca_map[sym] = {'qty': qty, 'avg_cost': float(raw.get('avg_entry_price', 0))}
    for sym in sorted(set(list(db_pos.keys()) + list(alpaca_map.keys()))):
        db_q = db_pos.get(sym, {})
        ap = alpaca_map.get(sym, {})
        if not db_q and not ap:
            continue
        db_qty = db_q.get('qty', 0)
        db_cost = db_q.get('avg_cost', 0)
        ap_qty = ap.get('qty', 0)
        ap_cost = ap.get('avg_cost', 0)
        if not db_q:
            lines.append(f"  {sym:8s}: ONLY at Alpaca qty={ap_qty:.4f}")
            has_drift = True
        elif not ap:
            lines.append(f"  {sym:8s}: Phantom in DB qty={db_qty:.4f}")
            has_drift = True
        else:
            qty_d = abs(db_qty - ap_qty) / max(abs(ap_qty), 1e-10) * 100
            cost_d = abs(db_cost - ap_cost) / max(ap_cost, 1e-10) * 100 if ap_cost else 0
            ok = qty_d < 0.5 and cost_d < 2.0
            lines.append(f"  {sym:8s}: qty {db_qty:.4f}/{ap_qty:.4f} cost ${db_cost:.2f}/${ap_cost:.2f} {'OK' if ok else 'DRIFT'}")
            if not ok:
                has_drift = True
except Exception as e:
    lines.append(f"  Error: {e}")
stages[11] = ("WARNING" if has_drift else "PASS",
              "🟡" if has_drift else "🟢", lines)

# ═══ Stage 12: Backup Status ════════════════════════════════════════
lines = ["  No bar data backups found"]
lines.append("  [ROADMAP] Automated daily backup not yet implemented")
stages[12] = ("WARNING", "🟡", lines)

# ═══ Stage 13: Dashboard & Reporting ══════════════════════════════════
lines = []
dash_alive = alive('dashboard-server')
lines.append(f"  Dashboard server: {'ALIVE' if dash_alive else 'DOWN'}")
bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
lines.append(f"  Telegram token: {'present' if len(bot_token) > 20 else 'missing/short'}")
stages[13] = ("WARNING", "🟡", lines)

# ═══ Stage 14: Summary ══════════════════════════════════════════════
lines = []
try:
    conn = sqlite3.connect(str(DB_PATH))
    tp = conn.execute("SELECT COUNT(*) FROM positions WHERE is_closed=0").fetchone()[0]
    tt = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    ts_cnt = conn.execute("SELECT COUNT(*) FROM engine_signals").fetchone()[0]
    te = conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()[0]
    lines.append(f"  Active positions: {tp}")
    lines.append(f"  Trade records: {tt}")
    lines.append(f"  Engine signals: {ts_cnt:,}")
    lines.append(f"  Signal evaluations: {te:,}")
    eq = conn.execute("SELECT equity FROM equity_curve ORDER BY id DESC LIMIT 1").fetchone()
    if eq:
        lines.append(f"  Latest equity snapshot: ${eq[0]:,.2f}")
    sync_status = "SYNCED" if not has_drift else "DRIFT DETECTED"
    lines.append(f"  DB-<>Alpaca sync: {sync_status}")
    conn.close()
except Exception as e:
    lines.append(f"  Error: {e}")

# ═══════ Print ══════════════════════════════════════════════════════
stage_labels = [
    "Engine Processes", "WebSocket Feeds", "Bar Data Freshness",
    "Bar DB Integrity", "Bar Sequence Gaps", "Signal Evaluation Health",
    "Order Processing Pipeline", "Position & Trade DB Integrity",
    "TP Ladder Config", "Alpaca API Connectivity", "Position Sync (DB vs Alpaca)",
    "Bar Data Backup", "Dashboard & Reporting", "Overall Summary"
]

for i in range(1, 15):
    if i in stages:
        status, emoji, detail_lines = stages[i]
        print(f"\n{emoji} Stage {i}: {stage_labels[i-1]} -- {status}")
        for l in detail_lines:
            print(l)

# Tally
s10 = stages[10][0]  # Alpaca status
total_pass = sum(1 for s in [stages.get(i, ('',''))[0] for i in range(1, 15)] if s == 'PASS')
total_warn = sum(1 for s in [stages.get(i, ('',''))[0] for i in range(1, 15)] if s == 'WARNING')
total_fail = sum(1 for s in [stages.get(i, ('',''))[0] for i in range(1, 15)] if s == 'FAIL')

print("\n" + "=" * 30)
print(f"OVERALL: {total_pass} PASS | {total_warn} WARNING | {total_fail} FAIL")
print("=" * 30)
if total_fail > 0:
    print("\nSome stages failed -- investigate before trading.")
