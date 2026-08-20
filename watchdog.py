#!/usr/bin/env python3
"""Watchdog process for the trading system test environment.

Monitors all engine processes every 30 seconds. Restarts dead engines and notifies on failures.
Runs as a single long-lived process — no launchd per-engine.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add test repo to path
TRADE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADE_ROOT))

from infra.db_pool import get_db
from infra.logger import get_logger
from infra.notify_engine import get_notify


_log = get_logger("watchdog")
_notify = get_notify()

# Engine processes to monitor
ENGINES = {
    "bar_ingest": "engine/bar_ingest_test.py",
    "stock_engine": "engine/stock_engine.py",
    "crypto_engine": "engine/crypto_engine.py",
    "order_server": "engine/order_server.py",
}

STATUS_FILE = Path.home() / ".trading-test-engine-status.json"


def _get_status_file():
    """Load current engine status from file."""
    if STATUS_FILE.exists():
        with open(STATUS_FILE) as f:
            return json.load(f)
    return {}


def _save_status(status):
    """Save current engine status to file."""
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def _is_running(pid):
    """Check if a process with given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _restart_engine(name, script_path):
    """Kill existing and restart a single engine."""
    try:
        cmd = ["python3", "-u", str(TRADE_ROOT / script_path)]
        proc = subprocess.Popen(
            cmd,
            cwd=str(TRADE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pid = proc.pid
        _log.info(f"restart_engine", engine=name, pid=pid)
        
        # Verify it started
        time.sleep(2)
        if not _is_running(pid):
            err = f"{name} failed to start after restart"
            _log.error("engine_start_failed", engine=name, error=err)
            _notify.notify("engine_start_failed", err, severity="CRITICAL")
            return None
        
        # Update status file
        status = _get_status_file()
        status[name] = {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat(), "status": "running"}
        _save_status(status)
        
        return pid
    except Exception as e:
        err = f"restart error for {name}: {e}"
        _log.error("restart_error", engine=name, error=str(e))
        return None


def check_databases():
    """Check all database health."""
    db_names = ["trades", "trading"]
    results = {}
    for name in db_names:
        try:
            pool = get_db(name)
            info = pool.health_check()
            results[name] = {
                "exists": info.get("exists", False),
                "readable": info.get("readable", False),
                "wal_size": info.get("wal_size", 0),
                "tables": len(info.get("tables", [])),
                "error": info.get("error"),
            }
        except Exception as e:
            results[name] = {"error": str(e)}
    return results


def run():
    """Main watchdog loop."""
    _log.info("watchdog_start")
    
    status = _get_status_file()
    running = False
    
    def _shutdown(sig, frame):
        nonlocal running
        _log.info("watchdog_shutdown", signal=sig)
        running = False
    
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    
    while running:
        now = datetime.now(timezone.utc)
        status["last_check"] = now.isoformat()
        
        # Check each engine
        for name, script in ENGINES.items():
            pid = status.get(name, {}).get("pid")
            
            if pid and _is_running(pid):
                status[name] = {"pid": pid, "status": "running", "last_check": now.isoformat()}
            else:
                # Engine is dead — try to restart
                _log.warning("engine_dead", engine=name)
                
                # Only try restart if it was previously running (not missing from config)
                if name in status and status[name].get("status") == "running":
                    new_pid = _restart_engine(name, script)
                    if new_pid:
                        status[name]["pid"] = new_pid
                        status[name]["status"] = "running"
                        status[name]["last_restart"] = now.isoformat()
                        _notify.notify(
                            f"engine_restart_{name}",
                            f"{name} was dead and has been restarted (new PID {new_pid})",
                            severity="WARNING"
                        )
                    else:
                        _notify.notify(
                            f"engine_failed_restart_{name}",
                            f"Failed to restart {name}",
                            severity="CRITICAL"
                        )
                elif name not in status:
                    # Engine not yet configured — start it for the first time
                    new_pid = _restart_engine(name, script)
                    if new_pid:
                        status[name] = {"pid": new_pid, "status": "running", "started_at": now.isoformat()}
        
        # Check databases periodically (every 10 cycles = ~5 minutes)
        if int(now.timestamp()) % 300 < 60:
            db_results = check_databases()
            for name, result in db_results.items():
                if not result.get("exists"):
                    _notify.notify(f"db_missing_{name}", f"{name}.db does not exist", severity="CRITICAL")
                elif result.get("wal_size", 0) > 10_000_000:
                    pool = get_db(name)
                    pool.checkpoint()
                    _log.warning("wal_checkpoint", db=name, wal_size=result["wal_size"])
        
        _save_status(status)
        
        # Sleep before next cycle
        for _ in range(30):
            if not running:
                break
            time.sleep(1)
    
    _log.info("watchdog_exited")


if __name__ == "__main__":
    running = True
    run()
