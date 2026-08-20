"""Watchlist management endpoints (read/write).

Flat format only: {defaults, assets:[...]}. No strategy_groups.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import shutil
from datetime import datetime

import yaml
from fastapi import APIRouter, HTTPException

WATCHLIST_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "watchlist.yaml"

router = APIRouter()
DEFAULT_TP_LEVELS = [
    {"level": 1, "profit_pct": 2.0, "sell_pct": 0.25},
    {"level": 2, "profit_pct": 4.0, "sell_pct": 0.5},
    {"level": 3, "profit_pct": 6.0, "sell_pct": 0.25},
]

VALID_ASSET_CLASSES = {"stock", "crypto"}
REQUIRED_ASSET_FIELDS = ["asset_class", "symbol"]


def _validate_asset(a: dict) -> list[str]:
    """Return list of validation error strings for an asset dict. Empty = valid."""
    errors = []
    if not isinstance(a, dict):
        return ["Asset must be a dict"]
    sym = a.get("symbol", "")
    if not sym or not str(sym).strip():
        errors.append(f"symbol is empty")
    ac = a.get("asset_class", "")
    if ac not in VALID_ASSET_CLASSES:
        errors.append(f"asset_class must be 'stock' or 'crypto', got '{ac}'")
    return errors


def _normalize_for_frontend(data: dict) -> dict:
    """Normalize watchlist data for the frontend.

    Returns { defaults, assets:[...] }. No strategy_groups — dead weight.
    Does NOT mutate input dicts; returns fresh copies with safe fallbacks.
    """
    if not data:
        return {"defaults": {}, "assets": []}

    # Flat format — already compatible, but clone to avoid mutating caller
    if "assets" in data and isinstance(data["assets"], list):
        assets = []
        for a in data["assets"]:
            if not isinstance(a, dict):
                continue
            na = {}
            na["symbol"] = a.get("symbol", "")  # keep as-is (validation later)
            na["asset_class"] = a.get("asset_class", "stock")
            na["max_position_dollar"] = a.get("max_position_dollar", 100)
            na["sl_pct"] = a.get("sl_pct")
            na["trailing_stop_pct"] = a.get("trailing_stop_pct")
            na["tp_levels"] = a.get("tp_levels", list(DEFAULT_TP_LEVELS))
            na["strategies"] = a.get("strategies", [])
            na["strategy_params"] = a.get("strategy_params", {})
            na["enabled"] = a.get("enabled", True)
            assets.append(na)
        return {
            "defaults": data.get("defaults", {}),
            "assets": assets,
        }

    # Legacy grouped format (stocks:[...], crypto:[...]) — flatten
    assets = []
    defaults = {}
    for key, val in data.items():
        if key == "defaults":
            defaults = val if isinstance(val, dict) else {}
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    ac = key if key not in ("defaults",) else "stock"
                    na = {
                        "symbol": item.get("symbol", ""),
                        "asset_class": item.get("asset_class", ac),
                        "max_position_dollar": item.get("max_position_dollar", 100),
                        "sl_pct": item.get("sl_pct"),
                        "trailing_stop_pct": item.get("trailing_stop_pct"),
                        "tp_levels": item.get("tp_levels", list(DEFAULT_TP_LEVELS)),
                        "strategies": item.get("strategies", []),
                        "strategy_params": item.get("strategy_params", {}),
                        "enabled": item.get("enabled", True),
                    }
                    assets.append(na)
        elif isinstance(val, dict) and "symbol" in val:
            ac = key if key not in ("defaults",) else "stock"
            na = {
                "symbol": val.get("symbol", ""),
                "asset_class": val.get("asset_class", ac),
                "max_position_dollar": val.get("max_position_dollar", 100),
                "sl_pct": val.get("sl_pct"),
                "trailing_stop_pct": val.get("trailing_stop_pct"),
                "tp_levels": val.get("tp_levels", list(DEFAULT_TP_LEVELS)),
                "strategies": val.get("strategies", []),
                "strategy_params": val.get("strategy_params", {}),
                "enabled": val.get("enabled", True),
            }
            assets.append(na)

    return {
        "defaults": defaults or data.get("defaults", {}),
        "assets": assets,
    }


@router.get("/api/watchlist-full")
def watchlist_full():
    """Full watchlist config as parsed YAML (normalized for frontend)."""
    try:
        with open(WATCHLIST_PATH) as f:
            data = yaml.safe_load(f)
        return _normalize_for_frontend(data or {})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/strategies-full")
def strategies_full():
    """Full strategies config as parsed YAML."""
    STRAT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "strategies.yaml"
    try:
        with open(STRAT_PATH) as f:
            data = yaml.safe_load(f)
        return data or {}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Strategies config not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/watchlist")
def update_watchlist(data: dict):
    """Write watchlist YAML.

    Accepts {defaults, assets:[...]}. Preserves top-level defaults from disk.
    Validates all asset dicts before writing. Backs up file before overwriting.
    """
    import json; print(f"[WATCHLIST PUT] received data keys: {list(data.keys())}")
    if isinstance(data.get("assets"), list):
        for a in data["assets"]:
            sym = a.get("symbol", "??")
            has_enabled = "enabled" in a
            enabled_val = a.get("enabled", "NOT PRESENT")
            print(f"[WATCHLIST PUT]   asset={sym}: has_enabled={has_enabled} enabled={enabled_val}")
        print(f"[WATCHLIST PUT] total assets: {len(data['assets'])}")
    try:
        # Validate incoming assets first (before any mutations)
        if not isinstance(data.get("assets"), list):
            import logging; logging.getLogger().warning(f"[watchlist] no assets list: {type(data.get('assets'))}")
            raise HTTPException(status_code=400, detail="'assets' must be a list")

        validation_errors = []
        for i, a in enumerate(data["assets"]):
            errs = _validate_asset(a)
            if errs:
                import logging; logging.getLogger().warning(f"[watchlist] asset[{i}] errors: {errs} | data={a}")
                for e in errs:
                    validation_errors.append(f"assets[{i}]: {e}")
        if validation_errors:
            raise HTTPException(status_code=400, detail="\n".join(validation_errors))

        # Preserve existing top-level defaults from disk
        existing = {}
        if WATCHLIST_PATH.exists():
            with open(WATCHLIST_PATH) as f:
                existing = yaml.safe_load(f) or {}

        preserved_defaults = existing.get("defaults", {})

        # Normalize incoming data
        normalized = _normalize_for_frontend(data)
        assets_out = normalized["assets"]

        # Build output — drop empty symbols (user deleted them)
        output_assets = [a for a in assets_out if a.get("symbol", "")]

        # Guard: reject wiping all entries (requires explicit restore from backup/git)
        if not output_assets:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove all watchlist entries. At least one asset is required.",
            )

        output = {
            "defaults": preserved_defaults or normalized.get("defaults", {}),
            "assets": output_assets,
        }

        # Backup before overwrite
        backup_path = WATCHLIST_PATH.with_suffix(f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(WATCHLIST_PATH, backup_path)

        with open(WATCHLIST_PATH, "w") as f:
            yaml.dump(output, f, default_flow_style=False, sort_keys=False)

        return {"status": "ok", "message": "Watchlist updated", "_backup": str(backup_path)}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Watchlist file not found")
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{str(e)}\n{traceback.format_exc()}")
