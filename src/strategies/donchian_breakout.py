"""Strategy 6: Donchian Channel Breakout (Turtle Trading).

Signal: Price breaks 20-period high → BUY
        Price breaks 20-period low → SELL
Filter: ATR(14) > threshold (need actual volatility to run)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.atr import atr


class Donchian_Breakout(BaseStrategy):
    NAME = "Donchian_Breakout"
    DESCRIPTION = "Donchian channel breakout — classic turtle trading signal"
    DEFAULT_PARAMS = {
        "donchian_period": 20,
        "atr_min": 0.5,  # minimum ATR to trade (needs volatility)
        "breakout_confirm_pct": 0.01,  # price must exceed channel by this % (not just touch)
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        if len(bars) <= p["donchian_period"]:
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        recent = closes[-p["donchian_period"]:]
        upper_channel = max(recent)
        lower_channel = min(recent)

        current_price = closes[-1]

        # ATR check
        atr_val = atr(highs, lows, closes, 14)
        if atr_val < p["atr_min"] or atr_val < 0:
            return StrategyResult(Signal.HOLD, 0.2, f"ATR too low ({atr_val:.2f}) — no volatility")

        # Breakout above upper channel (with confirmation gap)
        if current_price >= upper_channel * (1 + p["breakout_confirm_pct"]):
            return StrategyResult(
                Signal.BUY, 0.75,
                f"Donchian breakout above {p['donchian_period']}-period high ${upper_channel:.2f} (+{p['breakout_confirm_pct']*100:.1f}% confirm)",
                entry_price=current_price,
            )

        # Breakdown below lower channel (with confirmation gap)
        if current_price <= lower_channel * (1 - p["breakout_confirm_pct"]):
            return StrategyResult(
                Signal.SELL, 0.75,
                f"Donchian breakdown below {p['donchian_period']}-period low ${lower_channel:.2f} (-{p['breakout_confirm_pct']*100:.1f}% confirm)",
                stop_loss_price=current_price * 1.02,
            )

        # In-channel position tracking
        distance_from_upper = (upper_channel - current_price) / upper_channel if upper_channel > 0 else 999
        distance_from_lower = (current_price - lower_channel) / lower_channel if lower_channel > 0 else 999

        return StrategyResult(
            Signal.HOLD, 0.3,
            f"In channel (${lower_channel:.2f} - ${upper_channel:.2f}), ATR={atr_val:.4f}"
        )

    def warm_up_bars_needed(self):
        return 25
