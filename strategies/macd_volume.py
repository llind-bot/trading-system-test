"""Strategy 5: MACD + Volume Confirmation.

Signal: MACD histogram turns positive AND volume > threshold → BUY
Filter: Price must be above SMA(50) (trend alignment)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.macd import macd
from src.strategies.indicators.sma import sma


class MACD_Volume(BaseStrategy):
    NAME = "MACD_Volume"
    DESCRIPTION = "MACD histogram crossover with volume confirmation"
    DEFAULT_PARAMS = {
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "sma_period": 50,
        "volume_mult": 1.3,
        "volume_period": 20,
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        closes = [b["close"] for b in bars]
        macd_line, signal_line, histogram = macd(
            closes, p["macd_fast"], p["macd_slow"], p["macd_signal"]
        )

        if histogram < 0:
            return StrategyResult(Signal.HOLD, 0.0, "MACD histogram negative")

        # Trend filter: price above SMA(50)
        sma_50 = sma(closes, p["sma_period"])
        if sma_50 < 0 or closes[-1] < sma_50:
            return StrategyResult(Signal.HOLD, 0.3, f"Price below SMA({p['sma_period']})")

        # Volume filter
        vol_avg = sma([b["volume"] for b in bars], p["volume_period"]) if (p.get("volume_mult")) else float('inf')
        current_vol = bars[-1].get("volume", 0)
        vol_ok = current_vol > vol_avg * p["volume_mult"]

        current_price = closes[-1]
        conf = 0.8 if vol_ok else 0.5

        # Check previous histogram (was it negative?) → confirms turn positive
        if len(bars) >= 2:
            prev_closes = [b["close"] for b in bars[:-1]]
            _, _, prev_hist = macd(prev_closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
            if prev_hist < 0 and histogram > 0:
                # Confirming the turn positive — strong signal
                return StrategyResult(
                    Signal.BUY, conf + 0.15,
                    f"MACD histogram turned positive ({histogram:.4f}), {'' if vol_ok else 'low '}volume",
                    entry_price=current_price,
                )

        # Still positive but no confirmed turn → mild buy signal
        if histogram > 0:
            return StrategyResult(
                Signal.HOLD, conf * 0.7,
                f"MACD histogram positive ({histogram:.4f}) — no confirmed turn",
            )

        return StrategyResult(Signal.HOLD, 0.0, "No MACD signal")

    def warm_up_bars_needed(self):
        return 40
