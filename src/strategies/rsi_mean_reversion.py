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

        # ── No signal — score proximity to trigger zones ─────────────────
        conf = 0.0
        reason_parts = [f"{rsi_label}={rsi_val:.1f}\n\n        if rsi_val \u003c p[\"oversold_threshold\"]:\n            near_rsi = (p[\"oversold_threshold\"] - rsi_val) / p[\"oversold_threshold\"] * 100\n            conf = min(conf, 0.4 + (near_rsi / 100) * 0.3)\n            reason_parts.append(f\"Near oversold: depth {near_rsi:.1f}%\")\n        elif rsi_val \u003e overbought:\n            near_rsi = (rsi_val - overbought) / (100 - overbought) * 100\n            conf = min(conf, 0.4 + (near_rsi / 100) * 0.3)\n            reason_parts.append(f\"Near overbought: depth {near_rsi:.1f}%\")\n        else:\n            rsi_centerness = abs(rsi_val - 50) / 50\n            conf = min(0.2, rsi_centerness * 0.2)\n            reason_parts.append(\"Normal range\")\n\n        return StrategyResult(Signal.HOLD, round(max(0.01, conf), 2), \" | \".join(reason_parts))"}
