"""Shared configuration between Dashboard (backend) and Strategy-Lab (tuning).

This is the single source of truth for simulation defaults, strategy lists,
watchlist resolution, and TP/param conventions used by both the dashboard UI
and the CLI engine.
"""
import os
from pathlib import Path
from typing import Optional

TRADE_ROOT = Path(os.environ.get("TRADE_ROOT", str(Path.home() / "trading-system-test")))

# ── Paths (shared) ────────────────────────────────────────────────
WATCHLIST_PATH = TRADE_ROOT / "config" / "watchlist.yaml"
DB_PATH = str(TRADE_ROOT / "database" / "bars.db")

# ── Simulation defaults (the "simConfig") ────────────────────────
DEFAULT_DAYS = 365
DEFAULT_TIMEFRAME = "5m"
DEFAULT_TRIALS = 20
DEFAULT_CASH = 100_000
DEFAULT_METHOD = "tune"  # tune | explore | interact

# ── Watchlist loader (shared between dashboard API and strategy-lab) ─
def load_watchlist() -> dict:
    """Load and return the raw watchlist YAML as a dict."""
    import yaml
    if not WATCHLIST_PATH.exists():
        return {"defaults": {}, "default_strategy_groups": [], "assets": []}
    with open(WATCHLIST_PATH) as f:
        data = yaml.safe_load(f) or {}
    # Normalize flat format — mirrors watchlist_mgmt._normalize_for_frontend
    if "assets" not in data or not isinstance(data.get("assets"), list):
        assets = []
        for key, val in data.items():
            if key == "defaults":
                continue
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        item.setdefault("asset_class", key)
                        assets.append(item)
        data["assets"] = assets
    return data


def get_simulation_config(symbol: Optional[str] = None) -> dict:
    """Return simulation-ready config for a symbol or global defaults.

    Combines watchlist overrides with simConfig fallbacks.
    Used by both dashboard/api/simulation.py and strategy-lab CLI.
    """
    wl = load_watchlist()
    assets = {a["symbol"]: a for a in wl.get("assets", [])}

    if symbol and symbol in assets:
        asset = assets[symbol]
        return {
            "symbol": symbol,
            "strategy_groups": asset.get("strategy_params", {}).get(
                "default_strategy_groups", DEFAULT_STRATEGY_GROUPS
            ),
            "max_position_dollar": asset.get("max_position_dollar", 100),
            "tp_levels": asset.get("tp_levels", [
                {"level": 1, "profit_pct": 2.0, "sell_pct": 0.25},
                {"level": 2, "profit_pct": 4.0, "sell_pct": 0.5},
                {"level": 3, "profit_pct": 6.0, "sell_pct": 0.25},
            ]),
        }

    # Global defaults (used when no symbol override)
    return {
        "symbol": symbol,
        "strategy_groups": wl.get("default_strategy_groups", DEFAULT_STRATEGY_GROUPS),
        "max_position_dollar": 100,
        "tp_levels": [
            {"level": 1, "profit_pct": 2.0, "sell_pct": 0.25},
            {"level": 2, "profit_pct": 4.0, "sell_pct": 0.5},
            {"level": 3, "profit_pct": 6.0, "sell_pct": 0.25},
        ],
    }
