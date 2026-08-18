"""Strategy 1: RSI Mean Reversion (Larry Connors' 2-period RSI).

Signal: RSI(period) < oversold_threshold → BUY
Filters: ADX > threshold (trend filter — don't trade in dead markets)

Configurable:
  rsi_period:    RSI lookback period (default 7, 2 = Larry Connors fast mean-reversion)
  oversold_threshold:  Level to trigger BUY when RSI drops below (default 25)
  overbought_threshold: Level to trigger SELL when RSI rises above (default 80)
  adx_threshold:    Minimum ADX for strong-trend BUY (default 10)
  adx_period:       ADX calculation period (default 14)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.rsi import rsi, rsi2
from src.strategies.indicators.adx import adx


class RSI_MeanReversion(BaseStrategy):
    NAME = "RSI_MeanReversion"
    DESCRIPTION = "Larry Connors' 2-period RSI mean reversion strategy with ADX trend filter"
    DEFAULT_PARAMS = {
        "rsi_period": 7,
        "oversold_threshold": 25,
        "overbought_threshold": 80,
        "adx_threshold": 10,
        "adx_period": 14,
    }

    def warm_up_bars_needed(self):
        return 15

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        p = {**self.DEFAULT_PARAMS, **(params or {})}
        period = p.get("rsi_period", 2)
        overbought = p.get("overbought_threshold", 80)

        closes = [b["close"] for b in bars]
        # Use configurable RSI period — rsi2() for fast Connors mode, rsi() otherwise
        if period == 2:
            rsi_val = rsi2(closes)
        else:
            rsi_val = rsi(closes, period)

        if rsi_val < 0:
            return StrategyResult(Signal.HOLD, 0.0, "RSI calculation incomplete")

        # ADX filter
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        adx_val = adx(highs, lows, closes, p["adx_period"])

        current_price = closes[-1]
        rsi_label = f"RSI({period})"

        if rsi_val <= p["oversold_threshold"]:
            if adx_val > p["adx_threshold"]:
                return StrategyResult(
                    Signal.BUY, 0.85,
                    f"{rsi_label}={rsi_val:.1f} oversold + ADX={adx_val:.1f} trending",
                    entry_price=current_price,
                )
            elif adx_val <= p["adx_threshold"]:
                # Weak trend — still buy but lower confidence
                return StrategyResult(
                    Signal.BUY, 0.5,
                    f"{rsi_label}={rsi_val:.1f} oversold (ADX weak {adx_val:.1f})",
                    entry_price=current_price,
                )

        if rsi_val >= overbought:
            return StrategyResult(
                Signal.SELL, 0.7,
                f"{rsi_label}={rsi_val:.1f} overbought",
                stop_loss_price=current_price * 0.98,
            )

        return StrategyResult(Signal.HOLD, 0.0, f"{rsi_label}={rsi_val:.1f} in range")
