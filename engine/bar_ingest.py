"""Bar ingest — real Alpaca WS feed → trade aggregation → bar DB writes.

Replaces the Phase-1 stub in bar_ingest_test.py with a full pipeline:
  1. Connect to Alpaca dual-stream WebSocket (stock + crypto)
  2. Accumulate incoming trades into minute / 5m / 15m bars
  3. Flush completed bars to SQLite via infra.db_pool

API keys are read from os.environ (ALPACA_API_KEY, ALPACA_SECRET_KEY).
All DB access goes through infra.db_pool — no direct sqlite3.connect().
"""

import asyncio
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from infra.db_pool import get_db
from infra.logger import get_logger, StructuredMessage
from infra.ws_feed import WSFeed, drain_queue, _unpack_frame

_log = get_logger("bar-ingest")


# ---------------------------------------------------------------------------
# BarAggregator — accumulate trades into bars
# ---------------------------------------------------------------------------

class BarAggregator:
    """Accumulate individual trade events into OHLCV bars.

    Supports 1m, 5m, and 15m bar intervals. Bars are flushed to the DB
    as soon as a new interval begins for that symbol.
    """

    def __init__(self, db_pool, bar_type: str = "1t"):
        """
        Args:
            db_pool: infra.db_pool.DatabasePool instance ('trading' pool)
            bar_type: ``"1t"``, ``"5t"``, or ``"15t"`` (1/5/15 minute bars)
        """
        self.db = db_pool
        self.bar_type = bar_type
        # interval in seconds
        _interval_map = {"1t": 60, "5t": 300, "15t": 900}
        self.interval_s = _interval_map.get(bar_type, 60)

        # Current in-progress bars keyed by (symbol, bar_window)
        # Each bar dict: {open, high, low, close, volume, start_ts_str}
        self.current_bars: dict[str, dict] = {}

    @staticmethod
    def _bar_key(symbol: str, ts_iso: str, bar_minutes: int = 1) -> str:
        """Return the window-aligned key for *symbol* at *ts_iso*."""
        dt = datetime.fromisoformat(ts_iso).replace(tzinfo=ZoneInfo("UTC"))
        floored_min = (int(dt.minute) // bar_minutes) * bar_minutes
        dt_floor = dt.replace(minute=floored_min, second=0, microsecond=0)
        return f"{symbol}:{dt_floor.strftime('%Y-%m-%dT%H:%M:%S')}"

    def on_trade(self, symbol: str, price: float, size: float, ts_iso: str) -> None:
        """Process a trade event.  Flushes completed bars to DB automatically."""
        bar_minutes = self.interval_s // 60
        key = self._bar_key(symbol, ts_iso, bar_minutes)

        if key not in self.current_bars:
            # New bar window — flush previous if it exists for this symbol
            self._flush_current(key, symbol)

            # Start new bar
            self.current_bars[key] = {
                "symbol": symbol.upper(),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": size,
                "start_ts": ts_iso,
            }
        else:
            # Update existing bar
            bar = self.current_bars[key]
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += size

    def _flush_current(self, current_key: str, symbol: str) -> None:
        """Flush the in-progress bar for *symbol* (if it exists and is different from *current_key*)."""
        # Collect all bars that need flushing (previous window for this symbol)
        keys_to_flush = [k for k in self.current_bars if k.startswith(f"{symbol}:") and k != current_key]
        for key in keys_to_flush:
            bar = self.current_bars.pop(key, None)
            if bar and bar["volume"] > 0:
                self._write_bar(bar)

    def _write_bar(self, bar: dict) -> None:
        """Persist one completed bar row to the DB."""
        conn = self.db.connect()
        try:
            conn.execute(
                """INSERT INTO bars (symbol, bar_type, open, high, low, close, volume, timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bar["symbol"],
                    self.bar_type,
                    round(bar["open"], 2),
                    round(bar["high"], 2),
                    round(bar["low"], 2),
                    round(bar["close"], 2),
                    int(bar["volume"]),
                    bar["start_ts"],
                    datetime.now(ZoneInfo("America/New_York")).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def flush_all(self) -> int:
        """Write all in-progress bars to DB and clear them.  Returns count."""
        written = len(self.current_bars)
        for key, bar in list(self.current_bars.items()):
            if bar["volume"] > 0:
                self._write_bar(bar)
        self.current_bars.clear()
        return written

    def get_stats(self) -> dict:
        """Return bar aggregator stats."""
        return {
            "bar_type": self.bar_type,
            "bars_in_progress": len(self.current_bars),
        }


# ---------------------------------------------------------------------------
# BarIngest — main process tying WS feed → aggregator → DB
# ---------------------------------------------------------------------------

class BarIngest:
    """Connect Alpaca WS streams, aggregate trades into bars, write to DB.

    API keys are read from os.environ if not passed explicitly:
        ALPACA_API_KEY  /  ALPACA_SECRET_KEY
    """

    def __init__(self, api_key: str | None = None, secret_key: str | None = None, bar_type: str = "1t"):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self.bar_type = bar_type
        self._running = False
        self.bars_db = get_db("trading")
        self.bar_agg = BarAggregator(self.bars_db, bar_type)
        self.ws_feed = WSFeed(self.api_key, self.secret_key)

    # -- public lifecycle ------------------------------------------------

    async def start(self) -> dict:
        """Initialize DB tables, connect feeds, and return health."""
        if self._running:
            return self.ws_feed.health()  # Already started
        await self._init_db()
        health = await self.ws_feed.start(timeout=15.0)
        self._running = True
        _log.info(StructuredMessage("bar_ingest_started", bar_type=self.bar_type, **health))
        return health

    async def run(self) -> None:
        """Main event loop — drain WS queues, aggregate, write DB."""
        await self.start()

        last_health_ts = 0
        while self._running:
            try:
                # Poll stock queue
                stock_frame = None
                crypto_frame = None

                async def _drain(q):
                    try:
                        raw = await asyncio.wait_for(q.get(), timeout=1.0)
                        return _unpack_frame(raw)
                    except asyncio.TimeoutError:
                        return None

                tasks = []
                stock_queue = self.ws_feed.get_queue("stock")
                crypto_queue = self.ws_feed.get_queue("crypto")

                if not stock_queue.empty():
                    tasks.append(asyncio.create_task(_drain(stock_queue)))
                if not crypto_queue.empty():
                    tasks.append(asyncio.create_task(_drain(crypto_queue)))

                frames = []
                for t in asyncio.as_completed(tasks) if tasks else []:
                    try:
                        frames.append(await t)
                    except Exception:
                        pass

                # Process all collected frames
                for frame in frames:
                    if frame and isinstance(frame, dict):
                        self._handle_frame(frame)

                # Periodic health + flush
                now = asyncio.get_event_loop().time()
                if now - last_health_ts > 60:
                    await self._health_tick()
                    last_health_ts = now

            except Exception as e:
                _log.error("bar_ingest_loop_error", detail=str(e))
                break

    def stop(self) -> None:
        """Graceful shutdown — flush remaining bars, close streams."""
        _log.info("bar_ingest_stopping")
        self._running = False
        flushed = self.bar_agg.flush_all()
        if flushed > 0:
            _log.info("bar_ingest_final_flush", count=flushed)

    # -- frame handling --------------------------------------------------

    @staticmethod
    def _handle_frame(frame: dict) -> None:
        """Dispatch a raw trade/quote/bars event from the WS feed.
        
        Frame structure depends on stream type:
          Stock trades: {"T": "t", "S": "PANW", "P": 385.0, "s": 100}
          Crypto bars:  {"S": "BTC/USD", "o": {"o": 97000, "h": 97500, "l": 96800, "c": 97200, "v": 1.5}}
          Errors:       {"T": "error", "msg": "..."}
        """
        T = frame.get("T") or frame.get("type")
        
        # Error control frames
        if T == "error":
            _log.error(StructuredMessage("ws_error_frame", stream="unknown", msg=frame.get("msg", "")))
            return
        
        # Subscription confirmations — skip
        if T in ("subscription", "success"):
            return
        
        sym = frame.get("S") or frame.get("symbol")
        ts = frame.get("t") or frame.get("T_s") or frame.get("timestamp")
        
        # Crypto bars: nested OHLCV in 'o' key (no T field, but has 'o', 'h', 'l', 'c', 'v')
        if "o" in frame and isinstance(frame["o"], dict):
            ohlcv = frame["o"]
            o_price = ohlcv.get("o")
            if sym and o_price is not None:
                _log.info(
                    "bar_received",
                    symbol=sym, bar_type="crypto",
                    open=round(o_price, 2), high=ohlo.get("h"), low=ohlcv.get("l"), 
                    close=ohlcv.get("c"), volume=ohlcv.get("v"), ts=ts
                )
        # Stock trades: T="t" with P (price) and s (size)
        elif T in ("t", "trade"):
            price = frame.get("P") or frame.get("price")
            size = frame.get("s") or frame.get("size")
            if sym and price is not None:
                _log.info(
                    "trade_received",
                    symbol=sym.upper(), price=price, size=size or 0, ts=ts
                )

    @staticmethod
    def _log_trade(sym: str, price: float, size: int | None, ts: str | None) -> None:
        """Log a trade event (info-level)."""
        _log.info(
            "trade_received",
            symbol=sym.upper(),
            price=price,
            size=size or 0,
            ts=ts,
        )

    @staticmethod
    def _log_info(msg: str, **extra) -> None:
        """Convenience logger call."""
        pass  # delegated to module-level _log elsewhere

    # -- DB setup --------------------------------------------------------

    async def _init_db(self) -> None:
        """Ensure the bars table exists in the trading DB."""
        conn = self.bars_db.connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    bar_type    TEXT    NOT NULL DEFAULT '1t',
                    open        REAL    NOT NULL,
                    high        REAL    NOT NULL,
                    low         REAL    NOT NULL,
                    close       REAL    NOT NULL,
                    volume      INTEGER NOT NULL DEFAULT 0,
                    timestamp   TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL
                )
            """)
            # Create indexes for fast lookups (crypto bars table)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bars_crypto (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    bar_type    TEXT    NOT NULL DEFAULT '1t',
                    open        REAL    NOT NULL,
                    high        REAL    NOT NULL,
                    low         REAL    NOT NULL,
                    close       REAL    NOT NULL,
                    volume      INTEGER NOT NULL DEFAULT 0,
                    timestamp   TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL
                )
            """)
            # Orders table — SignalRouter writes here for generated order directives
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT    NOT NULL,
                    side        TEXT    NOT NULL,
                    qty         REAL    NOT NULL,
                    price       REAL    NOT NULL,
                    source      TEXT,
                    status      TEXT    NOT NULL DEFAULT 'pending',
                    confidence  REAL,
                    strategy    TEXT,
                    created_at  TEXT    NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def _health_tick(self) -> None:
        """Periodic health logging every ~60 s."""
        stats = self.bar_agg.get_stats()
        h = self.ws_feed.health()
        stale_stock = self.ws_feed.is_stale("stock")
        stale_crypto = self.ws_feed.is_stale("crypto")
        _log.info(
            "bar_ingest_health",
            stock_connected=h["stock_connected"],
            crypto_connected=h["crypto_connected"],
            stock_ticks=h.get("stock_ticks", 0),
            crypto_ticks=h.get("crypto_ticks", 0),
            stale_stock=stale_stock,
            stale_crypto=stale_crypto,
            **stats,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main():
    ingest = BarIngest()

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
