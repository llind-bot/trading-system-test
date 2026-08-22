"""Strategy: Crypto Swing Daily — RSI + Bollinger Band mean-reversion for daily swings.

Targets 2-3% daily gains on ETH/USD and BTC/USD using BB lower/upper band penetration
confirmed by RSI extremes. Phase 1+2 analysis validates this edge:

Phase 1 findings (from 365 days of daily bars):
- ETH/USD lower BB penetrations occur ~48 times/year with 94% bounce rate over 5d
- BTC/USD lower BB penetrations occur ~55 times/year with 85% bounce rate over 5d
- Average peak gain after penetration: 10-12%, so 3% TP target is conservative
- 85% of events hit +3% within 5 bars

Phase 2 findings (corrected backtest):
- ETH/USD: 90% win rate, +24% net return over 1 year (10 trades)
- BTC/USD: 79% win rate, +26% net return over 1 year (14 trades)

Strategy design:
- BUY when daily low penetrates lower BB AND RSI < threshold -> enter at penetration low
- SELL/Fade when daily high penetrates upper BB AND RSI > threshold -> enter at penetration high
- TP: ATR-based targeting 2-3% gains
- SL: fixed 2% stop loss (ATR would be too wide on crypto daily bars)

All strategy parameters are configurable via the asset's watchlist strategy_params.
Defaults remain in DEFAULT_PARAMS for when an asset is selected but doesn't override.
"""

from src.strategies.base import BaseStrategy, Signal, StrategyResult
from src.strategies.indicators.rsi import rsi as calc_rsi
from src.strategies.indicators.bollinger import bollinger_bands as calc_bb


