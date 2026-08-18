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
from datetime import datetime, time as dt_time
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
        self.bars_db = get_db("trading")
        self.signals_db = get_db("trades")
        self.watchlist: dict[str, dict] = {}
        self.signal_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.last_eval_times: dict[str, str] = {}

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
                    strategy = CryptoSwingDaily(symbol=symbol)
                    signal_result = strategy.evaluate(bars, asset_cfg.get("strategy_params", {}))
                    
                    if signal_result and signal_result.get("side"):
                        self._write_signal_to_db(symbol, signal_result)
                except Exception as e:
                    _log.error("strategy_eval_error", symbol=symbol, strategy=strat_name, error=str(e))

    def _fetch_new_bars(self) -> dict[str, list[dict]]:
        """Fetch bar data from the DB pool."""
        conn = self.bars_db.connect()
        result = {}
        try:
            for symbol in self.watchlist.keys():
                sym_upper = symbol.upper()
                rows = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM bars_stock "
                    "WHERE symbol = ? AND timeframe='1m' ORDER BY timestamp ASC",
                    (sym_upper,),
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
                INSERT INTO engine_signals (timestamp, symbol, side, strategy, confidence, engine)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                symbol.upper(),
                signal_result["side"],
                signal_result.get("strategy", "crypto_swing_daily"),
                signal_result.get("confidence", 0.0),
                "stock-engine-test",
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
