"""Strategy: Crypto Swing Mean-Reversion for BTC/USD and volatile crypto assets.

Buys at the lows and sells at the highs by combining RSI extremes on 4h bars
with Bollinger Band penetration — confirming price is truly stretched beyond
normal range before entering a mean-reversion trade.

The key insight from analysis: BTC whipsaws constantly on low TFs, but on 4h
real extreme events are rare and have reliable follow-through. This strategy
targets those moments.

Logic:
- BUY when RSI < oversold_threshold AND price is below lower Bollinger Band
- SELL when RSI > overbought_threshold AND price is above upper Bollinger Band
- TP targets are ATR-based (adaptive since BTC's daily range 3-10% varies wildly)
- Trend filter optional: avoids buying into a strong confirmed downtrend
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.rsi import rsi
import numpy as np


class CryptoSwingReversion(BaseStrategy):
    NAME = "CryptoSwingReversion"
    DESCRIPTION = "RSI + Bollinger Band swing mean-reversion for BTC/USD volatile assets"
    
    DEFAULT_PARAMS = {
        "rsi_period": 14,
        "oversold_threshold": 15,
        "overbought_threshold": 80,
        "bb_period": 30,
        "bb_multiplier": 2.5,
        "atr_period": 20,
        "trend_sma_period": 100,
        "min_rsi_stretch": 15,
    }

    def warm_up_bars_needed(self):
        return self.DEFAULT_PARAMS["bb_period"] + self.DEFAULT_PARAMS["rsi_period"] + \
               self.DEFAULT_PARAMS["atr_period"] + 10

    def _calc_bollinger_bands(self, closes, period, mult):
        """Calculate Bollinger Bands from close prices."""
        arr = np.array(closes, dtype=float)
        sma = np.full(len(arr), np.nan)
        std = np.full(len(arr), np.nan)
        
        for i in range(period - 1, len(arr)):
            window = arr[i - period + 1:i + 1]
            sma[i] = np.mean(window)
            std[i] = np.std(window, ddof=1) if np.std(window, ddof=1) != 0 else 1e-6
        
        upper = np.where(~np.isnan(sma), sma + mult * std, np.nan)
        lower = np.where(~np.isnan(sma), sma - mult * std, np.nan)
        return sma, upper, lower

    def _calc_atr(self, highs, lows, closes, period):
        """Calculate ATR from high/low/close."""
        tr = []
        for i in range(len(highs)):
            if i == 0:
                tr.append(highs[i] - lows[i])
            else:
                tr.append(max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                ))
        atr = np.full(len(tr), np.nan)
        for i in range(period - 1, len(tr)):
            window = tr[i - period + 1:i + 1]
            atr[i] = np.mean(window)
        return atr

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars for RSI + BB")

        p = {**self.DEFAULT_PARAMS, **(params or {})}
        
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        
        # RSI
        rsi_val = rsi(closes, p["rsi_period"])
        if rsi_val < 0 or np.isnan(rsi_val):
            return StrategyResult(Signal.HOLD, 0.0, "RSI calculation incomplete")
        
        # Bollinger Bands
        sma, upper_bb, lower_bb = self._calc_bollinger_bands(closes, p["bb_period"], p["bb_multiplier"])
        
        # ATR
        atr = self._calc_atr(highs, lows, closes, p["atr_period"])
        
        current_price = closes[-1]
        rsi_label = f"RSI({p['rsi_period']})"
        
        if np.isnan(upper_bb[-1]) or np.isnan(lower_bb[-1]):
            return StrategyResult(Signal.HOLD, 0.0, "Bollinger Bands not ready")
        
        # Price below lower band = stretched to the bottom
        price_below_lower = current_price < lower_bb[-1]
        # Price above upper band = stretched to the top  
        price_above_upper = current_price > upper_bb[-1]
        
        current_atr = float(atr[-1]) if not np.isnan(atr[-1]) else 0
        
        # ── BUY: RSI oversold AND price below lower BB ──
        if rsi_val <= p["oversold_threshold"] and price_below_lower:
            # Trend filter
            trend_conf = 1.0
            if p["trend_sma_period"] > 0 and len(closes) > p["trend_sma_period"]:
                recent_ma = np.mean(closes[-p["trend_sma_period"]:])
                if current_price < recent_ma:
                    trend_conf = 0.6  # Bearish — still valid entry but lower confidence
            
            # RSI depth determines strength (how extreme)
            rsi_depth = (p["oversold_threshold"] - rsi_val) / p["oversold_threshold"] * 100
            
            if rsi_depth < p["min_rsi_stretch"]:
                return StrategyResult(Signal.HOLD, 0.0, f"RSI stretch {rsi_depth:.1f}% below threshold")
            
            conf = min(0.95, 0.6 + (rsi_depth / 100) * 0.3 * trend_conf)
            
            # Check if price is actually bouncing (not crashing further)
            momentum_ok = True
            if len(closes) >= 4:
                recent = closes[-4:]
                if all(recent[i+1] < recent[i] for i in range(len(recent)-1)):
                    conf -= 0.1  # Still falling — less confidence
            
            atr_tp_str = f" (ATR: {current_atr:.2f})" if current_atr > 0 else ""
            
            return StrategyResult(
                Signal.BUY, round(conf, 2),
                f"{rsi_label}={rsi_val:.1f} oversold + BB penetration{atr_tp_str}",
                entry_price=current_price,
            )
        
        # ── SELL: RSI overbought AND price above upper BB ──
        elif rsi_val >= p["overbought_threshold"] and price_above_upper:
            trend_conf = 1.0
            if p["trend_sma_period"] > 0 and len(closes) > p["trend_sma_period"]:
                recent_ma = np.mean(closes[-p["trend_sma_period"]:])
                if current_price > recent_ma:
                    trend_conf = 0.6
            
            rsi_depth = (rsi_val - p["overbought_threshold"]) / (100 - p["overbought_threshold"]) * 100
            
            if rsi_depth < p["min_rsi_stretch"]:
                return StrategyResult(Signal.HOLD, 0.0, f"RSI stretch {rsi_depth:.1f}% below threshold")
            
            conf = min(0.95, 0.6 + (rsi_depth / 100) * 0.3 * trend_conf)
            
            return StrategyResult(
                Signal.SELL, round(conf, 2),
                f"{rsi_label}={rsi_val:.1f} overbought + BB penetration (ATR: {current_atr:.2f})",
                entry_price=current_price,
            )
        
        return StrategyResult(Signal.HOLD, 0.0, f"RSI={rsi_val:.1f} normal range")
