"""RSI Pullback buy strategy — fires on RSI(14) dip during an uptrend.

Designed for scalping: entries happen when price briefly dips in a larger upward move.
Unlike pure mean-reversion (which buys oversold regardless of trend), this requires
the broader trend to be positive before allowing the pullback entry.

Usage:
  Strategy group "pullback_1" evaluates this on every completed bar.
  
Trigger conditions:
  1. RSI(14) < 35 (oversold zone — normal dip, not panic)
  2. Price above SMA(20) (trend is still up)
  3. Price within 0.5-2% of recent 20-period low (pullback to support)

Parameters:
  rsi_period: RSI calculation period (default 14)
  sma_period: Trend filter SMA period (default 20)
  rsi_threshold: RSI level to trigger (default 40)
  price_from_low_pct: Max % above recent low for valid pullback (default 2.0)

Confidence:
  - 0.8 if all conditions met + price just bounced from support
  - 0.6 if RSI threshold met but no bounce yet
  - 0.0 if trend condition fails
"""

from dataclasses import dataclass
from typing import List, Optional

from src.strategies.base import BaseStrategy, Signal, StrategyResult




class RSI_Pullback:
    """Buy on RSI(14) pullback during uptrend."""

    def __init__(self):
        self.name = "RSI_Pullback"
    
    @staticmethod
    def _rsi(prices: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI(14)."""
        if len(prices) < period + 1:
            return None
        
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def _sma(prices: List[float], period: int = 20) -> Optional[float]:
        """Calculate SMA."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period
    
    def evaluate(self, bars: List[dict], params: dict = None) -> StrategyResult:
        """Evaluate bars for pullback entry signal."""
        if not bars or len(bars) < 25:
            return StrategyResult(Signal.HOLD, 0.0, f"Insufficient data ({len(bars)} bars)")
        
        period = params.get('rsi_period', 14) if params else 14
        sma_period = params.get('sma_period', 20) if params else 20
        rsi_threshold = params.get('rsi_threshold', 40) if params else 40
        price_from_low_pct = params.get('price_from_low_pct', 2.0) if params else 2.0
        
        closes = [bar['close'] for bar in bars]
        
        # Condition 1: RSI(14) < threshold (oversold zone)
        rsi_val = self._rsi(closes, period)
        if rsi_val is None or rsi_val >= rsi_threshold:
            return StrategyResult(Signal.HOLD, 0.0, f"RSI({period})={rsi_val:.1f} (threshold {rsi_threshold})")
        
        # Condition 2: Price above SMA(20) — broader uptrend intact
        sma_val = self._sma(closes, sma_period)
        if sma_val is not None and closes[-1] <= sma_val:
            return StrategyResult(Signal.HOLD, 0.3, f"Price ${closes[-1]:.4f} below SMA${sma_val:.4f}")
        
        # Condition 2b: If no SMA data yet (less than 20 bars), use trend filter:
        # Last 5 bars avg close > first 5 bars avg close = uptrend
        if sma_val is None and len(closes) >= 10:
            recent_avg = sum(closes[-5:]) / 5
            early_avg = sum(closes[:5]) / 5
            if recent_avg <= early_avg:
                return StrategyResult(Signal.HOLD, 0.3, "No uptrend (trend filter failed)")
        
        # Condition 3: Price near recent low (pullback to support)
        lookback = min(20, len(closes))
        recent_low = min(closes[-lookback:])
        current_price = closes[-1]
        pct_from_low = ((current_price - recent_low) / recent_low) * 100
        
        if pct_from_low > price_from_low_pct:
            return StrategyResult(Signal.HOLD, 0.3, f"Price {pct_from_low:.2f}% from low (too far)")
        
        # All conditions met — buy signal!
        confidence = 0.8 if pct_from_low < 1.0 else 0.6
        
        return StrategyResult(
            signal=Signal.BUY,
            confidence=confidence,
            reason=f"RSI={rsi_val:.1f} | Price ${current_price:.4f} above SMA${sma_val if sma_val else 'N/A'} | {pct_from_low:.2f}% from support"
        )
