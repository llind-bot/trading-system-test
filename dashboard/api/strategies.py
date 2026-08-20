"""Strategy evaluation and signal history API routes."""
from fastapi import APIRouter, Query
from datetime import datetime, timezone
import ast
from dashboard.utils.tz_convert import convert_timestamps_in_value

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

DB_PATH = "database/trades.db"
SIGNALS_DB_PATH = "database/trades.db"  # consolidated into trades.db


def _extract_warmup_from_ast(py_file, class_name):
    """Extract warm_up_bars_needed return value by parsing the strategy file AST."""
    try:
        src = py_file.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "warm_up_bars_needed":
                        for stmt in item.body:
                            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
                                return stmt.value.value
    except Exception:
        pass
    return 20


def _get_db():
    """Lazy connection to bars.db."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_signals_db():
    """Lazy connection to trades.db (engine_signals table — consolidated from trades.db (engine_signals table))."""
    import sqlite3
    conn = sqlite3.connect(SIGNALS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@router.get("/evaluations")
def get_strategy_evaluations():
    """Return latest evaluation per symbol (for the Evaluation Grid tab).

    Reads from trades.db (engine_signals table) engine_signals — uses strategy NAME as the column header.
    Strategy groups have been removed; each row is an individual strategy result.
    Uses grouped MAX(id) to get the latest per (symbol, strategy) combo.
    """
    import sqlite3
    conn = _get_signals_db()
    try:
        # Optimized: group by symbol+strategy first, then join back for fields
        sql = """
            SELECT s1.symbol, s1.strategy, s1.side, s1.confidence, s1.timestamp
            FROM engine_signals s1
            INNER JOIN (
                SELECT MAX(id) AS max_id FROM engine_signals
                WHERE status IN ('eval', 'pending')
                GROUP BY symbol, strategy
            ) s2 ON s1.id = s2.max_id
            WHERE s1.status IN ('eval', 'pending')
            ORDER BY s1.symbol, s1.strategy
        """
        rows = conn.execute(sql).fetchall()
        result = []
        for r in rows:
            conf = r["confidence"]
            confidence = float(conf) if conf is not None else 0.0
            result.append({
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "vote_result": r["side"],
                "confidence": confidence,
                "timestamp": convert_timestamps_in_value(r["timestamp"]),
            })
        return result
    finally:
        conn.close()


@router.get("/history")
def get_strategies_history(
    symbol: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    latest_per_symbol: bool = Query(False),
):
    """Return signal history from trades.db (engine_signals table) engine_signals table.

    No time filter — returns all rows so the dashboard can display historical
    evaluations for any asset, regardless of whether the engine is still active."""
    conn = _get_signals_db()
    try:
        if latest_per_symbol:
            sql = """
                SELECT s1.symbol, s1.strategy, s1.side, s1.confidence, s1.timestamp
                FROM engine_signals s1
                INNER JOIN (
                    SELECT MAX(id) AS max_id FROM engine_signals
                    WHERE status IN ('eval', 'pending')
                    GROUP BY symbol, strategy
                ) s2 ON s1.id = s2.max_id
            """
        else:
            sql = """
                SELECT symbol, strategy, side, confidence, timestamp
                FROM engine_signals
                WHERE status IN ('eval', 'pending')
            """
        if symbol:
            sql += f" AND symbol = '{symbol}'"
        sql += " ORDER BY timestamp DESC LIMIT ?"

        rows = conn.execute(sql, (limit,)).fetchall()
        result = []
        for r in rows:
            conf = r["confidence"]
            confidence = float(conf) if conf is not None else 0.0
            result.append({
                "symbol": r["symbol"],
                "strategy_group_VERIFIED": r["strategy"],  # strategy name now serves as group column
                "vote_result": r["side"],
                "confidence": confidence,
                "timestamp": convert_timestamps_in_value(r["timestamp"]),
            })
        return result
    finally:
        conn.close()


@router.get("/full")
def get_strategies_full():
    """Return available strategies as individual entries (not groups).

    Scans src/strategies/*.py dynamically — mirrors what the engine discovers.
    Also returns watchlist assets for simulation dropdowns.
    """
    import yaml
    import os
    from pathlib import Path
    from importlib import util as il_util

    wl_path = Path(__file__).parent.parent.parent / "config" / "watchlist.yaml"
    strategies_dir = Path(__file__).parent.parent.parent / "src" / "strategies"

    # ── Load watchlist assets for simulation dropdowns ──
    watchlist_assets = []
    if wl_path.exists():
        with open(wl_path) as f:
            watchlist = yaml.safe_load(f) or {}
        watchlist_assets = watchlist.get("assets", [])

    # ── Dynamically discover strategies from src/strategies/*.py ──
    available_strategies = []
    if strategies_dir.exists():
        for py_file in sorted(strategies_dir.glob("*.py")):
            if py_file.name in ("__init__.py", "base.py"):
                continue
            spec = il_util.spec_from_file_location(py_file.stem, py_file)
            mod = il_util.module_from_spec(spec) if spec else None
            try:
                spec.loader.exec_module(mod)
            except Exception:
                continue
            # Find the strategy class in this module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if hasattr(attr, "NAME") and isinstance(getattr(attr, "NAME", None), str):
                    name_val = getattr(attr, "NAME", "")
                    if name_val == "BaseStrategy":
                        continue
                    desc = getattr(attr, "DESCRIPTION", "")
                    defaults = getattr(attr, "DEFAULT_PARAMS", {})
                    # ── Fix: handle warm_up_bars_needed correctly ──
                    # It may be a regular instance method; try to get it via inspection
                    import inspect as _inspect
                    warmup_static = _inspect.getattr_static(attr, "warm_up_bars_needed", None)
                    if warmup_static and isinstance(warmup_static, (classmethod, staticmethod)):
                        try:
                            warmup = attr.warm_up_bars_needed()
                        except Exception:
                            warmup = 20
                    elif hasattr(attr, "__init__"):
                        # Try instantiating to call the method
                        sig = _inspect.signature(attr.__init__)
                        required_params = [p for p in sig.parameters.values()
                                           if p.default == _inspect.Parameter.empty and p.name != "self"]
                        if not required_params:
                            try:
                                inst = attr()
                                warmup = inst.warm_up_bars_needed()
                            except Exception:
                                warmup = _extract_warmup_from_ast(py_file, attr_name)
                        else:
                            warmup = _extract_warmup_from_ast(py_file, attr_name)
                    else:
                        warmup = _extract_warmup_from_ast(py_file, attr_name)
                    
                    available_strategies.append({
                        "name": name_val,
                        "description": desc or py_file.stem.replace("_", " ").title(),
                        "default_params": defaults or {},
                        "warm_up_bars_needed": warmup if isinstance(warmup, int) else 20,
                    })
                    break

    return {
        "assets": watchlist_assets,
        "available_strategies": available_strategies,
    }
