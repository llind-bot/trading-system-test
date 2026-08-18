"""Strategy 3: Opening Range Breakout (ORB).

Signal: Price breaks above high of first N bars → BUY
        Price breaks below low of first N bars → SELL
Configurable: orb_bars — first N bars for range calculation
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult


class OR_Breakout(BaseStrategy):
    NAME = "ORB"
    DESCRIPTION = "Opening Range Breakout — enters on breakout of initial range"
    DEFAULT_PARAMS = {
        "orb_bars": 4,       # first 4 bars (for 5m → first 20 min)
        "volume_mult": 1.3,  # volume confirmation threshold
        "confirm_pct": 0.01,  # price must exceed range by this % (not just touch)
    }

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        if len(bars) <= p["orb_bars"]:
            return StrategyResult(Signal.HOLD, 0.0, "Range not yet established")

        # Calculate range from first N bars
        range_bars = bars[:p["orb_bars"]]
        high_range = max(b["high"] for b in range_bars)
        low_range = min(b["low"] for b in range_bars)

        current_price = bars[-1]["close"]
        current_vol = bars[-1].get("volume", 0)
        avg_vol = sum(b.get("volume", 0) for b in range_bars) / len(range_bars)

        # Breakout above range high (with confirmation gap)
        if current_price >= high_range * (1 + p["confirm_pct"]):
            vol_ok = current_vol >= avg_vol * p["volume_mult"]
            conf = 0.85 if vol_ok else 0.6
            return StrategyResult(
                Signal.BUY, conf,
                f"Breakout above range high ${high_range:.2f} (+{p['confirm_pct']*100:.1f}% confirm, range: {p['orb_bars']} bars)",
                entry_price=current_price,
            )

        # Breakdown below range low (with confirmation gap)
        if current_price <= low_range * (1 - p["confirm_pct"]):
            vol_ok = current_vol >= avg_vol * p["volume_mult"]
            conf = 0.85 if vol_ok else 0.6
            return StrategyResult(
                Signal.SELL, conf,
                f"Breakdown below range low ${low_range:.2f} (-{p['confirm_pct']*100:.1f}% confirm)",
                stop_loss_price=current_price * 1.02,
            )

        return StrategyResult(Signal.HOLD, 0.0, f"In range (${low_range:.2f} - ${high_range:.2f})")

    def warm_up_bars_needed(self):
        return 5
