"""Order server — reads signals from DB, routes to Alpaca API, writes results.

Minimal logic: signal polling → place order → update status → handle errors.
Same structure as other engines for consistency.

Run: python3 engine/order_server.py
"""
import asyncio
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from strategies.tp_ladder import TPLadderEngine
from infra.db_pool import get_db
from infra.logger import get_logger
from infra.notify_engine import get_notify

_log = get_logger("order-server-test")
_notify = get_notify()


class OrderServer:
    def __init__(self, poll_interval: int = 10):
        self.poll_interval = poll_interval
        self._running = False
        self.signals_db = get_db("trades")
        self.tp_engine = TPLadderEngine()

    async def _process_signals(self):
        """Check for pending signals and route them to Alpaca."""
        conn = self.signals_db.connect()
        try:
            # Get all engine_signals with status='pending' that haven't been processed
            rows = conn.execute("""
                SELECT id, symbol, side, strategy, confidence 
                FROM engine_signals 
                WHERE status = 'pending' AND engine LIKE '%test%'
                ORDER BY timestamp ASC
            """).fetchall()
            
            for row in rows:
                sig_id = row[0]
                symbol = row[1]
                side = row[2]
                
                _log.info("processing_signal", signal_id=sig_id, symbol=symbol, side=side)
                
                # Route through order router (will be implemented in Phase 3)
                # For now: log the signal and mark as processed for scaffolding validation
                conn.execute(
                    "UPDATE engine_signals SET status = 'processed', processed_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), sig_id),
                )
                conn.commit()
                
        finally:
            conn.close()
        
        self.signals_db.checkpoint()

    async def run(self):
        """Main event loop."""
        self._running = True
        _log.info("order_server_start")
        
        while self._running:
            try:
                await self._process_signals()
            except Exception as e:
                _log.error("signal_processing_error", detail=str(e))
                _notify.notify("order_server_error", f"Order server error:\n{str(e)}", severity="WARNING")
                self._running = False
            
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False


async def main():
    server = OrderServer(poll_interval=10)
    
    def _sh(sig, frame):
        _log.info("shutdown", signal=sig)
        server.stop()
    
    signal.signal(signal.SIGINT, _sh)
    signal.signal(signal.SIGTERM, _sh)
    
    try:
        await server.run()
    except Exception as e:
        _log.error("fatal_error", detail=str(e))
    finally:
        server.stop()


if __name__ == "__main__":
    asyncio.run(main())
