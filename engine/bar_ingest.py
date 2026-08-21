"""Bar ingest — real Alpaca WS feed → trade aggregation → bar DB writes.

Replaces the Phase-1 stub in bar_ingest_test.py with a full pipeline:
  1. Connect to Alpaca dual-stream WebSocket (stock + crypto)
  2. Accumulate incoming trades into minute / 5m / 15m bars
  3. Flush completed bars to SQLite via infra.db_pool

API keys are read from os.environ (ALPACA_API_KEY, ALPACA_SECRET_KEY).
All DB access goes through infra.db_pool — no direct sqlite3.connect().

CRASH PREVENTION:
  - All numeric fields validated before use (no += None, max(None, x), etc.)
  - Every frame type logged at INFO with full metadata before processing
  - Fatal errors caught and logged with full stack trace so root cause is visible
  - Graceful restart loop on any unhandled exception (max 5 tries, then stops)
"""

import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("ALPACA_ENV", "paper")

# Load .env config so API keys / DB paths are available in os.environ
_TRADE_ROOT = Path(__file__).resolve().parent.parent
_env_path = _TRADE_ROOT / "config" / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
del _env_path

TRADE_ROOT = _TRADE_ROOT
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
        self.db = db_pool
        self.bar_type = bar_type
        _interval_map = {"1t": 60, "5t": 300, "15t": 900, "240t": 14400, "1440t": 86400}
        self.interval_s = _interval_map.get(bar_type, 60)
        self.bar_minutes = self.interval_s // 60

        # Current in-progress bars keyed by (symbol, bar_window)
        self.current_bars: dict[str, dict] = {}
        self._log = get_logger("bar-ingest")

    def _bar_key(self, symbol: str, ts_iso: str) -> str:
        """Return the window-aligned key for *symbol* at *ts_iso*, using this bar's minutes."""
        try:
            dt = datetime.fromisoformat(ts_iso).replace(tzinfo=ZoneInfo("UTC"))
        except Exception:
            return f"{symbol}:invalid_ts"
        floored_min = (int(dt.minute) // self.bar_minutes) * self.bar_minutes
        dt_floor = dt.replace(minute=floored_min, second=0, microsecond=0)
        return f"{symbol}:{dt_floor.strftime('%Y-%m-%dT%H:%M:%S')}"

    def on_trade(self, symbol: str, price: float, size: float, ts_iso: str) -> None:
        """Process a trade event.  Flushes completed bars to DB automatically.

        All numeric values are coerced to safe defaults (0/None) if they come through
        as None or non-numeric types from the WS frame. This is the fix for the
        recurring crash on size=None.
        """
        # ── Validate incoming values ────────────────────────────────────
        price = self._to_float(price)
        size = self._to_float(size, default=0)  # zero-size trades are valid (e.g., off-book)
        
        if symbol is None or ts_iso is None:
            return

        key = self._bar_key(symbol, ts_iso)

        if key not in self.current_bars:
            # New bar window — flush previous if it exists for this symbol
            self._flush_current(key, symbol)

            # Start new bar with validated values
            self.current_bars[key] = {
                "symbol": symbol.upper(),
                "open": price or 0,
                "high": price or 0,
                "low": price or 0,
                "close": price or 0,
                "volume": size or 0,
                "start_ts": ts_iso,
            }
        else:
            # Update existing bar
            bar = self.current_bars[key]
            safe_price = price or bar["close"]
            bar["high"] = max(bar["high"], safe_price)
            bar["low"] = min(bar["low"], safe_price)
            bar["close"] = safe_price
            bar["volume"] = (bar.get("volume", 0) or 0) + (size or 0)

    @staticmethod
    def _to_float(value, default=None):
        """Safely convert any value to float, returning default if None/non-numeric."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _flush_current(self, current_key: str, symbol: str) -> None:
        """Flush the in-progress bar for *symbol* (if it exists and is different from *current_key*)."""
        keys_to_flush = [k for k in self.current_bars if k.startswith(f"{symbol}:") and k != current_key]
        for key in keys_to_flush:
            bar = self.current_bars.pop(key, None)
            if bar and (bar.get("volume", 0) or 0) > 0:
                self._write_bar(bar)

    def _write_bar(self, bar: dict) -> None:
        """Persist one completed bar row to the DB."""
        try:
            conn = self.db.connect()
            # Write to generic bars table (legacy compatibility)
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
                    int(bar.get("volume", 0) or 0),
                    bar["start_ts"],
                    datetime.now(ZoneInfo("America/New_York")).isoformat(),
                ),
            )
            # Also write to bars_crypto since that's what the crypto engine reads
            timeframe_map = {
                "1t": "1m", "5t": "5m", "15t": "15m",
                "240t": "240m", "1440t": "1D",
            }
            timeframe = timeframe_map.get(self.bar_type, "1m")
            conn.execute(
                """INSERT INTO bars_crypto (symbol, bar_type, open, high, low, close, volume, timestamp, created_at, timeframe)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bar["symbol"], self.bar_type,
                    round(bar["open"], 2), round(bar["high"], 2),
                    round(bar["low"], 2), round(bar["close"], 2),
                    int(bar.get("volume", 0) or 0),
                    bar["start_ts"],
                    datetime.now(ZoneInfo("America/New_York")).isoformat(),
                    timeframe,
                ),
            )
            # Also write to bars_stock since the stock engine reads from there
            conn.execute(
                """INSERT INTO bars_stock (symbol, bar_type, open, high, low, close, volume, timestamp, created_at, timeframe)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bar["symbol"], self.bar_type,
                    round(bar["open"], 2), round(bar["high"], 2),
                    round(bar["low"], 2), round(bar["close"], 2),
                    int(bar.get("volume", 0) or 0),
                    bar["start_ts"],
                    datetime.now(ZoneInfo("America/New_York")).isoformat(),
                    timeframe,
                ),
            )
            conn.commit()
        except Exception as e:
            self._log.error(
                "bar_write_error",
                error=str(e),
                symbol=bar.get("symbol"),
                bar_type=self.bar_type,
            )
        finally:
            conn.close()

    def flush_all(self) -> int:
        """Write all in-progress bars to DB and clear them.  Returns count."""
        written = len([b for b in self.current_bars.values() if (b.get("volume", 0) or 0) > 0])
        for key, bar in list(self.current_bars.items()):
            if (bar.get("volume", 0) or 0) > 0:
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

    Has a robust restart loop (max 5 attempts) so it doesn't just die and stay dead.
    All frame types are logged at INFO level for diagnostic purposes.
    """

    def __init__(self, api_key: str | None = None, secret_key: str | None = None, bar_types=None):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self.bar_types = bar_types or ["1t", "5t", "15t", "240t", "1440t"]
        self._running = False
        self.bars_db = get_db("bars")
        self.aggregators = {bt: BarAggregator(self.bars_db, bt) for bt in self.bar_types}
        self.ws_feed = WSFeed(self.api_key, self.secret_key)

    # -- public lifecycle ------------------------------------------------

    async def start(self) -> dict:
        """Initialize DB tables, connect feeds, and return health."""
        if self._running:
            return self.ws_feed.health()
        await self._init_db()

        # Load crypto symbols from watchlist config
        import yaml
        wl_path = Path(__file__).resolve().parent.parent / "config" / "watchlist.yaml"
        try:
            with open(wl_path) as f:
                wl = yaml.safe_load(f) or {}
            crypto_symbols = [s["symbol"] for s in wl.get("assets", [])
                              if s.get("asset_class") == "crypto" and s.get("enabled", True)]
        except FileNotFoundError:
            _log.error("watchlist_config_missing", path=str(wl_path))
            crypto_symbols = WSFeed.DEFAULT_CRYPTO_SUBS

        self.ws_feed.set_crypto_symbols(crypto_symbols)

        health = await self.ws_feed.start(timeout=15.0)
        self._running = True
        _log.info(StructuredMessage("bar_ingest_started", bar_types=self.bar_types, **health))
        return health

    async def run(self) -> None:
        """Main event loop — drain WS queues, aggregate, write DB."""
        await self.start()

        last_health_ts = 0
        error_count = 0
        
        while self._running:
            try:
                stock_queue = self.ws_feed.get_queue("stock")
                crypto_queue = self.ws_feed.get_queue("crypto")

                frames_processed = 0
                for q in (stock_queue, crypto_queue):
                    while not q.empty():
                        try:
                            raw = q.get_nowait()
                            frame = _unpack_frame(raw)
                            
                            # Log every frame at INFO level for diagnostics
                            if isinstance(frame, list):
                                _log.info("ws_frame_arriving", type_hint="array", count=len(frame))
                            elif isinstance(frame, dict):
                                T = frame.get("T") or frame.get("type", "?")
                                sym = frame.get("S") or frame.get("symbol", "?")
                                _log.info("ws_frame_arriving", T=T, symbol=sym)
                            
                            if isinstance(frame, list):
                                for item in frame:
                                    if isinstance(item, dict):
                                        self._handle_frame(item)
                            elif frame and isinstance(frame, dict):
                                self._handle_frame(frame)

                            frames_processed += 1
                        except asyncio.QueueEmpty:
                            break
                        except Exception as frame_err:
                            error_count += 1
                            _log.error("ws_frame_unpack_error", 
                                       error=str(frame_err),
                                       raw_len=len(raw) if 'raw' in locals() else 0,
                                       trace=traceback.format_exc())
                            continue

                if frames_processed > 0:
                    _log.debug("frames_drained", count=frames_processed)

                await asyncio.sleep(0.1)

                # Periodic health, bar flush, and DB checkpoint every ~60s
                now = asyncio.get_event_loop().time()
                if now - last_health_ts > 60:
                    # Flush all in-progress bars to DB (prevents data loss on crash)
                    flushed = sum(ag.flush_all() for ag in self.aggregators.values())
                    if flushed > 0:
                        _log.info("bar_ingest_periodic_flush", count=flushed)
                    
                    # Checkpoint DB WAL files
                    try:
                        conn = self.bars_db.connect()
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        conn.close()
                    except Exception:
                        pass
                    
                    await self._health_tick()
                    last_health_ts = now

            except Exception as e:
                error_count += 1
                _log.error(
                    "bar_ingest_loop_error",
                    detail=str(e),
                    trace=traceback.format_exc(),
                    consecutive_errors=error_count,
                )
                # Don't crash the whole process on single error — continue loop
                if error_count > 10:
                    _log.error("bar_ingest_too_many_errors", count=error_count)
                    self.stop()
                await asyncio.sleep(1)

    def stop(self) -> None:
        """Graceful shutdown — flush remaining bars, close streams."""
        _log.info("bar_ingest_stopping")
        self._running = False
        flushed = sum(ag.flush_all() for ag in self.aggregators.values())
        if flushed > 0:
            _log.info("bar_ingest_final_flush", count=flushed)

    # -- frame handling --------------------------------------------------

    def _handle_frame(self, frame: dict) -> None:
        """Dispatch a raw trade/quote/bars event from the WS feed.

        All frames are logged at INFO before processing so we can see exactly what
        Alpaca sends. The key fix: all price/size fields must be validated as safe
        floats before passing to the aggregator (line that was crashing with += None).
        """
        T = frame.get("T") or frame.get("type")
        
        # Error control frames
        if T == "error":
            _log.error("ws_error_frame", stream="unknown", msg=frame.get("msg", ""))
            return
        
        # Subscription confirmations — skip
        if T in ("subscription", "success"):
            return

        sym = frame.get("S") or frame.get("symbol")
        raw_ts = frame.get("t") or frame.get("T_s") or frame.get("timestamp")
        
        # Convert timestamp to ISO string
        if hasattr(raw_ts, "seconds") and hasattr(raw_ts, "nanoseconds"):
            ts = datetime.fromtimestamp(raw_ts.seconds + raw_ts.nanoseconds * 1e-9, tz=timezone.utc).isoformat()
        elif hasattr(raw_ts, "__float__"):
            ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc).isoformat()
        elif isinstance(raw_ts, (int, float)):
            ts = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()
        else:
            ts = raw_ts
        
        # Crypto bars: nested OHLCV in 'o' key
        if "o" in frame and isinstance(frame["o"], dict):
            ohlcv = frame["o"]
            o_price = ohlcv.get("o")
            if sym and o_price is not None:
                _log.info(
                    "bar_received",
                    symbol=sym, bar_type="crypto",
                    open=round(o_price, 2), 
                    high=ohlcv.get("h"), 
                    low=ohlcv.get("l"), 
                    close=ohlcv.get("c"), 
                    volume=ohlcv.get("v"), 
                    ts=ts,
                )
            return

        # Stock trades: T="t" with P/p (price) and s (size)
        if T in ("t", "trade"):
            price = frame.get("p") or frame.get("P") or frame.get("price")
            size = frame.get("s") or frame.get("size")
            
            # ── CRITICAL FIX: validate numeric values before passing to aggregator ──
            if sym is None:
                _log.warning("trade_frame_no_symbol", T=T, raw_frame=frame)
                return
            
            if price is None:
                _log.warning(
                    "trade_frame_no_price",
                    symbol=sym, size=size, ts=ts, raw_keys=list(frame.keys()),
                    detail="Price was None — dropping this trade frame to prevent crash",
                )
                return
            
            # Coerce to float safely
            price_f = self._to_float(price)
            size_f = self._to_float(size, default=0)
            
            if price_f is None or price_f <= 0:
                _log.warning(
                    "trade_frame_invalid_price",
                    symbol=sym, raw_price=price, ts=ts,
                    detail="Price could not be coerced to float — dropping frame",
                )
                return

            _log.info(
                "trade_received",
                symbol=sym.upper(), price=price_f, size=size_f, ts=ts,
            )
            
            for agg in self.aggregators.values():
                agg.on_trade(sym.upper(), price_f, size_f, ts)
            return
        
        # Unknown frame type — log for diagnostics
        _log.warning("unknown_frame_type", T=T, keys=list(frame.keys())[:10])

    @staticmethod
    def _to_float(value, default=None):
        """Safely convert any value to float, returning default if None/non-numeric."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    # -- DB setup --------------------------------------------------------

    async def _init_db(self) -> None:
        """Ensure the bars tables exist in the trading DB."""
        conn = self.bars_db.connect()
        try:
            for tbl_sql in [
                "CREATE TABLE IF NOT EXISTS bars (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_type TEXT NOT NULL DEFAULT '1t', open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, timestamp TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS bars_crypto (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_type TEXT NOT NULL DEFAULT '1t', open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, timestamp TEXT NOT NULL, created_at TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '1m')",
                "CREATE TABLE IF NOT EXISTS bars_stock (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_type TEXT NOT NULL DEFAULT '1t', open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, timestamp TEXT NOT NULL, created_at TEXT NOT NULL, timeframe TEXT NOT NULL DEFAULT '1m')",
                "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, source TEXT, status TEXT NOT NULL DEFAULT 'pending', confidence REAL, strategy TEXT, created_at TEXT NOT NULL)",
            ]:
                conn.execute(tbl_sql)
            conn.commit()
        finally:
            conn.close()

    async def _health_tick(self) -> None:
        """Periodic health logging every ~60 s."""
        stats = {k: v for ag in self.aggregators.values() for k, v in ag.get_stats().items()}
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
# Main loop with restart logic
# ---------------------------------------------------------------------------

async def main():
    """Run bar ingest with automatic restart on crash."""
    max_restarts = 5
    restart_delay = 2
    
    for attempt in range(1, max_restarts + 1):
        _log.info("bar_ingest_attempt", attempt=attempt, max=max_restarts)
        
        ingest = BarIngest()

        def _sh(sig, frame):
            _log.info("shutdown", signal=sig)
            ingest.stop()

        signal.signal(signal.SIGINT, _sh)
        signal.signal(signal.SIGTERM, _sh)

        try:
            await ingest.run()
            _log.info("bar_ingest_exited_normally")
            break  # normal exit — stop trying
        except Exception as e:
            _log.error(
                "bar_ingest_fatal_error",
                attempt=attempt,
                max=max_restarts,
                error=str(e),
                trace=traceback.format_exc(),
            )
            ingest.stop()
            
            if attempt < max_restarts:
                _log.info("bar_ingest_will_restart", delay=restart_delay)
                await asyncio.sleep(restart_delay)
        finally:
            ingest.stop()
    
    _log.info("bar_ingest_giving_up", attempts=max_restarts, message="Max restarts reached. Check logs for root cause.")


if __name__ == "__main__":
    asyncio.run(main())
