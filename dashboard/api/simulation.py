"""Simulation (Strategy Lab) API endpoints — Dashboard integration (Phase 8)."""
import json
import logging
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import yaml

# Ensure strategy-lab is on the path for backend use
_STRATLAB_PATH = str(Path(__file__).resolve().parent.parent.parent / "strategy-lab")
if _STRATLAB_PATH not in sys.path:
    sys.path.insert(0, _STRATLAB_PATH)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Shared simConfig — used by both dashboard and strategy-lab
defaults = __import__('dashboard.config.sim_config', fromlist=['defaults'])
simConfig = defaults.defaults if hasattr(defaults, 'defaults') else {}
from dashboard.config.sim_config import get_simulation_config, DEFAULT_DAYS, DEFAULT_TRIALS, DEFAULT_TIMEFRAME

router = APIRouter(prefix="/api/simulation", tags=["simulation"])

logger = logging.getLogger("dashboard.api.simulation")

# ── Run status tracking (in-memory during process lifetime) ─────────────

def _build_leaderboard(entries, run_id):
    """Convert temp progress entries into leaderboard rows for the frontend."""
    import json as _json2
    from datetime import datetime, timezone
    lb = []
    for i, entry in enumerate(entries):
        row = {
            "trial_id": f"{run_id}_trial_{i+1}",
            "sharpe": round(entry.get("sharpe", 0), 4),
            "win_rate": entry.get("win_rate", 0) / 100.0,
            "total_return": entry.get("total_return", 0),
            "max_drawdown": entry.get("max_drawdown", 0),
            "profit_factor": entry.get("profit_factor", 0),
            "score": entry.get("score", 0),
            "params": entry.get("params"),
            "total_trades": entry.get("total_trades", 0),
        }
        # Include trades for chart rendering
        if "trades" in entry and entry["trades"]:
            row["trades"] = entry["trades"]
        lb.append(row)
    return sorted(lb, key=lambda x: x.get("score", 0), reverse=True)


def _parse_temp_progress(stdout):
    """Extract leaderboard from stored stdout if it contains trial results.

    Matches actual CLI print format from strategy_lab/cli.py auto-tune output:
      - 'Assets loaded: N bars'
      - 'Best params: {key: val, ...}' (or '★ Best params:')
      - 'Results saved: <run_id>'
      - Metric lines like 'Total Return:', 'Sharpe Ratio:', 'Win Rate:', etc.
    Also handles VisualTuner JSON batch output written to progress files.
    """
    lb = []
    if not stdout:
        return lb
    try:
        stdout_str = str(stdout)
        lines = stdout_str.split('\n')
        current_params = {}
        best_score = None
        for line in lines:
            stripped = line.strip()
            # Match '★ Best params: {...}' or 'Best params: {...}'
            if ('Best params' in stripped or 'best params' in stripped) and '{' in stripped:
                idx = stripped.index('{')
                try:
                    import json as _json2
                    current_params = _json2.loads(stripped[idx:])
                except Exception:
                    pass
            # Match metric lines like 'Total Return:  -4.13%', 'Sharpe Ratio:   0.07', etc.
            if 'Sharpe Ratio:' in stripped or 'Sharpe' in stripped and ':' in stripped:
                import re as _re2
                m = _re2.search(r'[Ss]harpe[^(\:]*[:\\s]+([\\-\\d\\.]+)', stripped)
                if m:
                    current_params['_sharpe'] = float(m.group(1))
            if 'Total Return:' in stripped or 'total_return' in stripped.lower() and ':' in stripped:
                import re as _re2
                m = _re2.search(r'[Rr]eturn[:\\s]+([\\-\\d\\.]+)%?', stripped)
                if m:
                    val = float(m.group(1))
                    current_params['_total_return'] = val / 100.0 if abs(val) > 1 else val
            if 'Win Rate:' in stripped or 'win_rate' in stripped.lower() and ':' in stripped:
                import re as _re2
                m = _re2.search(r'[Ww]in[:\\s]+([\\d\\.]+)%?', stripped)
                if m:
                    current_params['_win_rate'] = float(m.group(1)) / 100.0
            # Match 'Results saved: <run_id>' - indicates end of run, finalize leaderboard
            if 'Results saved:' in stripped and current_params:
                current_params['trial_id'] = '-'
                lb.append(dict(current_params))
                current_params = {}
        return lb
    except Exception:
        pass
    return lb

_run_progress: dict[str, dict] = {}


class RunRequest(BaseModel):
    """Input for starting a simulation run."""
    symbol: str
    strategy: Optional[str] = None
    days: int = Field(default=365, ge=7)
    method: str = Field(default="auto", description="tune|explore|interact")
    timeframe: Optional[str] = None
    trials: int = Field(default=20, ge=1)
    window: int = Field(default=5, ge=1, description="Conflict detection window (for interact method)")
    # Advanced tuning fields (Phase 5+)
    tp_mode: Optional[str] = Field(default=None, description="auto|manual")
    manual_tp_levels: Optional[list[float]] = None
    manual_tp_split: Optional[list[float]] = None
    param_overrides: Optional[dict] = None
    # Extra fields sent from frontend but not passed to CLI directly
    tp_levels_override: Optional[list[float]] = None
    tp_split_override: Optional[list[float]] = None
    param_grid_override: Optional[dict] = None


