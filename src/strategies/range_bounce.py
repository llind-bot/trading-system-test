"""Strategy: Range Bounce

Mean-reversion strategy designed for BTC-style range-bound markets.
Detects established support/resistance zones and trades bounces within them,
rather than waiting for extreme RSI events like CryptoSwingReversion does.

Buys near confirmed support on oversold confirmation.
Sells near confirmed resistance on overbought confirmation.
Uses a longer-term SMA trend filter to avoid fighting momentum.

Multi-level approach:
- Short-range support/resistance (20 bars) for quick bounces
- Mid-range (50 bars) for medium swings  
- Full-range (100 bars) for deep support/resistance (captures $60K zones)
- Wider lookback captures more history → deeper, more reliable support levels

Configurable:
  range_lookback_short:  Short-term support/resistance bars (default 20)
  range_lookback_mid:    Mid-term support/resistance bars (default 50)
  range_lookback_full:   Full-range support/resistance bars (default 100)
  trend_sma_period:      Longer-term SMA for trend direction (default 50)
  oversold_threshold:    RSI level to trigger buy near support (default 35)
  overbought_threshold:  RSI level to trigger sell near resistance (default 65)
  min_range_pct:         Minimum % range size to consider tradeable (default 1.0)
  max_range_pct:         Maximum % range size — wider = trending, not ranging (default 30.0)
  entry_buffer_short:    Proximity to SHORT support/resistance (default 1.5%)
  entry_buffer_mid:      Proximity to MID support/resistance (default 2.0%)
  entry_buffer_full:     Proximity to FULL support/resistance (default 3.0%)
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.rsi import rsi
import numpy as np


class Range_Bounce(BaseStrategy):
    NAME = "Range_Bounce"
    DESCRIPTION = "Multi-level support/resistance mean-reversion for BTC-style markets"

    DEFAULT_PARAMS = {
        # Three range detection windows
        "range_lookback_short": 20,
        "range_lookback_mid": 50, 
        "range_lookback_full": 100,
        
        # Trend filter
        "trend_sma_period": 50,
        
        # RSI thresholds (standard levels)
        "rsi_period": 14,
        "oversold_threshold": 50,     # buy when RSI drops below this
        "overbought_threshold": 50,   # sell when RSI rises above this
        
        # Range filters
        "min_range_pct": 1.0,
        "max_range_pct": 30.0,
        
        # Entry buffers (tighter for shorter lookbacks)
        "entry_buffer_short": 2.0,    # 2% of short support level
        "entry_buffer_mid": 3.0,      # 3% of mid support level  
        "entry_buffer_full": 7.0,     # 7% of full support level (optimal for BTC)
        
        # Trade management
        "consecutive_drop_bars": 3,   # require momentum confirmation
        "sell_on_gain_pct": 2.5,      # take profit at +2.5%
        "max_hold_bars": 12,          # max 48 hours in trade
        "stop_loss_pct": -1.0,        # tight stop loss
    }

    def warm_up_bars_needed(self):
        return max(
            self.DEFAULT_PARAMS["range_lookback_full"],
            self.DEFAULT_PARAMS["trend_sma_period"],
            self.DEFAULT_PARAMS["rsi_period"],
        ) + 5

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars")

        p = {**self.DEFAULT_PARAMS, **(params or {})}

        closes = np.array([b["close"] for b in bars], dtype=float)
        highs = np.array([b["high"] for b in bars], dtype=float)
        lows = np.array([b["low"] for b in bars], dtype=float)

        current_close = float(closes[-1])
        current_high = float(highs[-1])
        current_low = float(lows[-1])
        prev_close = float(closes[-2])

        # ── RSI (computed once, used by BUY and SELL checks) ──
        rsi_val = rsi(closes.tolist(), p["rsi_period"])
        if rsi_val < 0 or np.isnan(rsi_val):
            return StrategyResult(Signal.HOLD, 0.0, "RSI incomplete")

        # ── Trend filter: SMA over longer window ──
        trend_period = p["trend_sma_period"]
        if len(closes) >= trend_period:
            trend_sma = float(np.mean(closes[-trend_period:]))
        else:
            trend_sma = current_close  # insufficient data — neutral

        uptrend = trend_sma < current_close
        downtrend = trend_sma > current_close

        # ── Three support/resistance levels (multi-level detection) ──
        lookbacks = {
            "short": p["range_lookback_short"],
            "mid": p["range_lookback_mid"], 
            "full": p["range_lookback_full"]
        }
        
        ranges = {}
        for name, lookback in lookbacks.items():
            if len(closes) >= lookback:
                recent_highs = highs[-lookback:]
                recent_lows = lows[-lookback:]
                resistance = float(np.max(recent_highs))
                support = float(np.min(recent_lows))
                range_width_pct = (resistance - support) / support * 100 if support > 0 else 0
                
                # Skip if too wide (trending) or too narrow (dead zone)
                if p["min_range_pct"] <= range_width_pct <= p["max_range_pct"]:
                    ranges[name] = {
                        "support": support,
                        "resistance": resistance, 
                        "range_pct": range_width_pct
                    }

        # ── BUY: Multi-level support detection ──
        if uptrend:
            for level_name in ["short", "mid", "full"]:
                if level_name not in ranges:
                    continue
                    
                level_info = ranges[level_name]
                buffer_key = f"entry_buffer_{level_name}"
                entry_buffer = p[buffer_key]
                
                support = level_info["support"]
                proximity_pct = (current_close - support) / support * 100
                
                # Check if within buffer zone of this level's support
                threshold = support * (1 + entry_buffer / 100)
                if current_low <= threshold:
                    # RSI confirmation needed
                    rsi_val = rsi(closes.tolist(), p["rsi_period"])
                    if rsi_val < 0 or np.isnan(rsi_val):
                        continue
                        
                    if rsi_val <= p["oversold_threshold"]:
                        # Candle rejection of lows
                        body = abs(current_close - prev_close) if prev_close > 0 else 0.01
                        lower_shadow = min(current_low, current_close, prev_close) - min(current_close, prev_close)
                        green_candle_after_red = (current_close > prev_close) and (prev_close < closes[-3] if len(closes) >= 3 else True)

                        shadow_confidence = lower_shadow > body * 0.5 if body > 0 else False
                        momentum_ok = shadow_confidence or green_candle_after_red

                        # Confidence based on level and proximity
                        level_weight = {"short": 0.9, "mid": 0.75, "full": 0.6}[level_name]
                        conf = min(0.95, 0.4 + level_weight * 0.3)
                        if not momentum_ok:
                            conf *= 0.8

                        return StrategyResult(
                            Signal.BUY,
                            round(conf, 2),
                            f"Support bounce ({level_name}): range {support:.0f}-{level_info['resistance']:.0f} | RSI({p['rsi_period']})={rsi_val:.1f} oversold + trend OK",
                            entry_price=current_close,
                        )

        # ── SELL: Multi-level resistance detection ──
        if downtrend:
            for level_name in ["short", "mid", "full"]:
                if level_name not in ranges:
                    continue
                    
                level_info = ranges[level_name]
                buffer_key = f"entry_buffer_{level_name}"
                entry_buffer = p[buffer_key]
                
                resistance = level_info["resistance"]
                
                # Check if within buffer zone of this level's resistance
                threshold = resistance * (1 - entry_buffer / 100)
                if current_high >= threshold:
                    # RSI confirmation needed  
                    rsi_val = rsi(closes.tolist(), p["rsi_period"])
                    if rsi_val < 0 or np.isnan(rsi_val):
                        continue
                        
                    if rsi_val >= p["overbought_threshold"]:
                        body = abs(current_close - prev_close) if prev_close > 0 else 0.01
                        upper_shadow = max(current_high, current_close, prev_close) - max(current_close, prev_close)
                        red_candle_after_green = (current_close < prev_close) and (prev_close > closes[-3] if len(closes) >= 3 else True)

                        shadow_confidence = upper_shadow > body * 0.5 if body > 0 else False
                        momentum_ok = shadow_confidence or red_candle_after_green

                        level_weight = {"short": 0.9, "mid": 0.75, "full": 0.6}[level_name]
                        conf = min(0.95, 0.4 + level_weight * 0.3)
                        if not momentum_ok:
                            conf *= 0.8

                        return StrategyResult(
                            Signal.SELL,
                            round(conf, 2),
                            f"Resistance rejection ({level_name}): range {ranges[level_name]['support']:.0f}-{resistance:.0f} | RSI({p['rsi_period']})={rsi_val:.1f} overbought + trend OK",
                            entry_price=current_close,
                        )

        # No signal — price not at any support/resistance level or wrong direction
        pct_in_range = (current_close - current_low) / (current_high - current_low) * 100 if current_high > current_low else 50
        return StrategyResult(Signal.HOLD, 0.0, f"In-range: RSI={rsi_val:.1f} | {pct_in_range:.0f}% of range")

