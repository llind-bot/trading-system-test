"""Stock engine — standalone strategy evaluation process for stock assets.

Reads bars from DB, evaluates strategies per symbol, writes signals to DB.
Only runs during NYSE market hours (9:30 AM - 4:00 PM ET on weekdays).
Uses timezone-aware datetime — no string parsing bugs.

Run: python3 engine/stock_engine.py
"""
import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Force paper mode for test environment
os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from strategies.crypto_swing_daily import CryptoSwingDaily  # strategy to evaluate
from infra.db_pool import get_db
from infra.logger import get_logger, StructuredMessage
from infra.notify_engine import get_notify

_log = get_logger("stock-engine-test")
_notify = get_notify()


def _is_stock_market_open() -> bool:
    """Check if NYSE is open. Uses timezone-aware datetime."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    weekday = now.weekday()
    if weekday >= 5:  # Saturday=5, Sunday=6
        return False
    
    hour = now.hour
    minute = now.minute
    second = now.second
    
    # Market open 9:30 AM — before that = closed
    if hour < 9 or (hour == 9 and minute < 30):
        return False
    
    # Market close 4:00 PM — at or after = closed
    if hour > 16:
        return False
    if hour == 16 and minute >= 1:  # Any time past 4:00
        return False
    if hour == 16 and minute == 0 and second >= 1:  # At exactly 4:00:01+
        return False
    
    return True


class StockEngine:
    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self._running = False
        self.bars_db = get_db("bars")
        self.signals_db = get_db("trades")
        self.watchlist: dict[str, dict] = {}
        self.signal_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.last_eval_times: dict[str, str] = {}
        self._risk_config_cache: dict[str, float] = {}  # symbol -> max_position_dollar

    def _get_risk_config(self) -> dict[str, float]:
        """Load risk limits from watchlist.yaml on first call, then cache."""
        if not self._risk_config_cache:
            import yaml
            wl_path = TRADE_ROOT / "config" / "watchlist.yaml"
            if wl_path.exists():
                with open(wl_path) as f:
                    data = yaml.safe_load(f) or {}
                for asset in data.get("assets", []):
                    sym = str(asset.get("symbol", "")).upper()
                    if sym:
                        self._risk_config_cache[sym] = float(
                            asset.get("max_position_dollar", 1000)
                        )
            else:
                pass
        return self._risk_config_cache

    def _get_existing_position_value(self, symbol: str) -> float:
        """Get current position value for a symbol from the positions table."""
        try:
            conn = self.signals_db.connect()
            row = conn.execute(
                "SELECT qty * current_price FROM positions WHERE symbol=? AND qty > 1e-10",
                (symbol.upper(),),
            ).fetchone()
            conn.close()
            if row and row[0]:
                return max(float(row[0]), 0)
        except Exception:
            pass
        return 0.0

    async def _load_config(self):
        """Load watchlist from config (read-only reference)."""
        import yaml
        wl_path = TRADE_ROOT / "config" / "watchlist.yaml"
        with open(wl_path) as f:
            data = yaml.safe_load(f) or {}
        
        for asset in data.get("assets", []):
            if (asset.get("asset_class") or "").lower() != "stock":
                continue
            if not asset.get("enabled", True):
                continue
            sym = asset.get("symbol")
            if not sym:
                continue
            self.watchlist[sym] = {
                "strategies": asset.get("strategies", []),
                "strategy_params": asset.get("strategy_params", {}),
                "max_position_dollar": asset.get("max_position_dollar", 1000),
                "tp_levels": asset.get("tp_levels"),
            }

    async def _tick(self):
        """One evaluation cycle: check market hours, fetch bars, evaluate strategies."""
        if not _is_stock_market_open():
            return  # Silently skip — no warnings (reduces noise)
        
        new_bars = self._fetch_new_bars()
        
        for symbol in self.watchlist.keys():
            bars = new_bars.get(symbol, [])
            if not bars:
                continue
            
            # Evaluate strategies for this symbol
            asset_cfg = self.watchlist[symbol]
            for strat_name in asset_cfg["strategies"]:
                try:
                    strategy = CryptoSwingDaily()
                    signal_result = strategy.evaluate(bars, asset_cfg.get("strategy_params", {}))
                    
                    # Handle both dict-style (legacy) and StrategyResult object returns
                    if signal_result is None:
                        continue
                    if hasattr(signal_result, 'signal'):
                        side = signal_result.signal.name if signal_result.signal else None
                        confidence = getattr(signal_result, 'confidence', 0.0)
                        reason = getattr(signal_result, 'reason', '')
                        strategy_name = getattr(signal_result, 'strategy', strat_name) if hasattr(signal_result, 'strategy') else strat_name
                    else:
                        side = signal_result.get("side")
                        confidence = signal_result.get("confidence", 0.0)
                        reason = signal_result.get("reason", "")
                        strategy_name = signal_result.get("strategy", strat_name)
                    
                    if side and side not in ("HOLD", None):
                        # Include the last bar's close as the signal price (fallback market price)
                        signal_price = bars[-1]["open"] if bars else 0.0
                        
                        # --- Risk check before writing to DB ---
                        if side.upper() == "BUY" and signal_price > 0:
                            risk_cfg = self._get_risk_config()
                            max_pos_dollar = float(
                                asset_cfg.get("max_position_dollar", risk_cfg.get(symbol.upper(), 1000))
                            )
                            existing_value = self._get_existing_position_value(symbol)
                            total_after = existing_value + signal_price * 1.0  # qty=1 default
                            if total_after > max_pos_dollar:
                                _log.warning("position_limit_exceeded", symbol=symbol,
                                    side=side, price=signal_price,
                                    total_after=f"${total_after:,.0f}", limit=max_pos_dollar)
                                # Write rejected signal for audit trail
                                try:
                                    rconn = self.signals_db.connect()
                                    try:
                                        rconn.execute(
                                            "INSERT INTO engine_signals "
                                            "(timestamp, symbol, side, strategy, confidence, status, reason, engine)"
                                            " VALUES (?, ?, ?, ?, ?, 'rejected', ?, ?)",
                                            (
                                                datetime.now(timezone.utc).isoformat(),
                                                symbol.upper(),
                                                side,
                                                strat_name,
                                                confidence,
                                                f"Position cap exceeded: ${total_after:,.0f} > ${max_pos_dollar:,.0f}",
                                                "stock-engine",
                                            ),
                                        )
                                        rconn.commit()
                                    finally:
                                        rconn.close()
                                except Exception as e:
                                    _log.error("rejected_signal_write_error", symbol=symbol, error=str(e))
                                continue
                        
                        self._write_signal_to_db(symbol, {
                            "side": side,
                            "confidence": confidence,
                            "reason": reason,
                            "strategy": strategy_name,
                            "price": signal_price,
                        })
                    
                    # Always log evaluation result to signal_evaluations for dashboard visibility
                    self._log_evaluation(symbol, strategy_name or strat_name, side or "HOLD", confidence, reason)
                    
                    # Also write to engine_signals (for strategy history grid in dashboard)
                    self._write_to_engine_signals(symbol, strategy_name or strat_name, side or "HOLD", confidence, reason)
                except Exception as e:
                    _log.error("strategy_eval_error", symbol=symbol, strategy=strat_name, error=str(e))

    def _write_to_engine_signals(self, symbol: str, strategy_name: str, side: str, confidence: float, reason: str):
        """Write evaluation to engine_signals for dashboard strategy history grid.

        Uses status='eval' so the order_server (which only processes 'pending') won't consume it.
        The dashboard's Evaluation Grid queries status IN ('eval', 'pending').
        """
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO engine_signals (symbol, side, strategy, confidence, status, timestamp)
                VALUES (?, ?, ?, ?, 'eval', ?)
            """, (
                symbol,
                side,
                strategy_name,
                confidence,
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        except Exception as e:
            _log.warning("engine_signals_write_error", symbol=symbol, error=str(e))
        finally:
            conn.close()

    def _log_evaluation(self, symbol: str, strategy_name: str, side: str, confidence: float, reason: str):
        """Write evaluation result to signal_evaluations for dashboard."""
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO signal_evaluations 
                    (symbol, strategy, side, confidence, reason, engine)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                strategy_name,
                side,
                confidence,
                reason[:500] if reason else '',
                'stock-engine',
            ))
            conn.commit()
        except Exception as e:
            _log.warning("eval_write_error", symbol=symbol, error=str(e))
        finally:
            conn.close()

    def _fetch_new_bars(self) -> dict[str, list[dict]]:
        """Fetch bar data from the DB pool."""
        conn = self.bars_db.connect()
        result = {}
        try:
            for symbol in self.watchlist.keys():
                rows = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM bars_stock "
                    "WHERE symbol = ? AND bar_type='240t' ORDER BY timestamp ASC",
                    (symbol,),
                ).fetchall()
                
                if rows:
                    result[symbol] = [{
                        "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                        "close": float(r[4]), "volume": float(r[5]), "timestamp": r[0],
                    } for r in rows]
        finally:
            conn.close()
        
        # Checkpoint after read
        self.bars_db.checkpoint()
        return result

    def _write_signal_to_db(self, symbol: str, signal_result: dict):
        """Write a strategy signal to the trades DB."""
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO engine_signals (symbol, side, strategy, confidence, status, reason, price, engine, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol.upper(),
                signal_result["side"],
                signal_result.get("strategy", "crypto_swing_daily"),
                signal_result.get("confidence", 0.0),
                'pending',
                signal_result.get("reason", ''),
                signal_result.get("price", 0.0),
                'stock-engine',
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    async def run(self):
        """Main event loop."""
        self._running = True
        await self._load_config()
        
        _log.info("engine_start", watchlist=list(self.watchlist.keys()), market_open=_is_stock_market_open())
        
        last_mkt_check_time = 0
        
        while self._running:
            now = datetime.now().timestamp()
            
            # Market hours check (throttled to once per minute)
            if now - last_mkt_check_time > 60:
                market_open = _is_stock_market_open()
                if not market_open:
                    await asyncio.sleep(self.poll_interval)
                    last_mkt_check_time = now
                    continue
            
            try:
                await self._tick()
            except Exception:
                err_msg = traceback.format_exc()
                _log.error("tick_error", detail=err_msg)
                _notify.notify("engine_tick_error", f"Stock engine tick error:\n{err_msg}", severity="WARNING")
                self._running = False  # Exit so watchdog can restart
            
            await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Graceful shutdown."""
        _log.info("engine_stop")
        self._running = False


async def main():
    engine = StockEngine(poll_interval=30)
    
    def _sh(sig, frame):
        _log.info("shutdown", signal=sig)
        engine.stop()
    
    signal.signal(signal.SIGINT, _sh)
    signal.signal(signal.SIGTERM, _sh)
    
    try:
        await engine.run()
    except Exception as e:
        _log.error("fatal_error", detail=str(e))
        traceback.print_exc()
    finally:
        engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