class InjectRequest(BaseModel):
    """Push tuning result to trading-system."""
    symbol: str = Field()
    params: dict = Field()
    commit: bool = False

    class Config:
        extra = "allow"

    # Strategy block control (Phase 9):
    new_strategies: list[str] = []         # groups to ADD (auto-populated if empty)
    remove_strategies: list[str] = []       # groups to REMOVE from this asset
    replace_all_strategies: bool = False    # if True, clear all strategies first


def _is_nested_params(params: dict) -> bool:
    """Detect multi-strategy mode: params values are dicts (strategy-level params)."""
    return len(params) > 0 and any(isinstance(v, dict) for v in params.values())


def _filter_tp_params(params: dict) -> dict:
    """Remove _tp_levels/_tp_split meta keys from params before passing to get_strategy()."""
    return {k: v for k, v in params.items() if not k.startswith('_tp')}


# ── Helper: run strategy-lab CLI as subprocess ─────────────────────────

def _run_strat_lab(args: list[str], timeout_seconds: int = 3600) -> tuple[int, str, str]:
    """Run strategy-lab CLI command via subprocess. Returns (returncode, stdout, stderr)."""
    _real_sys = __import__('sys')  # Force stdlib sys even in shadowed thread context
    try:
        lab_dir = Path(__file__).resolve().parent.parent.parent / "strategy-lab"
        cmd = [
            _real_sys.executable or "python3",
            "-m", "strategy_lab",
            *args,
            "--log-level", "WARNING",
        ]
        print(f"[simulation] cmd={cmd}", flush=True)
        import os as _os_env
        env = {**dict(_os_env.environ)}
        env["PYTHONPATH"] = str(lab_dir) + ":" + env.get("PYTHONPATH", "")
        print(f"[simulation] calling subprocess with timeout={timeout_seconds}", flush=True)
        result = subprocess.run(
            cmd, cwd=str(lab_dir), capture_output=True, text=True,
            timeout=timeout_seconds, env=env,
        )
        print(f"[simulation] subprocess done: rc={result.returncode} stdout_lines={len(result.stdout.splitlines()) if result.stdout else 0}", flush=True)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"Timed out after {timeout_seconds}s"
    except Exception as e:
        import traceback as _tb
        return -2, "", f"subprocess error: {e}\n{_tb.format_exc()}"


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/run")
async def run_simulation(req: RunRequest):
    """Start a simulation run. Long-running — returns immediately with run_id."""
    # Guard against None strategy
    strat_prefix = (req.strategy or "unknown")[:8] if req.strategy else "nostrat"
    run_id = f"sim_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{strat_prefix}"
    _dbg="/tmp/sim_debug.log"; open(_dbg,"a").write(f"[{__import__('datetime').datetime.utcnow().isoformat()} /run] ENTER method={req.method!r} symbol={req.symbol!r} strategy={req.strategy!r} trials={req.trials!r}\n"); open(_dbg,"a").flush()

    # Spawn in background thread (uvicorn already has event loop)
    import threading

    def _execute():
        try:
            import sys as _de; _dbg="/tmp/sim_debug.log"; open(_dbg,"a").write(f"[{__import__('datetime').datetime.utcnow().isoformat()} /run-thread] START run_id={run_id}\n"); open(_dbg,"a").flush()
            cli_args = ["--symbol", req.symbol]
            if req.strategy:
                cli_args += ["--strategy", req.strategy]
            cli_args += ["--days", str(req.days)]
            if req.timeframe:
                cli_args += ["--timeframe", req.timeframe]
            # Always include trials (default is 20, but frontend may send exact count)
            cli_args += ["--trials", str(req.trials)]
            # Wire through advanced tuning fields (frontend sends tp_levels_override / param_grid_override)
            # Note: auto subcommand only accepts --symbol, --strategy, --days, --timeframe, --cash, --trials,
            #       --oos-days, --commit, --log-level — ignore tp_mode/manual_tp etc for now
            if req.param_overrides:
                cli_args += ["--param_overrides", json.dumps(req.param_overrides)]
            elif req.param_grid_override:
                cli_args += ["--param_overrides", json.dumps(req.param_grid_override)]

            if req.method == "tune" or req.method == "meta":
                cmd = ["auto"] + cli_args
            elif req.method == "explore":
                cmd = ["explore", req.symbol] + (["--days", str(req.days)] if hasattr(req, 'days') else [])
                cli_args = [req.symbol, "--days", str(req.days)]
                cmd = ["explore"] + cli_args
            elif req.method == "interact":
                cmd = ["interact"] + cli_args + ["--window", str(req.window)]
            else:
                cmd = ["auto"] + cli_args

            _dbg="/tmp/sim_debug.log"; open(_dbg,"a").write(f"[{__import__('datetime').datetime.utcnow().isoformat()} /run-thread] CMD={' '.join(cmd[:5])}...\n"); open(_dbg,"a").flush()
            rc, stdout, stderr = _run_strat_lab(cmd)
            _dbg="/tmp/sim_debug.log"; open(_dbg,"a").write(f"[{__import__('datetime').datetime.utcnow().isoformat()} /run-thread] RC={rc} out_lines={len(stdout.splitlines()) if stdout else 0}\n"); open(_dbg,"a").flush()
            if stdout:
                for line in str(stdout).split('\n'):
                    if 'Best params' in line or 'results saved' in line.lower() or 'results:' in line.lower():
                        print(f"[DEBUG /run thread] OUTLINE: {line.strip()}", flush=True)
            if stderr:
                for line in str(stderr).split('\n')[:10]:
                    print(f"[DEBUG /run thread] STDERR: {line.strip()}", flush=True)
            # Extract the ResultStore filename from subprocess output so /progress can find it
            result_file_id = None
            if rc == 0 and stdout:
                for line in str(stdout).split('\n'):
                    if 'Results saved:' in line:
                        result_file_id = line.split('Results saved:')[1].strip()
                        break
            _run_progress[run_id] = {
                "status": "completed" if rc == 0 else "failed",
                "returncode": rc,
                "stdout": stdout[-10000:] if len(stdout) > 10000 else stdout,
                "stderr": stderr[-5000:] if len(stderr) > 5000 else stderr,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result_file_id": result_file_id,  # key used by ResultStore.get()
            }
            _dbg="/tmp/sim_debug.log"; open(_dbg,"a").write(f"[{__import__('datetime').datetime.utcnow().isoformat()} /run-thread] DONE status={_run_progress[run_id]['status']} file_id={result_file_id}\n"); open(_dbg,"a").flush()
        except Exception as e:
            import sys; print(f"[simulation] ERROR in thread for {run_id}: {e}", flush=True)
            import traceback
            print(f"[simulation] TB: {traceback.format_exc()}", flush=True)
            _run_progress[run_id] = {
                "status": "error",
                "error": str(e),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

    thread = threading.Thread
    thread = threading.Thread(target=_execute, daemon=False)
    thread.start()
    _run_progress[run_id] = {"status": "running", "thread": thread}
    return {"run_id": run_id, "status": "running"}


@router.get("/list")
async def list_runs(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    min_sharpe: Optional[float] = None,
):
    """List stored simulation results (calls ResultStore CLI)."""
    import os
    from strategy_lab.results import ResultStore

    store = ResultStore()
    runs = store.list_runs(
        min_sharpe=min_sharpe, limit=100,
    )
    print(f"[DEBUG /list] list_runs returned {len(runs)} raw entries")
    for r in runs:
        print(f"  DEBUG: run_id={r.get('run_id')} symbol={r.get('symbol')} strategy={r.get('strategy')}")
    # Normalize: index entries use _sharpe/_total_return etc. frontend expects
    # sharpe/win_rate/total_return/max_drawdown/profit_factor without underscore prefix.
    normalized = []
    for r in runs:
        entry = {
            "run_id": r.get("run_id"),
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "timeframe": r.get("timeframe"),
            "days": r.get("days"),
            "source": r.get("source"),
            "created_at": convert_timestamps_in_value(r.get("timestamp_utc")),
            "sharpe": r.get("_sharpe"),
            "total_return": r.get("_total_return"),
            "win_rate": r.get("_win_rate"),
            "max_drawdown": r.get("_max_drawdown"),
            "profit_factor": r.get("_profit_factor"),
            "tp_levels": r.get("tp_levels"),
            "tp_split": r.get("tp_split"),
            "tp_mode": r.get("tp_mode"),
        }
        normalized.append(entry)
    return {"runs": normalized, "count": len(normalized)}


@router.get("/results/{run_id}")
async def get_result(run_id: str):
    """Get a single stored result by ID."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    result = store.get(run_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Result {run_id} not found")
    return _flatten_for_frontend(result)


@router.get("/compare")
async def compare_runs(first: str, second: str):
    """Compare two stored results (GET for frontend convenience)."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    a = store.get(first) or {}
    b = store.get(second) or {}
    # Flatten metrics to top level for frontend compatibility
    return {
        "first": _flatten_for_frontend(a),
        "second": _flatten_for_frontend(b),
        "diff": store.diff(first, second),
    }


def _flatten_for_frontend(result: dict) -> dict:
    """Flatten ResultStore entry to field names the frontend expects."""
    m = result.get("metrics", {})
    return {
        "id": result.get("run_id"),
        "run_id": result.get("run_id"),
        "symbol": result.get("symbol"),
        "strategy": result.get("strategy"),
        "days": result.get("days"),
        "timeframe": result.get("timeframe"),
        "tp_mode": result.get("tp_mode"),
        "tp_levels": result.get("tp_levels"),
        "tp_split": result.get("tp_split"),
        "sharpe": m.get("sharpe"),
        "win_rate": m.get("win_rate"),
        "total_return": m.get("total_return"),
        "max_drawdown": m.get("max_drawdown"),
        "profit_factor": m.get("profit_factor"),
        "total_trades": m.get("total_trades"),
        "best_params": result.get("params", {}),
        "params": result.get("params", {}),
        "metrics": m,
        "oos_result": result.get("oos_result"),
        "wf_result": result.get("wf_result"),
        "created_at": convert_timestamps_in_value(result.get("timestamp_utc")),
    }


@router.post("/diff")
async def diff_runs(run_a: str, run_b: str):
    """Compare two stored results."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    return store.diff(run_a, run_b)


@router.get("/status/{run_id}")
async def get_run_status(run_id: str):
    """Get status of an in-flight simulation run."""
    run_info = _run_progress.get(run_id)
    if not run_info:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    status = run_info.get("status", "unknown")
    return {"run_id": run_id, "status": status}


@router.post("/inject")
async def inject_result(req: InjectRequest):
    """Push tuning params to trading-system via strategy-injector."""
    from strategy_lab.inject import WatchlistConfigInjector

    injector = WatchlistConfigInjector()
    is_nested = _is_nested_params(req.params)
    dry_run = not req.commit
    diffs_out = []
    combined_state = {
        "added_groups": [], "removed_groups": [],
        "new_strategy_list": [], "strategy_to_group_map": {},
        "strategy_param_diffs": {},
        "current_strategy_names": [], "proposed_strategy_names": [],
    }

    def _merge_state(dest, src):
        for k, v in src.items():
            if isinstance(v, list):
                dest[k] = list(set((dest.get(k) or []) + v))
            elif isinstance(v, dict):
                dest[k] = {**(dest.get(k) or {}), **v}
            else:
                if k not in dest or dest[k] is None:
                    dest[k] = v

    if is_nested:
        for strat_name, strat_params in req.params.items():
            d, s = injector.inject_strategy_block(
                symbol=req.symbol,
                strategy_name=strat_name,
                params=strat_params,
                remove_strategies=req.remove_strategies or [],
                replace_all=req.replace_all_strategies,
                dry_run=dry_run,
                tp_levels=None,  # TF/TP now come inside strat_params as _prefixed meta keys
                tp_split=None,
                timeframe=None,
            )
            diffs_out.extend(d)
            _merge_state(combined_state, s)
    else:
        diffs_out, combined_state = injector.inject_strategy_block(
            symbol=req.symbol,
            strategy_name=req.strategy,
            params=req.params,
            remove_strategies=req.remove_strategies or [],
            replace_all=req.replace_all_strategies,
            dry_run=dry_run,
            tp_levels=getattr(req, 'tp_levels', None),
            tp_split=getattr(req, 'tp_split', None),
            timeframe=getattr(req, 'timeframe', None),
        )

    return {
        "status": "staged" if not req.commit else "committed",
        "diffs": diffs_out,
        "strategy": req.strategy,
        "symbol": req.symbol,
        "state": combined_state,
    }


# ── Additional endpoints needed by the frontend ─────────────────

@router.get("/watchlist")
async def get_simulation_watchlist():
    """Return watchlist SYMBOLS for the Simulation tab dropdown.

    Uses shared simConfig.load_watchlist() so dashboard and strategy-lab share the same source.
    """
    from dashboard.config.sim_config import load_watchlist
    data = load_watchlist()
    symbols = [a["symbol"] for a in data.get("assets", []) if isinstance(a, dict) and "symbol" in a]
    return sorted(symbols)


@router.get("/watchlist/details")
async def get_simulation_watchlist_details():
    """Return full watchlist entries keyed by symbol.

    Uses shared simConfig.load_watchlist() — same source as strategy-lab.
    """
    from dashboard.config.sim_config import load_watchlist
    data = load_watchlist()
    result = {}
    for a in data.get("assets", []):
        if isinstance(a, dict) and "symbol" in a:
            result[a["symbol"]] = a
    return result


@router.get("/strategies")
async def get_simulation_strategies():
    """Return available strategies as a plain array of names for the Simulation tab dropdown."""
    # simConfig default_strategy_groups are included here when populated
    if simConfig and "default_strategy_groups" in simConfig:
        return list_strategies()
    from strategy_lab.strategies import list_strategies
    return list_strategies()


@router.get("/strategy-params/{strategy_name}")
async def get_strategy_params(strategy_name: str):
    """Return param_descriptors for a strategy (used as default param fallback)."""
    from strategy_lab.strategies import get_strategy
    strat = get_strategy(strategy_name)
    descriptors = getattr(strat, "param_descriptors", {})
    return descriptors




@router.get("/progress/{run_id}")
async def get_progress(run_id: str):
    """Poll tuning progress and leaderboard for an in-flight or completed run.

    Unified handler — works for BOTH sim_* (Tune tab, auto-tune) and vt_* (Visual Tune).
    """
    import json as _json2
    from pathlib import Path as _Path

    is_visual = run_id.startswith("vt_")
    info = _run_progress.get(run_id)
    print(f"[DEBUG /progress] ENTER run_id={run_id} is_visual={is_visual} in_memory={bool(info)}", flush=True)

    # 404 if no record anywhere
    if not info:
        from strategy_lab.results import ResultStore
        store = ResultStore()
        rec = store.get(run_id)
        print(f"[DEBUG /progress] NO in-memory, looked up store: found={bool(rec)}", flush=True)
        if not rec:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        # No in-memory state — treat as running and scan JSONL
        info = {}

    status = info.get("status")
    print(f"[DEBUG /progress] status={status!r}", flush=True)
    if status == "completed":
        # Use the helper (defined below)
        return _handle_completed(run_id, info, is_visual)

    if status == "error":
        return {"run_id": run_id, "status": "error", "leaderboard": [],
                "completed": 0, "error": info.get("error", str(info.get("traceback", "")))}

    # Handle "failed" (rc != 0) — same as error for frontend purposes
    if status == "failed":
        return {"run_id": run_id, "status": "error", "leaderboard": [],
                "completed": 0, "error": info.get("stderr", "Process exited with non-zero code")}

    # In-memory entry missing (GC/daemon thread died) — fall back to JSONL scan
    if is_visual:
        home = _Path.home() / ".strategy-lab"
        if home.exists():
            for fpath in sorted(home.glob(f"_progress_{run_id}.jsonl")):
                try:
                    with open(fpath) as pf:
                        lines = [l for l in pf.readlines() if l.strip()]
                    partial = [_json2.loads(l) for l in lines[-50:] if l.strip()]
                    if partial:
                        last_entry = partial[-1]
                        lb_list = last_entry.get("leaderboard_entries", [])
                        leaderboard = _build_leaderboard(lb_list, run_id) if lb_list else []
                        completed = len(leaderboard) if leaderboard else 0
                        cu = last_entry.get("chart_url") or last_entry.get("_chart_url")
                        eq_u = last_entry.get("equity_url") or last_entry.get("_equity_url")
                        btc = last_entry.get("best_trades_count", 0)
                        # Trades aren't in JSONL — check if any batch entry has them
                        rs_trades = []
                        for be in partial:
                            bt = be.get("best") or {}
                            if isinstance(bt, dict) and bt.get("trades"):
                                rs_trades.extend(bt["trades"][:50])
                            at = be.get("all_trades") or []
                            for t in at:
                                if isinstance(t, dict) and len(rs_trades) < 50:
                                    rs_trades.append(t)
                        return {
                            "run_id": run_id, "status": "completed",
                            "leaderboard": leaderboard, "completed": completed,
                            "chart_url": cu or None, "equity_chart_url": eq_u or None,
                            "trades": [], "best_trades_count": btc,
                        }
                except Exception:
                    pass

    # Still running — scan for partial JSONL data
    leaderboard = []
    completed_count = 0
    chart_url = None
    best_trades_count = 0
    home = _Path.home() / ".strategy-lab"

    if home.exists():
        for fpath in sorted(home.glob(f"_progress_{run_id}.jsonl")):
            try:
                with open(fpath) as pf:
                    lines = [l for l in pf.readlines() if l.strip()]
                partial = [_json2.loads(l) for l in lines[-50:] if l.strip()]
                if partial:
                    # Use only the LAST progress entry — it contains ALL accumulated data
                    last_entry = partial[-1]
                    completed_count = len(last_entry.get("leaderboard_entries", []))  
                    lb_list = last_entry.get("leaderboard_entries", [])
                    leaderboard = _build_leaderboard(lb_list, run_id) if lb_list else []
                    cu = last_entry.get("chart_url") or last_entry.get("_chart_url")
                    if cu: chart_url = cu
                    btc = last_entry.get("best_trades_count", 0)
                    if btc: best_trades_count = btc
            except Exception:
                pass

    return {
        "run_id": run_id, "status": "running", "completed": completed_count,
        "leaderboard": leaderboard, "chart_url": chart_url,
        "trades": [], "best_trades_count": best_trades_count,
    }


def _handle_completed(run_id: str, info: dict, is_visual: bool) -> dict:
    """Unified handler for completed sim_* and vt_* runs."""
    if is_visual:
        return _handle_vt_completed(run_id, info)
    else:
        return _handle_sim_completed(run_id, info)


def _handle_sim_completed(run_id: str, info: dict) -> dict:
    """Handle Tune tab (sim_*) completed runs."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    import sys as _debug_sys; print(f"[DEBUG _handle_sim] ENTER run_id={run_id!r}", flush=True)

    rec = None
    result_file_id = info.get("result_file_id")
    print(f"[DEBUG _handle_sim] result_file_id={result_file_id!r}", flush=True)
    if result_file_id:
        rec = store.get(result_file_id)
        print(f"[DEBUG _handle_sim] store.get({result_file_id!r}) found={bool(rec)}", flush=True)
    if not rec:
        rec = store.get(run_id)
        print(f"[DEBUG _handle_sim] fallback store.get({run_id!r}) found={bool(rec)}", flush=True)

    # Build leaderboard from stored data
    lb = []
    completed_count = 0
    if rec:
        lb = rec.get("leaderboard", []) or []
        if not lb:
            m = rec.get("metrics", {})
            if m:
                lb = [{
                    "trial_id": f"{run_id}_best",
                    "sharpe": m.get("sharpe"),
                    "win_rate": (m.get("win_rate") or 0) / 100.0,
                    "total_return": m.get("total_return", 0),
                    "max_drawdown": m.get("max_drawdown"),
                    "profit_factor": m.get("profit_factor"),
                    "score": m.get("sharpe") or 0,
                }]
        completed_count = len(lb) if lb else 1

    stdout = info.get("stdout", "")
    return {
        "run_id": run_id, "status": "completed", "leaderboard": lb,
        "completed": completed_count,
        "stdout": stdout[-3000:] if isinstance(stdout, str) and len(stdout) > 3000 else stdout,
        "error": info.get("stderr", "") if info.get("status") == "failed" else "",
        "chart_url": None, "equity_chart_url": None, "trades": [],
    }


def _handle_vt_completed(run_id: str, info: dict) -> dict:
    """Handle Visual Tune (vt_*) completed runs."""
    import json as _json2
    from pathlib import Path as _Path

    leaderboard = []
    completed = 0
    chart_url = ""
    equity_chart_url = ""
    trades = []
    best_trades_count = 0

    # Try in-memory results list first (VisualTuner stores it)
    raw_results = info.get("results")
    # VisualTuner yields dataclass instances, not dicts — convert to dict
    if raw_results:
        def _to_dict(item):
            if isinstance(item, dict): return item
            if hasattr(item, 'to_dict'): return item.to_dict()
            if hasattr(item, '__dict__'): return item.__dict__
            return {}
        # Extract completed trial count from last batch (total_trials is the real count)
        for d in reversed([_to_dict(r) for r in raw_results]):
            if d.get("total_trials"):
                completed = d["total_trials"]
                break
        # Also use leaderboard entries count as fallback (only if total_trials was 0)
        if completed == 0:
            for d in reversed([_to_dict(r) for r in raw_results]):
                lb = d.get("leaderboard", [])
                if isinstance(lb, list) and len(lb) > 0:
                    completed = len(lb)
                    break
        # Populate leaderboard from dataclass.leaderboard (last batch has full accumulated list)
        for d in reversed([_to_dict(r) for r in raw_results]):
            lb_list = d.get("leaderboard", [])
            if isinstance(lb_list, list) and lb_list:
                leaderboard = _build_leaderboard(lb_list, run_id)
                break
        # Extract chart URLs from dataclass
        for d in reversed([_to_dict(r) for r in raw_results]):
            if d.get("chart_url"):
                chart_url = d["chart_url"]
                equity_chart_url = d.get("equity_url", "") or equity_chart_url
                break
        # Extract trades from best results
        for d in reversed([_to_dict(r) for r in raw_results]):
            if isinstance(d.get("best"), dict):
                tt = d["best"].get("trades", [])
                if tt: best_trial_trades = tt
                for t in (d.get("all_trades") or []):
                    if isinstance(t, dict) and len(trades) < 50:
                        trades.append(t)
        if not trades and 'best_trial_trades' in dir() and best_trial_trades:
            trades = best_trial_trades[:50]

    # JSONL scan for full leaderboard + any charts not in memory
    home = _Path.home() / ".strategy-lab"
    if home.exists():
        for fpath in sorted(home.glob(f"_progress_{run_id}.jsonl")):
            try:
                with open(fpath) as pf:
                    lines = [l for l in pf.readlines() if l.strip()]
                partial = [_json2.loads(l) for l in lines[-50:] if l.strip()]
                if partial:
                    # Use only the LAST entry's leaderboard (it already contains accumulated data)
                    last_entry = partial[-1]
                    lb_list = last_entry.get("leaderboard_entries", [])
                    if isinstance(lb_list, list) and lb_list:
                        leaderboard = _build_leaderboard(lb_list, run_id)
                        completed = len(leaderboard)
                    last = partial[-1]
                    if isinstance(last, dict):
                        cu = last.get("chart_url") or last.get("_chart_url")
                        if cu and not chart_url: chart_url = cu
                        eu = last.get("equity_url") or last.get("_equity_url")
                        if eu and not equity_chart_url: equity_chart_url = eu
                        btc = last.get("best_trades_count", 0)
                        if btc and not best_trades_count: best_trades_count = btc
                break  # only use first file
            except Exception:
                pass

    return {
        "run_id": run_id, "status": "completed",
        "leaderboard": leaderboard, "completed": completed,
        "chart_url": chart_url or None, "equity_chart_url": equity_chart_url or None,
        "trades": trades[:50], "best_trades_count": best_trades_count,
    }
class RunVisualRequest(BaseModel):
    """Input for starting a visual tuner run."""
    symbol: str = Field(description="Asset symbol to tune")
    strategy: str = Field(description="Strategy to tune (e.g. RSI_MeanReversion)")
    days: int = Field(default=90, ge=7)
    timeframe: Optional[str] = None
    chart_window_days: int = Field(default=90, ge=7, description="Days of price data to show on overlay")
    trials: int = Field(default=10, ge=1)


class RunRankRequest(BaseModel):
    """Input for running multi-strategy ranking on a symbol."""
    symbol: str = Field(description="Asset symbol to rank strategies for")
    days: int = Field(default=365, ge=7)
    timeframe: Optional[str] = None
    window_bars: int = Field(default=10, ge=2)


def _run_visual_tuner(symbol, strategy, days, timeframe, trials):
    """Run VisualTuner as a background thread. Returns (run_id, thread)."""
    from strategy_lab.tune.visual_tuner import VisualTuner
    from strategy_lab.core.data_source import get_bars as core_get_bars
    from datetime import timezone as _tz

    run_id = f"vt_{datetime.now(_tz.utc).strftime('%Y%m%dT%H%M%SZ')}"

    def _execute():
        try:
            end_dt = datetime.now(_tz.utc)
            start_dt = end_dt - timedelta(days=days)
            # Use core_get_bars which auto-detects asset_class for DB table routing
            # when there are unfillable gaps (no Alpaca credentials)
            # Use user's timeframe_hint — only override to "auto" if None
            effective_tf = (timeframe or "5m")
            # core_get_bars auto-detects asset_class for DB table routing
            bars = core_get_bars(symbol, effective_tf, start_dt, end_dt)
            if bars is None or (hasattr(bars, 'empty') and bars.empty):
                raise ValueError(f"No data available for {symbol}")

            # Don't hardcode batch_size — use it as-is from default so batches are reasonable
            tuner = VisualTuner(max_trials=trials, batch_size=min(5, max(trials, 1)), chart_window_days=90)
            results = list(tuner.run(
                bars=bars,
                strategy_name=strategy,
                symbol=symbol,
                days=days,
                timeframe_hint=timeframe or "auto",  # pass user's choice through
            ))
            # Validate that at least one valid trial produced results
            has_valid = any(
                r for r in results if getattr(r, 'best', None) and r.best.get('total_trades', 0) > 0
            )
            if not results or not has_valid:
                raise ValueError(
                    f"Visual Tuner produced no valid trials for {symbol}/{strategy}. "
                    f"This usually means the strategy returns zero signals on the loaded data, "
                    f"or the timeframes suggested have insufficient bars. Check that Alpaca data exists for this asset."
                )

            # Store full progress state for polling
            _run_progress[run_id] = {
                "status": "completed",
                "results": results,
                "completed_at": datetime.now(_tz.utc).isoformat(),
            }
        except Exception as e:
            import traceback
            _run_progress[run_id] = {
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
                "completed_at": datetime.now(_tz.utc).isoformat(),
            }

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()
    return run_id, thread


def _run_ranking(symbol, days, timeframe, window_bars):
    """Run ranking engine. Returns dict with leaderboard + conflicts."""
    from strategy_lab.rank import rank_strategies_on_asset
    from strategy_lab.core.data_source import get_bars as core_get_bars
    from strategy_lab.strategies import list_strategies
    from strategy_lab.results import ResultStore
    from datetime import datetime, timezone, timedelta

    # Fetch bars for all strategies
    cache = BarCache()
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    bars = None
    if timeframe:
        bars = cache.get(symbol, timeframe, start_dt, end_dt)
    if bars is None or (hasattr(bars, 'empty') and bars.empty):
        # core_get_bars auto-detects asset_class for DB table routing
        bars = core_get_bars(symbol, timeframe or "5m", start_dt, end_dt)

    if hasattr(bars, 'empty') and bars.empty:
        raise ValueError(f"No data for {symbol}")

    # Get all strategies that have results in ResultStore
    store = ResultStore()
    runs = store.list_runs(symbol=symbol, limit=500)

    # Group runs by strategy — take the best (highest Sharpe) per strategy
    best_per_strategy = {}
    for run in runs:
        sname = run.get("strategy")
        sharpe = run.get("_sharpe", 0)
        if sname and sharpe is not None:
            if sname not in best_per_strategy or (best_per_strategy[sname].get("_sharpe", -999) < sharpe):
                best_per_strategy[sname] = run

    # Run ranking with conflict detection
    strategy_data = {
        name: {
            "metrics": best.get("metrics", {}),
            "params": best.get("params", {}),
            "timeframe": best.get("timeframe"),
            "days": best.get("days"),
            "run_id": best.get("run_id"),
        }
        for name, best in best_per_strategy.items()
    }

    leaderboard, conflicts = rank_strategies_on_asset(bars, strategy_data, window_bars=window_bars)

    return {
        "leaderboard": leaderboard,
        "conflicts": conflicts,
        "strategies_count": len(leaderboard),
        "conflict_count": len(conflicts) if isinstance(conflicts, list) else 0,
    }


@router.post("/run-visual")
async def run_visual_tuner(req: RunVisualRequest):
    """Start the visual tuner for a strategy on a symbol.

    Returns immediately with run_id. Poll progress via /progress/{run_id}
    (which will include chart URLs when available).
    """
    try:
        run_id, thread = _run_visual_tuner(
            req.symbol, req.strategy, req.days,
            req.timeframe, req.trials,
        )
        _run_progress[run_id] = {
            "status": "running",
            "thread": thread,
            "symbol": req.symbol,
            "strategy": req.strategy,
        }
        return {"run_id": run_id, "status": "running", "chart_url": None}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/rank")
async def run_ranking(req: RunRankRequest):
    """Run strategy ranking on a symbol. Blocks until complete.

    Returns leaderboard + conflict analysis for all strategies that have
    tuned results for the given symbol.
    """
    try:
        result = _run_ranking(
            req.symbol, req.days,
            req.timeframe or None, req.window_bars,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        logger.error("Ranking failed: %s", e)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Ranking failed: {e}")


# ═══════════════════════════════════════════════════════════════
# Chart file serving endpoint
# ═══════════════════════════════════════════════════════════════

_CHART_DIR = Path.home() / ".strategy-lab" / "charts"

@router.get("/chart/{filename}")
async def serve_chart_file(filename: str):
    """Serve chart HTML files saved by the VisualTuner.

    Files are stored in ~/.strategy-lab/charts/ and served directly.
    Only .html files within the chart dir are allowed (no path traversal).
    """
    import mimetypes
    safe_name = Path(filename).name  # strip any directory components
    if not safe_name or '..' in filename or '/' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = _CHART_DIR / safe_name
    
    if not file_path.exists():
        # Also check with .html extension if not present
        alt = file_path.with_suffix('.html')
        if alt.exists():
            file_path = alt
        else:
            raise HTTPException(status_code=404, detail=f"Chart file not found: {safe_name}")
    
    mime, _ = mimetypes.guess_type(safe_name)
    if mime is None:
        mime = "text/html"
    
    return FileResponse(
        path=str(file_path),
        media_type=mime,
        filename=safe_name,
    )

@router.options("/chart/{filename}")
async def chart_options():
    from fastapi.responses import Response
    return Response(status_code=200)

# Ensure all routes include CORS headers — do this after all route defs


@router.delete("/delete/{run_id}")
async def delete_simulation_result(run_id: str):
    """Delete a single simulation result."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    ok = store.delete(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Result {run_id} not found")
    return {"deleted": run_id}


@router.delete("/delete-all")
async def delete_all_simulation_results():
    """Delete all simulation results."""
    from strategy_lab.results import ResultStore
    store = ResultStore()
    count = store.delete_all()
    return {"deleted": count}


@router.get("/rank/{symbol}")
async def get_cached_ranking(symbol: str):
    """Get cached ranking results for a symbol.

    Rankings are cached in _run_progress keyed by the run_id returned from POST /rank.
    For on-demand ranking, use POST /rank instead.
    """
    # Search for any completed ranking run for this symbol
    for run_id, info in _run_progress.items():
        if "/rank" not in run_id and str(info.get("status", "")) != "completed":
            continue
        # Check if the result has leaderboard data (ranking output)
        if info.get("leaderboard") or isinstance(info.get("result"), dict):
            ranking = info.get("result") or {}
            return {
                "symbol": symbol,
                "run_id": run_id,
                **ranking,
            }
    raise HTTPException(status_code=404, detail=f"No ranking results for {symbol}")

# ── Strategy param registry ────────────────────────────────────────────

def _get_strategy_params(strategy_name: str) -> Optional[dict]:
    """Return the param_descriptors (or empty dict) for a strategy name."""
    import importlib
    try:
        from strategy_lab.strategies import get_strategy
        s = get_strategy(strategy_name)
        if s and hasattr(s, 'param_descriptors'):
            desc = s.param_descriptors
            # param_descriptors can be list or dict; normalize to dict of {param: descriptor}
            if isinstance(desc, dict):
                return desc
            elif isinstance(desc, list):
                return {item.get('name', item.get('param_name', '')): item for item in desc}
        return {}
    except Exception:
        return {}


@router.get("/best-config")
async def get_best_config(strategy: str = "") -> dict:
    """Return the default param descriptors for a strategy.
    
    Used by the Inject tab to pre-populate parameter fields.
    """
    if not strategy:
        return {"error": "Missing strategy parameter", "params": {}}
    params = _get_strategy_params(strategy)
    return {
        "strategy": strategy,
        "params": params,
    }

@router.get("/strategies/params")
async def get_all_strategy_params() -> dict:
    """Return param descriptors for all registered strategies."""
    from strategy_lab.strategies import STRATEGY_REGISTRY
    result = {}
    for name in STRATEGIES:
        try:
            from strategy_lab.strategies import get_strategy
            s = get_strategy(name)
            if s and hasattr(s, 'param_descriptors'):
                desc = s.param_descriptors
                if isinstance(desc, dict):
                    result[name] = desc
                elif isinstance(desc, list):
                    result[name] = {item.get('name', item.get('param_name', '')): item for item in desc}
        except Exception:
            result[name] = {}
    return {"strategies": result}



@router.get("/debug/progress-all")
async def debug_progress_all():
    """Debug endpoint to inspect all entries in _run_progress."""
    result = {}
    for rid, entry in _run_progress.items():
        info = {k: v for k, v in entry.items()}
        if isinstance(info.get('thread'), threading.Thread):
            t = info.pop('thread')
            info['thread_alive'] = t.is_alive()
        result[rid] = info
    return {"status": "ok", "runs": result}
