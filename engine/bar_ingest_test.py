"""Bar ingest — fetches bar data from Alpaca (WebSocket + REST) and writes to DB.

Run: python3 engine/bar_ingest_test.py
"""
import asyncio
import os
import signal
import sys
from pathlib import Path

os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from infra.db_pool import get_db
from infra.logger import get_logger

_log = get_logger("bar-ingest-test")


class BarIngest:
    def __init__(self, poll_interval: int = 10):
        self.poll_interval = poll_interval
        self._running = False
        self.bars_db = get_db("trading")

    async def run(self):
        """Main event loop — fetches bars and writes to DB."""
        self._running = True
        _log.info("bar_ingest_start")
        
        while self._running:
            try:
                # TODO: Implement Alpaca WS feed connection in Phase 3
                # For now: validate that the DB pool is working
                conn = self.bars_db.connect()
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                _log.info("db_health", tables=[t[0] for t in tables])
                
            except Exception as e:
                _log.error("bar_ingest_error", detail=str(e))
            
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False


async def main():
    ingest = BarIngest(poll_interval=10)
    
    def _sh(sig, frame):
        _log.info("shutdown", signal=sig)
        ingest.stop()
    
    signal.signal(signal.SIGINT, _sh)
    signal.signal(signal.SIGTERM, _sh)
    
    try:
        await ingest.run()
    except Exception as e:
        _log.error("fatal_error", detail=str(e))
    finally:
        ingest.stop()


if __name__ == "__main__":
    asyncio.run(main())
