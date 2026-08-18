"""Strategy 7: VWAP Reversion (Institutional-grade).

Signal: Price deviates > 2× from daily VWAP → mean-revert entry
        Returns to VWAP → exit
Daily reset — VWAP recalculates each session.
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.vwap import vwap


class VWAP_Reversion(BaseStrategy):
    NAME = "VWAP_Reversion"
    DESCRIPTION = "Price reversion to daily VWAP — institutional mean-reversion strategy"
    DEFAULT_PARAMS = {
        "deviation_mult": 2.0,   # deviates more than 2× from VWAP
        "revert_threshold": 1.005,  # within 0.5% of VWAP → exit signal
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars for VWAP")

        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]
        volumes = [b.get("volume", 0) for b in bars]

        vwap_val = vwap(highs, lows, closes, volumes)
        if vwap_val < 0:
            return StrategyResult(Signal.HOLD, 0.0, "VWAP calculation incomplete")

        current_price = closes[-1]
        deviation_pct = (current_price - vwap_val) / vwap_val if vwap_val > 0 else 0

        # Price significantly above VWAP → mean-revert short opportunity
        if deviation_pct > p["deviation_mult"]:
            return StrategyResult(
                Signal.SELL, 0.7,
                f"Price ${current_price:.2f} is {deviation_pct*100:.1f}% above VWAP ${vwap_val:.2f}",
                stop_loss_price=current_price * 1.02,
            )

        # Price significantly below VWAP → mean-revert long opportunity
        if deviation_pct < -p["deviation_mult"]:
            return StrategyResult(
                Signal.BUY, 0.7,
                f"Price ${current_price:.2f} is {abs(deviation_pct)*100:.1f}% below VWAP ${vwap_val:.2f}",
                entry_price=current_price,
            )

        # Near VWAP → exit signal (mean-reversion complete)
        if abs(deviation_pct) < p["revert_threshold"]:
            return StrategyResult(
                Signal.HOLD, 0.5,
                f"Price converged to VWAP (${vwap_val:.2f}) — reversion complete",
            )

        # Within deviation range → monitor
        pct = abs(deviation_pct) * 100
        return StrategyResult(
            Signal.HOLD, 0.3,
            f"Price {pct:.1f}% from VWAP — monitoring",
        )

    def warm_up_bars_needed(self):
        return 5
