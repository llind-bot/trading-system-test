"""Strategy 2: SMA Crossover Momentum.

Signal: SMA(fast) crosses above SMA(slow) → BUY
        SMA(fast) crosses below SMA(slow) → SELL
Filter: Volume > 1.5× 20-bar average volume (confirms the move)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.sma import sma


class SMA_Crossover(BaseStrategy):
    NAME = "SMA_Crossover"
    DESCRIPTION = "SMA fast/slow crossover with volume confirmation"
    DEFAULT_PARAMS = {
        "sma_fast": 9,
        "sma_slow": 21,
        "volume_mult": 1.5,
        "volume_period": 20,
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        fast = sma([b["close"] for b in bars], p["sma_fast"])
        slow = sma([b["close"] for b in bars], p["sma_slow"])
        if fast < 0 or slow < 0:
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        # Volume filter
        vol_avg = sma([b["volume"] for b in bars], p["volume_period"])
        current_vol = bars[-1].get("volume", 0) if len(bars) > 0 else 0
        vol_confirmed = current_vol > vol_avg * p["volume_mult"]

        prev_fast = sma([b["close"] for b in bars[:-1]], p["sma_fast"])
        prev_slow = sma([b["close"] for b in bars[:-1]], p["sma_slow"])

        # Bullish crossover
        if fast > slow and prev_fast <= prev_slow:
            if vol_confirmed:
                conf = 0.8
                reason = f"SMA({p['sma_fast']}) crosses above SMA({p['sma_slow']}), volume confirmed"
            else:
                conf = 0.5
                reason = f"SMA crossover but low volume ({current_vol:.0f} vs {vol_avg:.0f})"
            return StrategyResult(Signal.BUY, conf, reason, entry_price=bars[-1]["close"])

        # Bearish crossover
        if fast < slow and prev_fast >= prev_slow:
            if vol_confirmed:
                conf = 0.8
                reason = f"SMA({p['sma_fast']}) crosses below SMA({p['sma_slow']}), volume confirmed"
            else:
                conf = 0.5
                reason = f"SMA cross down but low volume"
            return StrategyResult(Signal.SELL, conf, reason, stop_loss_price=bars[-1]["close"] * 0.98)

        # Trend check (are we in an uptrend?)
        if fast > slow:
            return StrategyResult(Signal.HOLD, 0.3, f"Trending up — SMA({p['sma_fast']})={fast:.2f} > SMA({p['sma_slow']})={slow:.2f}")
        else:
            return StrategyResult(Signal.HOLD, 0.3, f"Trending down")

    def warm_up_bars_needed(self):
        return max(21, 26)  # slow period + buffer
