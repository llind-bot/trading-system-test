"""Crypto engine — standalone strategy evaluation for crypto assets.

24/7 operation (no market hours check). Same structure as stock_engine.

Run: python3 engine/crypto_engine.py
"""
import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path

os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from strategies.crypto_swing_daily import CryptoSwingDaily
from infra.db_pool import get_db
from infra.logger import get_logger
from infra.notify_engine import get_notify

_log = get_logger("crypto-engine-test")
_notify = get_notify()


class CryptoEngine:
    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self._running = False
        self.bars_db = get_db("trading")
        self.signals_db = get_db("trades")
        self.watchlist: dict[str, dict] = {}
        self.signal_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    async def _load_config(self):
        """Load watchlist from config."""
        import yaml
        wl_path = TRADE_ROOT / "config" / "watchlist.yaml"
        with open(wl_path) as f:
            data = yaml.safe_load(f) or {}
        
        for asset in data.get("assets", []):
            if (asset.get("asset_class") or "").lower() != "crypto":
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
        """One evaluation cycle for all crypto symbols."""
        new_bars = self._fetch_new_bars()
        
        for symbol in self.watchlist.keys():
            bars = new_bars.get(symbol, [])
            if not bars:
                continue
            
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
        """Fetch crypto bar data from DB pool."""
        conn = self.bars_db.connect()
        result = {}
        try:
            for symbol in self.watchlist.keys():
                sym_upper = symbol.upper().replace("/", "_")
                rows = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM bars_crypto "
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
        
        self.bars_db.checkpoint()
        return result

    def _write_signal_to_db(self, symbol: str, signal_result: dict):
        """Write signal to DB."""
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
                "crypto-engine-test",
            ))
            conn.commit()
        finally:
            conn.close()

    async def run(self):
        """Main event loop — runs 24/7."""
        self._running = True
        await self._load_config()
        
        _log.info("engine_start", watchlist=list(self.watchlist.keys()))
        
        while self._running:
            try:
                await self._tick()
            except Exception:
                err_msg = traceback.format_exc()
                _log.error("tick_error", detail=err_msg)
                _notify.notify("engine_tick_error", f"Crypto engine tick error:\n{err_msg}", severity="WARNING")
                self._running = False
        
        _log.info("engine_stopped")
    
    def stop(self):
        self._running = False


async def main():
    engine = CryptoEngine(poll_interval=30)
    
    def _sh(sig, frame):
        _log.info("shutdown", signal=sig)
        engine.stop()
    
    signal.signal(signal.SIGINT, _sh)
    signal.signal(signal.SIGTERM, _sh)
    
    try:
        await engine.run()
    except Exception as e:
        _log.error("fatal_error", detail=str(e))
    finally:
        engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
