"""3-level Take-Profit Ladder engine.

Each position has 3 TP levels (configurable per symbol):
- Level 1: sell 25% of position at profit_pct_1
- Level 2: sell 50% of remaining at profit_pct_2
- Level 3: sell remaining 25% at profit_pct_3

Evaluates current profit vs each level and returns TP events to execute.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import os
from src.data.symbol_utils import to_alpaca

# ── Default TP levels — mirrors stock defaults in config/watchlist.yaml and risk/manager.py ──
DEFAULT_TP_LEVELS_STOCK = [
    {"level": 1, "sell_pct": 0.25, "profit_pct": 3.0},
    {"level": 2, "sell_pct": 0.50, "profit_pct": 6.0},
    {"level": 3, "sell_pct": 0.25, "profit_pct": 10.0},
]

DEFAULT_TP_LEVELS_CRYPTO = [
    {"level": 1, "sell_pct": 0.25, "profit_pct": 2.0},
    {"level": 2, "sell_pct": 0.50, "profit_pct": 4.0},
    {"level": 3, "sell_pct": 0.25, "profit_pct": 7.0},
]

DEFAULT_TP_LEVELS = DEFAULT_TP_LEVELS_STOCK


@dataclass
class TPEvent:
    """A TP ladder hit that needs to be executed."""
    symbol: str
    level: int
    qty_to_sell: float      # quantity to sell
    profit_pct: float       # current profit % (hit threshold)
    order_value_dollar: float
    is_last_level: bool


@dataclass
class TPConfig:
    """TP ladder config for a single symbol."""
    levels: list[dict]  # [{"level": 1, "sell_pct": 0.25, "profit_pct": 3.0}, ...]

    def get_levels(self) -> list[tuple[int, float, float]]:
        """Return list of (level_num, sell_percentage, profit_threshold)."""
        return [(lv["level"], lv["sell_pct"], lv["profit_pct"]) for lv in self.levels]


class TPLadderEngine:
    """Manages TP ladder state and evaluates hits per position."""

    def __init__(self):
        self._tp_configs: dict[str, TPConfig] = {}  # symbol → config from watchlist
        self._config_mtime: float = 0
        self._reload_dir = Path(__file__).parent.parent.parent / "config"
        # Track cumulative qty sold per position across tick passes (keyed by position entry_date or symbol)
        self._cumulative_sold: dict[str, float] = {}  # symbol → total qty sold so far

    def register_symbol(self, symbol: str, tp_levels: list[dict]) -> None:
        """Register TP ladder config for a symbol (from watchlist.yaml)."""
        self._tp_configs[symbol] = TPConfig(levels=tp_levels)

    def get_config(self, symbol: str) -> Optional[TPConfig]:
        # Exact match first, then try normalized (strip slashes for Alpaca compat)
        config = self._tp_configs.get(symbol)
        if not config:
            normalized = to_alpaca(symbol)
            config = self._tp_configs.get(normalized)
        return config

    def check_tp_hits(self, symbol: str, avg_cost: float, current_price: float,
                       qty_remaining: float, tp_levels_fired: list[int] = None) -> list[TPEvent]:
        """Check which TP levels are hit for a position. Returns list of events to execute.
        
        Args:
            symbol: Position symbol (used as key in cumulative sold tracking)
            tp_levels_fired: List of level numbers already fired this round
                             (caller tracks via DB tp_levels_fired column).
        """
        config = self.get_config(symbol)
        if not config or qty_remaining <= 0:
            return []

        # Skip TP checks if position is effectively closed (near-zero qty left after sells)
        if qty_remaining < 1e-6:
            self.reset_position_sold(symbol)
            return []

        # Normalize tp_levels_fired — could be int, list, tuple, or JSON string from DB
        raw = tp_levels_fired
        if isinstance(raw, str):
            try:
                import json
                fired = set(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                fired = set()
        elif hasattr(raw, '__iter__') and not isinstance(raw, (int, str)):
            fired = set(int(x) for x in raw if x is not None)
        else:
            fired = set()

        # Use cumulative sold tracking to account for qty already sold across tick passes.
        # qty_remaining from Alpaca may still include qty from sells that haven't settled yet,
        # so we subtract what the TP engine already determined was sold.
        cum_sold = self._cumulative_sold.get(symbol, 0)

        events = []

        profit_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0

        # Effective qty for TP calc: what's left after subtracting previously-sold amounts
        qty_left = max(0, qty_remaining - cum_sold)

        for level, sell_pct, threshold in config.get_levels():
            if level in fired:
                continue
            if profit_pct >= threshold:
                is_last = (level == len(config.get_levels()))
                qty_to_sell = max(0, qty_remaining - cum_sold)
                if not is_last:
                    qty_to_sell = qty_left * sell_pct

                order_value = qty_to_sell * current_price

                events.append(TPEvent(
                    symbol=symbol,
                    level=level,
                    qty_to_sell=qty_to_sell,
                    profit_pct=profit_pct,
                    order_value_dollar=order_value,
                    is_last_level=is_last,
                ))
                fired.add(level)
                # Track cumulative sold so next pass subtracts this amount
                cum_sold += qty_to_sell

        return events

    def record_qty_sold(self, symbol: str, qty: float) -> None:
        """Record qty sold from a TP level that executed. 
        Used by the tracker after each TP sell to prevent stale qty across tick passes."""
        self._cumulative_sold[symbol] = self._cumulative_sold.get(symbol, 0) + qty

    def reset_position_sold(self, symbol: str) -> None:
        """Reset cumulative sold tracking when a position is fully closed or reset."""
        self._cumulative_sold.pop(symbol, None)

    def _get_watchlist_mtime(self) -> float:
        """Get last modified timestamp of watchlist.yaml."""
        wl_path = self._reload_dir / "watchlist.yaml"
        if wl_path.exists():
            return os.path.getmtime(wl_path)
        return 0

    def check_reload(self) -> bool:
        """Check if watchlist.yaml has changed since last load.
        
        Returns True if reload was needed and performed, False otherwise.
        """
        current_mtime = self._get_watchlist_mtime()
        if current_mtime > self._config_mtime:
            self.reload()
            return True
        return False

    def reload(self) -> None:
        """Reload TP configs from watchlist.yaml."""
        import yaml
        
        wl_path = self._reload_dir / "watchlist.yaml"
        if not wl_path.exists():
            return
        
        with open(wl_path) as f:
            watchlist = yaml.safe_load(f) or {}
        
        self._tp_configs.clear()
        assets = watchlist.get("assets", [])
        for asset in assets:
            sym = asset.get("symbol")
            if sym and "tp_levels" in asset:
                tp_levels = asset["tp_levels"]
                # Register original name (with slashes — matches engine's self.positions keys)
                self._tp_configs[sym] = TPConfig(levels=tp_levels)
        
        # Also read per-strategy strategy_configs block (dynamic TP from tuning)
        # Format: { symbol_strategies: [TPEvent(...)], "strategy_configs": {
        #             SMA_Crossover: { tp_levels: [0.01, 0.02, 0.03] }, ... }}
        for asset in assets:
            sym = asset.get("symbol")
            strat_configs = asset.get("strategy_configs", {})
            if not strat_configs or not sym:
                continue
            # Merge per-strategy configs: for each strategy's TP levels,
            # use the dynamic values (they override the asset-level tp_levels)
            for strat_name, cfg in strat_configs.items():
                tp_from_cfg = cfg.get("tp_levels")
                if not tp_from_cfg:
                    continue
                # Convert from decimal fractions (tuning output) to percentage thresholds
                # e.g. [0.01, 0.02, 0.03] → [{level:1,sell_pct:0.25,profit_pct:1}, ...]
                split = cfg.get("tp_split", [0.25, 0.50, 0.25])
                dynamic_tp_levels = []
                for i, level in enumerate(tp_from_cfg):
                    dynamic_tp_levels.append({
                        "level": i + 1,
                        "sell_pct": split[i] if i < len(split) else 0.25,
                        "profit_pct": round(level * 100, 2),  # convert fraction → pct
                    })
                # Use strategy name as symbol key so it's distinct from asset-level config
                self._tp_configs[f"{sym}/{strat_name}"] = TPConfig(levels=dynamic_tp_levels)
        
        self._config_mtime = self._get_watchlist_mtime()
    
    def get_tp_for_symbol(self, symbol: str) -> Optional[TPConfig]:
        """Get TP config for a symbol (with normalization)."""
        config = self._tp_configs.get(symbol)
        if not config:
            normalized = to_alpaca(symbol)
            config = self._tp_configs.get(normalized)
        return config


def load_tp_configs_from_watchlist(symbol: str = None) -> Optional[list[dict]]:
    """Load TP levels for a symbol from watchlist.yaml."""
    try:
        import yaml
    except ModuleNotFoundError:
        return None
    
    config_dir = Path(__file__).parent.parent.parent / "config"
    watchlist_path = config_dir / "watchlist.yaml"
    
    if not watchlist_path.exists():
        return None

    try:
        with open(watchlist_path) as f:
            watchlist = yaml.safe_load(f) or {}
    except Exception:
        return None

    assets = watchlist.get("assets", [])

    if symbol is None:
        # Return all configs keyed by symbol (skip disabled assets)
        result = {}
        for asset in assets:
            sym = asset.get("symbol")
            if sym and "tp_levels" in asset and not asset.get("enabled", False) == False:
                result[sym] = asset["tp_levels"]
        return result

    # Find matching symbol (normalize BTCUSD ↔ BTC/USD)
    normalized = to_alpaca(symbol)
    for asset in assets:
        sym = to_alpaca(str(asset.get("symbol", "")))
        if sym == normalized and "tp_levels" in asset:
            return asset["tp_levels"]

    # Not found — return stock defaults (safe fallback)
    return DEFAULT_TP_LEVELS_STOCK


def register_all_from_watchlist(engine: TPLadderEngine) -> None:
    """Register all symbols from watchlist.yaml into the given engine.

    Call this once at system startup to load TP configs for every asset.
    Registers with original name, normalized (slashes stripped), AND
    the no-slash Alpaca form so lookups work regardless of caller format.
    """
    all_configs = load_tp_configs_from_watchlist()
    if not all_configs:
        return

    for symbol, tp_levels in all_configs.items():
        engine.register_symbol(symbol, tp_levels)
        # Also register with slashes stripped so Alpaca-style names work
        normalized = to_alpaca(symbol)
        if normalized != symbol:
            engine.register_symbol(normalized, tp_levels)
        # Register the no-slash form (e.g. 'LTCUSD', 'AVAXUSD') used in the positions DB
        # This handles: BTC/USD → BTCUSD, ETH/USD → ETHUSD, etc.
        no_slash = symbol.replace('/', '')
        if no_slash != symbol:
            engine.register_symbol(no_slash, tp_levels)
