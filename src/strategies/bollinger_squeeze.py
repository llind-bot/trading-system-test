"""Strategy 4: Bollinger Band Squeeze + Breakout.

Signal: BB bandwidth drops below threshold → squeeze detected. Entry on breakout through upper band with volume confirmation.
Filters: ATR must be declining before squeeze (volatility compression)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.bollinger import bollinger_bands
from src.strategies.indicators.atr import atr


class Bollinger_Squeeze(BaseStrategy):
    NAME = "Bollinger_Squeeze"
    DESCRIPTION = "Bollinger Band squeeze detection with breakout entry"
    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_multiplier": 2.0,
        "squeeze_bandwidth_threshold": 0.01,  # bandwidth below this = squeeze (was 5% — fires on any compression)
        "atr_decline_bars": 10,
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        volumes = [b.get("volume", 0) for b in bars]

        bb = bollinger_bands(closes, p["bb_period"], p["bb_multiplier"])
        if bb is None:
            return StrategyResult(Signal.HOLD, 0.0, "BB calc incomplete")

        # Check if in squeeze (low bandwidth)
        in_squeeze = bb["bandwidth"] < p["squeeze_bandwidth_threshold"]

        # Check ATR decline (volatility compression)
        atr_vals = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i]["high"] - bars[i]["low"],
                abs(bars[i]["high"] - bars[i-1]["close"]),
                abs(bars[i]["low"] - bars[i-1]["close"])
            )
            atr_vals.append(tr)

        atr_declining = False
        if len(atr_vals) >= p["atr_decline_bars"]:
            recent_atr = sum(atr_vals[-5:]) / 5
            older_atr = sum(atr_vals[-p["atr_decline_bars"]:-5]) / (p["atr_decline_bars"] - 5)
            atr_declining = recent_atr < older_atr if older_atr > 0 else True

        current_price = closes[-1]

        # In squeeze + ATR declining → waiting for breakout
        if in_squeeze and atr_declining:
            # Breakout above upper band
            if current_price >= bb["upper"]:
                return StrategyResult(
                    Signal.BUY, 0.75,
                    f"BB squeeze breakout! BW={bb['bandwidth']:.4f}, ATR compressing",
                    entry_price=current_price,
                )
            # Breakdown below lower band → SELL
            elif current_price <= bb["lower"]:
                return StrategyResult(
                    Signal.SELL, 0.75,
                    f"BB squeeze breakdown! BW={bb['bandwidth']:.4f}",
                    stop_loss_price=current_price * 1.02,
                )
            else:
                return StrategyResult(Signal.HOLD, 0.3, "In squeeze — waiting for breakout")

        # Not in squeeze — check if price is at band extremes
        if current_price >= bb["upper"] * 0.995:
            return StrategyResult(Signal.HOLD, 0.4, f"Near upper band (${bb['upper']:.2f})")
        elif current_price <= bb["lower"] * 1.005:
            return StrategyResult(Signal.HOLD, 0.4, f"Near lower band (${bb['lower']:.2f})")

        if conf == 0.0:
            # Neutral scoring: distance from center of BB
            bb_width = bb["upper"] - bb["lower"]
            if bb_width > 0:
                pct_in_bb = (current_price - bb["lower"]) / bb_width * 100
            else:
                pct_in_bb = 50
            rsi_centerness = abs(pct_in_bb - 50) / 50
            conf = min(0.2, rsi_centerness * 0.2)
        
        return StrategyResult(Signal.HOLD, round(max(0.01, conf), 2), f"BB normal — BW={bb['bandwidth']:.4f}")

    def warm_up_bars_needed(self):
        return 30
