#!/usr/bin/env bash
# Engine restart — manages all four trading engines as standalone background processes.
# Usage: ./restart_engine.sh [--dry]
#
# NOTE: launchd plists are DISABLED. Engines run as manual background processes only.
# No auto-respawn, no pkill cascades. Each engine is a long-lived process.

set -euo pipefail

DRY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry) DRY=true; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

TRADE_ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "[restart] ===== Starting engine restart ====="
echo "  TRADE_ROOT: $TRADE_ROOT"

if [[ "$DRY" == true ]]; then
    echo "[dry-run] Would kill and restart the following engines:"
    for name in bar_ingest crypto_engine stock_engine order_server; do
        echo "  $name -> logs/${name}.log"
    done
    exit 0
fi

# ── Step 1: Kill all old engine processes ─────────
echo "[step 1] Killing old engines..."
PATTERN="stock_engine|crypto_engine|bar_ingest|order_server"
pids=$(pgrep -f "$PATTERN" 2>/dev/null || true)
if [[ -n "$pids" ]]; then
    for pid in $pids; do kill $pid 2>/dev/null || true; done
    sleep 2
    pids=$(pgrep -f "$PATTERN" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        for pid in $pids; do kill -9 $pid 2>/dev/null || true; done
        sleep 1
    fi
fi
remaining=$(pgrep -f "$PATTERN" 2>/dev/null || true)
if [[ -n "$remaining" ]]; then
    echo "  [ERROR] Engines still alive after kill attempt."
    exit 1
fi
echo "  ✅ All engines confirmed dead"

# ── Step 2: Clear stale .pyc cache ───────────────
echo "[step 2] Clearing .pyc cache..."
find "$TRADE_ROOT/src" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$TRADE_ROOT/strategies" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── Step 3: Start each engine as a background process ─────────
echo "[step 3] Starting engines..."
export PYTHONPATH="$TRADE_ROOT"
export PYTHONUNBUFFERED=1

for name in bar_ingest crypto_engine stock_engine order_server; do
    cmd="python3 -u $TRADE_ROOT/engine/${name}.py"
    log_file="$TRADE_ROOT/logs/${name}.log"
    
    nohup $cmd > "$log_file" 2>&1 &
    pid=$!
    echo "  ✅ ${name} started (PID $pid) -> ${log_file}"
done

sleep 3

# ── Step 4: Verify all engines are running ─────────
echo "[step 4] Verifying engines..."
all_alive=true
for name in bar_ingest crypto_engine stock_engine order_server; do
    pid=$(pgrep -f "$name" 2>/dev/null | head -1 || true)
    if [[ -n "$pid" ]] && kill -0 $pid 2>/dev/null; then
        echo "  ✅ ${name} (PID $pid) is running"
    else
        echo "  ❌ ${name} FAILED to start"
        all_alive=false
    fi
done

if [[ "$all_alive" == "false" ]]; then
    echo "[warn] Some engines failed. Check logs:"
    for name in bar_ingest crypto_engine stock_engine order_server; do
        log="$TRADE_ROOT/logs/${name}.log"
        if [[ -s "$log" ]]; then
            echo "  $log (last 5):"
            tail -5 "$log" | sed 's/^/    /'
        fi
    done
fi

echo "[restart] ===== Done ====="
