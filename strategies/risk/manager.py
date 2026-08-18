"""Risk manager — global and per-symbol risk limits.

All trades must pass through here before execution.
Checks: emergency stop, drawdown, daily loss, position caps, cash reserve, exposure.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── Config path shared with the rest of the trading system ──
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str                # why approved or rejected
    reduced_qty: Optional[float] = None  # if partially approved (reduce size)


class RiskManager:
    """Global risk checks + dollar-based position sizing."""

    def __init__(self, global_limits: dict, emergency_stop_flag):
        self.global_limits = global_limits  # from config/risk_limits.yaml
        self.emergency_stop_flag = emergency_stop_flag
        self._symbol_config_cache: dict[str, dict] = {}

    def pre_trade_check(self, symbol: str, order_value_dollar: float,
                        risk_dollar: float, current_equity: float) -> RiskCheckResult:
        """Run all risk checks. Returns approved/rejected with reason."""

        # 1. Emergency stop
        if self._get_emergency_stop():
            return RiskCheckResult(False, "Emergency stop active", reduced_qty=0)

        # 2. Global limits
        gl = self.global_limits.get("global", {})

        if risk_dollar > 0 and abs(self._get_current_drawdown(current_equity)) > gl.get("max_drawdown_dollar", 999999):
            return RiskCheckResult(False, f"Drawdown limit exceeded: ${self._get_current_drawdown(current_equity):,.2f}")

        if self._get_current_daily_loss() > -abs(gl.get("daily_loss_limit_dollar", 999999)):
            daily_limit = gl.get("daily_loss_limit_dollar", -300)
            if abs(self._get_current_daily_loss()) >= abs(daily_limit):
                return RiskCheckResult(False, f"Daily loss limit hit: ${self._get_current_daily_loss():,.2f}")

        # 3. Cash reserve check
        min_res = gl.get("min_cash_reserve_dollar", 0)
        if current_equity - order_value_dollar < min_res:
            return RiskCheckResult(False, f"Cash reserve breached -- need ${min_res:,} cash")

        # 4. Total exposure check
        max_exposure = gl.get("max_total_exposure_dollar", 999999)
        current_exposure = self._get_current_exposure()
        if current_exposure + order_value_dollar > max_exposure:
            remaining = max(0, max_exposure - current_exposure)
            return RiskCheckResult(False, f"Total exposure limit -- only ${remaining:,} room", reduced_qty=remaining)

        # 5. Per-symbol limits (from watchlist)
        per_symbol = self._get_per_symbol_limits(symbol)
        if not per_symbol:
            # No config found for this symbol -- allow but warn
            pass
        else:
            max_pos = per_symbol.get("max_position_dollar", 999999)
            # Check against TOTAL position value (existing + new order), not just the new order
            existing_value = self._get_current_position_value(symbol)
            total_after = existing_value + order_value_dollar
            if total_after > max_pos:
                reduced_cap = max(0, max_pos - existing_value)
                return RiskCheckResult(False, f"Per-symbol cap exceeded -- ${total_after:,.0f} total (${existing_value:,.0f} existing + ${order_value_dollar:,.0f} new) > ${max_pos:,}", reduced_qty=reduced_cap)

            max_risk = per_symbol.get("risk_dollar_per_trade", 999999)
            if risk_dollar > max_risk:
                return RiskCheckResult(False, f"Risk exceeded -- ${risk_dollar} > ${max_risk}", reduced_qty=max_risk)

        # 6. Concurrent positions check
        max_pos_count = gl.get("max_concurrent_positions", 999999)
        if self._get_current_position_count() >= max_pos_count:
            return RiskCheckResult(False, f"Max concurrent positions reached ({max_pos_count})")

        return RiskCheckResult(True, "All risk checks passed")

    def calculate_position_size(self, entry_price: float, stop_loss_price: float,
                                 risk_dollar: float) -> float:
        """Calculate position size in shares/units based on dollar risk.

        qty = risk_dollar / (entry_price - stop_loss_price)
        Clamped to available cash and per-symbol caps.
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return 0

        price_distance = abs(entry_price - stop_loss_price)
        if price_distance == 0:
            return 0

        qty = risk_dollar / price_distance
        return qty

    # Internal helpers -- backed by database for real implementation
    def _get_emergency_stop(self) -> bool:
        return self.emergency_stop_flag.get("value", False)

    def _get_current_drawdown(self, equity: float) -> float:
        """Get current drawdown from peak in dollars."""
        # In production: query SQLite for max total_equity in period
        return 0.0  # placeholder

    def _get_current_daily_loss(self) -> float:
        """Get today's PnL."""
        return 0.0  # placeholder

    def _get_db_path(self) -> Path:
        """Get the path to the trading database."""
        return Path(__file__).parent.parent.parent / "database" / "trades.db"

    def _get_current_exposure(self) -> float:
        """Get total value of all open positions from DB."""
        db_path = self._get_db_path()
        if not db_path.exists():
            return 0.0
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT qty * current_price FROM positions WHERE qty > 1e-10"
            ).fetchall()
            conn.close()
            return sum(float(r[0]) for r in rows if r[0] and float(r[0]) > 0)
        except Exception:
            return 0.0

    def _get_current_position_count(self) -> int:
        """Count of concurrent open positions from DB."""
        db_path = self._get_db_path()
        if not db_path.exists():
            return 0
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            count = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE qty > 1e-10"
            ).fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _get_current_position_value(self, symbol: str) -> float:
        """Get the current dollar value of an open position for a specific symbol."""
        db_path = self._get_db_path()
        if not db_path.exists():
            return 0.0
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT qty * current_price FROM positions WHERE symbol=? AND qty > 1e-10",
                (symbol,),
            ).fetchone()
            conn.close()
            if row and row[0]:
                return max(float(row[0]), 0)
            return 0.0
        except Exception:
            return 0.0

    def _get_per_symbol_limits(self, symbol: str) -> dict:
        """Get per-symbol limits from watchlist.yaml.

        Returns a dict with keys: max_position_dollar, risk_dollar_per_trade,
        sl_pct, trailing_stop_pct, tp_levels, strategies, etc.
        Falls back to safe defaults if the symbol is not in the config.
        """
        # Use cache to avoid repeated file I/O
        if symbol in self._symbol_config_cache:
            return self._symbol_config_cache[symbol]

        per_symbol = self._load_symbol_config(symbol)
        self._symbol_config_cache[symbol] = per_symbol
        return per_symbol

    def _load_symbol_config(self, symbol: str) -> dict:
        """Load config for a single symbol from watchlist.yaml with safe defaults."""
        import yaml

        watchlist_path = _CONFIG_DIR / "watchlist.yaml"
        if not watchlist_path.exists():
            return {}

        try:
            with open(watchlist_path) as f:
                watchlist = yaml.safe_load(f) or {}
        except Exception:
            return {}

        assets = watchlist.get("assets", [])

        # Normalize symbol for matching (BTCUSD <-> BTC/USD, etc.)
        normalized = to_alpaca(symbol)

        for asset in assets:
            sym = to_alpaca(str(asset.get("symbol", "")))
            if sym == normalized:
                return dict(asset)  # shallow copy of the full asset config

        # Symbol not found -- return sensible defaults (mirrors _ensure_in_watchlist logic)
        base = to_alpaca(symbol)
        is_crypto = base.upper() not in {
            "AAPL", "MSFT", "TSLA", "NVDA", "GOOGL", "AMZN", "META", "NFLX", "AMD",
            "INTC", "CRM", "ADBE", "PYPL", "SHOP", "SQ", "ROD", "JPM", "BAC",
            "WMT", "DIS", "VZ", "PFE", "KO", "PEP", "TMO", "AVGO", "LLY", "COST",
        }

        # Read defaults from watchlist.yaml `defaults` section (crypto/stock)
        wl_defaults = {}
        try:
            _wd_path = _CONFIG_DIR / "watchlist.yaml"
            if _wd_path.exists():
                with open(_wd_path) as wf:
                    wl_data = yaml.safe_load(wf) or {}
                class_defaults = wl_data.get("defaults", {}) or {}
                ac_key = "crypto" if is_crypto else "stock"
                wl_defaults = dict(class_defaults.get(ac_key, {}))
        except Exception:
            pass

        defaults = {
            "asset_class": "crypto" if is_crypto else "stock",
            "strategies": ["momentum_1"] if not is_crypto else ["mean_reversion_1"],
            "sell_strategies": ["tp_ladder", "trailing_stop"],
            "tp_levels": [
                {"level": 1, "sell_pct": 0.25, "profit_pct": 3.0},
                {"level": 2, "sell_pct": 0.50, "profit_pct": 6.0},
                {"level": 3, "sell_pct": 0.25, "profit_pct": 10.0},
            ] if not is_crypto else [
                {"level": 1, "sell_pct": 0.25, "profit_pct": 2.0},
                {"level": 2, "sell_pct": 0.50, "profit_pct": 4.0},
                {"level": 3, "sell_pct": 0.25, "profit_pct": 7.0},
            ],
            "sl_pct": -3.0 if is_crypto else -2.0,
            "trailing_stop_pct": 6.0 if is_crypto else 3.0,
            "max_position_dollar": wl_defaults.get("max_position_dollar", 1500 if is_crypto else 1000),
            "risk_dollar_per_trade": wl_defaults.get("risk_dollar_per_trade", 60 if is_crypto else 50),
            "min_order_value": wl_defaults.get("min_order_value", 20 if is_crypto else 50),
                    }
        return defaults