class CryptoSwingDaily(BaseStrategy):
    NAME = "CryptoSwingDaily"
    DESCRIPTION = (
        "Daily timeframe swing strategy targeting 2-3% gains on crypto. "
        "BB lower/upper band penetration + RSI extremes, enter at penetration low/high."
    )

    DEFAULT_PARAMS = {
        # ── RSI settings ───────────────────────────────────────────────
        "rsi_period": 14,
        "oversold_threshold": 35,       # BUY when RSI drops below this (loosened from 28 to capture more mean-reversion entries)
        "overbought_threshold": 90,     # SELL/Fade when RSI rises above this (raised from 75 to avoid premature fades on volatile crypto)

        # ── Bollinger Bands ────────────────────────────────────────────
        "bb_period": 20,                # ~1 trading month on daily bars
        "bb_multiplier": 2.0,           # Standard 2-sigma -- gives regular penetration events

        # ── Trend filter (optional) ────────────────────────────────────
        "trend_sma_period": 50,         # SMA of closes for trend context
        "use_trend_filter": True,       # Enabled: don't fade-sell during strong uptrends

        # ── Profit targets ─────────────────────────────────────────────
        "tp_fixed_pct": 2.5,            # Fixed take profit percentage (default)
        "tp_atr_mult": 1.5,             # ATR-based TP multiplier
        "min_tp_pct": 2.0,              # Floor on TP %
        "max_tp_pct": 3.0,              # Cap on TP %

        # ── Stop loss ──────────────────────────────────────────────────
                "sl_fixed_pct": 2.0,            # Buy-side stop loss (tight -- down moves are violent)
        "min_penetration_depth_pct": 0.05,  # Minimum % below/above BB to qualify (loosened from 0.1 to catch shallower touches)

        # ── ATR ────────────────────────────────────────────────────────
        "atr_period": 14,                  # ATR calculation period

        # ── Confidence scoring ─────────────────────────────────────────
        "base_confidence": 0.7,            # Base confidence on signal trigger (Phase 2 data)
        "rsi_depth_conf_mult": 0.002,      # Confidence boost per % RSI depth from threshold
        "penetration_conf_threshold": 0.3,  # Penetration % at which confidence bonus applies
        "penetration_conf_bonus": 0.05,     # Confidence boost amount for deep penetration
        "confidence_cap": 0.95,            # Maximum confidence value

        # ── Sell-side specific (wider SL for fade entries) ─────────────
        "sl_sell_pct": 3.0,             # SELL/Fade stop loss % -- wider to avoid whipsaw on upward runs

        # ── Trend filter thresholds ────────────────────────────────────
        "trend_filter_buy_threshold": -3.0,  # Counter-trend buy avoidance threshold (% vs SMA)
        "trend_filter_sell_threshold": 3.0,   # Don't sell fade if price > SMA +3% (strong uptrend)

        # ── Trade management ───────────────────────────────────────────
        "max_hold_bars": 5,             # Exit if TP/SL not hit within N bars (safety)
    }

    def warm_up_bars_needed(self) -> int:
        return max(
            self.DEFAULT_PARAMS["bb_period"],
            self.DEFAULT_PARAMS["rsi_period"],
            self.DEFAULT_PARAMS.get("trend_sma_period", 0),
        ) + 5

    def _calc_tp(self, current_atr_pct: float) -> float:
        """Calculate take profit percentage using ATR-based approach."""
        p = self.DEFAULT_PARAMS
        atr_tp = p["tp_atr_mult"] * current_atr_pct
        return max(p["min_tp_pct"], min(p["max_tp_pct"], atr_tp))

    def evaluate(self, bars: list[dict], params: dict = None) -> StrategyResult:
        if len(bars) < self.warm_up_bars_needed():
            return StrategyResult(Signal.HOLD, 0.0, "Insufficient bars for warm-up")

        p = {**self.DEFAULT_PARAMS, **(params or {})}

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]
        prev_close = closes[-2] if len(closes) >= 2 else current_close

        # ── RSI ──────────────────────────────────────────────────────────
        rsi_val = calc_rsi(closes, p["rsi_period"])
        if rsi_val < 0 or rsi_val > 100:
            return StrategyResult(Signal.HOLD, 0.0, "RSI incomplete")

        # ── Bollinger Bands ──────────────────────────────────────────────
        bb = calc_bb(closes, p["bb_period"], p["bb_multiplier"])
        if bb is None:
            return StrategyResult(Signal.HOLD, 0.0, "Bollinger Bands incomplete")

        # ── ATR ──────────────────────────────────────────────────────────
        tr_values = []
        for i in range(1, len(highs)):
            tr_val = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_values.append(tr_val)

        if len(tr_values) >= p["atr_period"]:
            current_atr = sum(tr_values[-p["atr_period"]:]) / p["atr_period"]
        else:
            current_atr = (highs[-1] - lows[-1]) if highs[-1] != lows[-1] else current_close * 0.02
        
        atr_pct = (current_atr / current_close) * 100 if current_close > 0 else 0

        # ── Trend filter ─────────────────────────────────────────────────
        trend_sma_period = p.get("trend_sma_period", 0)
        price_vs_trend = None
        if trend_sma_period > 0 and len(closes) >= trend_sma_period:
            trend_sma = sum(closes[-trend_sma_period:]) / trend_sma_period
            price_vs_trend = (current_close - trend_sma) / trend_sma * 100

        # ── BUY: Lower BB penetration + oversold RSI ─────────────────────
        lower_penetration_depth = (bb["lower"] - current_low) / bb["lower"] * 100 if bb["lower"] > 0 else 0
        
        if current_low <= bb["lower"] and rsi_val <= p["oversold_threshold"] and \
           lower_penetration_depth >= p.get("min_penetration_depth_pct", 0.1):

            # Trend filter check
            if p.get("use_trend_filter", False) and price_vs_trend is not None:
                if price_vs_trend < p["trend_filter_buy_threshold"] and rsi_val > 20:
                    return StrategyResult(
                        Signal.HOLD, 0.0,
                        f"Trend filter: {price_vs_trend:+.1f}% vs SMA -- avoid counter-trend buy"
                    )

            # Calculate TP using ATR-based approach (capped at 2-3%)
            tp_pct = self._calc_tp(atr_pct)
            sl_pct = p["sl_fixed_pct"]

            # Entry at penetration low for maximum upside
            entry_price = current_low if current_low < prev_close else min(current_low, closes[-2]) \
                if len(closes) >= 2 else current_low

            conf = p["base_confidence"]  # Base confidence from Phase 2 data (90% ETH / 79% BTC bounce rate)
            
            # Boost confidence for deeper RSI oversold
            rsi_depth = (p["oversold_threshold"] - rsi_val) / p["oversold_threshold"] * 100
            conf += rsi_depth * p["rsi_depth_conf_mult"]
            
            # Boost if penetration is deep (stronger signal)
            if lower_penetration_depth > p["penetration_conf_threshold"]:
                conf += p["penetration_conf_bonus"]

            tp_price = entry_price * (1 + tp_pct / 100)
            sl_price = entry_price * (1 - sl_pct / 100)

            reason = (
                f"BUY: Lower BB ({bb['lower']:.2f}) penetration {lower_penetration_depth:.2f}% deep "
                f"+ RSI({p['rsi_period']})={rsi_val:.1f} oversold | "
                f"TP=+{tp_pct:.1f}% ({tp_price:.2f}) | SL={-sl_pct}% ({sl_price:.2f})"
            )

            return StrategyResult(
                signal=Signal.BUY,
                confidence=round(min(p["confidence_cap"], conf), 2),
                reason=reason,
                entry_price=entry_price,
                stop_loss_price=sl_price,
            )

        # ── SELL/Fade: Upper BB penetration + overbought RSI ────────────
        upper_penetration_depth = (current_high - bb["upper"]) / bb["upper"] * 100 if bb["upper"] > 0 else 0
        
        if current_high >= bb["upper"] and rsi_val >= p["overbought_threshold"] and \
           upper_penetration_depth >= p.get("min_penetration_depth_pct", 0.1):

            # Trend filter (inverse for sell)
            if p.get("use_trend_filter", False) and price_vs_trend is not None:
                if price_vs_trend > p["trend_filter_sell_threshold"] and rsi_val < 80:
                    return StrategyResult(
                        Signal.HOLD, 0.0,
                        f"Trend filter: {price_vs_trend:+.1f}% vs SMA -- avoid counter-trend sell"
                    )

            tp_pct = self._calc_tp(atr_pct)
            sl_pct = p.get("sl_sell_pct", 3.0)  # Sell-side uses wider SL (3% vs buy-side 2%)
            entry_price = current_high if current_high > prev_close else max(current_high, closes[-2]) \
                if len(closes) >= 2 else current_high

            conf = p["base_confidence"]
            rsi_depth = (rsi_val - p["overbought_threshold"]) / (100 - p["overbought_threshold"]) * 100
            conf += rsi_depth * p["rsi_depth_conf_mult"]
            
            if upper_penetration_depth > p["penetration_conf_threshold"]:
                conf += p["penetration_conf_bonus"]

            tp_price = entry_price * (1 - tp_pct / 100)
            sl_price = entry_price * (1 + sl_pct / 100)

            reason = (
                f"SELL: Upper BB ({bb['upper']:.2f}) penetration {upper_penetration_depth:.2f}% deep "
                f"+ RSI({p['rsi_period']})={rsi_val:.1f} overbought | "
                f"TP=-{tp_pct:.1f}% ({tp_price:.2f}) | SL=+{sl_pct}% ({sl_price:.2f})"
            )

            return StrategyResult(
                signal=Signal.SELL,
                confidence=round(min(p["confidence_cap"], conf), 2),
                reason=reason,
                entry_price=entry_price,
                stop_loss_price=sl_price,
            )

        # ── No signal -- price inside bands (score proximity to trigger zones) ───────────────────────────────
        pct_of_bb = ((current_close - bb["lower"]) / (bb["upper"] - bb["lower"])) * 100 \
            if (bb["upper"] - bb["lower"]) != 0 else 50

        # Partial confidence: how close are we to a BUY/SELL trigger?
        conf = 0.0
        reason_parts = [f"In range: RSI={rsi_val:.1f}"]

        # Near lower BB + RSI approaching oversold → buy proximity
        if current_close < bb["middle"]:
            near_bb_pct = max(0, (bb["lower"] - current_close) / bb["lower"] * 100) if bb["lower"] > 0 else 999
            # Use 100 - pct_of_bb for proximity to lower band
            proximity_to_lower = min(100, max(0, (bb["lower"] * (1 + 0.05) - current_close) / (bb["upper"] - bb["lower"]) * 100)) if (bb["upper"] - bb["lower"]) > 0 else 0
            near_rsi = max(0, (p["oversold_threshold"] - rsi_val) / p["oversold_threshold"] * 100)
            conf = min(conf, 0.4 + (min(proximity_to_lower, 50) / 50) * 0.3 + (near_rsi / 100) * 0.2)
            reason_parts.append(f"Near lower BB: {proximity_to_lower:.1f}%")

        # Near upper BB + RSI approaching overbought → sell proximity
        elif current_close >= bb["middle"]:
            proximity_to_upper = min(100, max(0, (current_close - bb["upper"] * 0.95) / (bb["upper"] - bb["lower"]) * 100)) if (bb["upper"] - bb["lower"]) > 0 else 0
            near_rsi = max(0, (rsi_val - p["overbought_threshold"]) / (100 - p["overbought_threshold"]) * 100)
            conf = min(conf, 0.4 + (min(proximity_to_upper, 50) / 50) * 0.3 + (near_rsi / 100) * 0.2)
            reason_parts.append(f"Near upper BB: {proximity_to_upper:.1f}%")

        if conf == 0.0:
            # Neutral position — score based on RSI distance from center
            rsi_centerness = abs(rsi_val - 50) / 50
            conf = min(0.25, rsi_centerness * 0.25)
            reason_parts.append(f"Neutral: {pct_of_bb:.0f}% of BB width")

        return StrategyResult(
            Signal.HOLD, round(max(0.01, conf), 2),
            " | ".join(reason_parts)
        )
