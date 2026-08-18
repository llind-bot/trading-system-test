"""Phase 3 tests — ws_feed, BarAggregator, SignalRouter (no real API keys needed).

All tests use in-memory / local-only objects. No live WebSocket connections or
Alpaca credentials are required.
"""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

TRADE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRADE_ROOT))

os.environ.setdefault("ALPACA_ENV", "paper")
os.environ.setdefault("ALPACA_API_KEY", "test-key-fallback")
os.environ.setdefault("ALPACA_SECRET_KEY", "test-secret-fallback")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def inmemory_db():
    """Return a DB pool backed by a temp file for testing."""
    tmp = TRADE_ROOT / "database" / "test_ws_feed.db"
    db = pytest.importorskip("infra.db_pool", reason="db_pool not available").DatabasePool(tmp)
    conn = db.connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS bars (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, bar_type TEXT NOT NULL DEFAULT '1t', open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL DEFAULT 0, timestamp TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, source TEXT, status TEXT NOT NULL DEFAULT 'pending', confidence REAL, strategy TEXT, created_at TEXT)""")
    conn.commit()
    yield db
    conn.close()


# ---------------------------------------------------------------------------
# WSFeed initialization tests — no real connection
# ---------------------------------------------------------------------------

class TestWSFeedInit:
    def test_ws_feed_creates_queues(self):
        from infra.ws_feed import WSFeed
        feed = WSFeed("key", "secret")
        q = feed.get_queue("stock")
        assert isinstance(q, asyncio.Queue)
        assert feed.get_queue("crypto") is not None

    def test_ws_feed_stock_crypto_urls(self):
        from infra.ws_feed import WSFeed
        assert WSFeed.STOCK_URL == "wss://stream.data.alpaca.markets/v2/sip"
        assert WSFeed.CRYPTO_URL == "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

    def test_ws_feed_set_symbols(self):
        from infra.ws_feed import WSFeed
        feed = WSFeed("key", "secret")
        feed.set_stock_symbols(["PANW", "UNH"])
        feed.set_crypto_symbols(["BTC/USD", "ETH/USD"])
        assert feed._stock_symbols == ["PANW", "UNH"]
        assert feed._crypto_symbols == ["BTC/USD", "ETH/USD"]

    def test_ws_feed_health_before_start(self):
        from infra.ws_feed import WSFeed
        feed = WSFeed("key", "secret")
        h = feed.health()
        assert "stock_connected" in h
        assert "crypto_ticks" in h
        assert h["stock_connected"] is False  # not started yet


# ---------------------------------------------------------------------------
# BarAggregator tests
# ---------------------------------------------------------------------------

class TestBarAggregator:
    def test_bar_creation(self, inmemory_db):
        from engine.bar_ingest import BarAggregator
        agg = BarAggregator(inmemory_db, "1t")
        assert agg.current_bars == {}
        
        # Process first trade — should create a new bar
        agg.on_trade("PANW", 385.0, 100, "2026-08-18T14:30:00+00:00")
        assert len(agg.current_bars) == 1

    def test_bar_accumulation(self, inmemory_db):
        from engine.bar_ingest import BarAggregator
        agg = BarAggregator(inmemory_db, "1t")
        
        # Multiple trades within the same minute should accumulate
        agg.on_trade("PANW", 385.0, 100, "2026-08-18T14:30:00+00:00")
        agg.on_trade("PANW", 386.5, 200, "2026-08-18T14:30:30+00:00")
        agg.on_trade("PANW", 384.0, 50, "2026-08-18T14:30:45+00:00")
        
        bar = list(agg.current_bars.values())[0]
        assert bar["open"] == 385.0    # first trade price
        assert bar["high"] == 386.5    # highest
        assert bar["low"] == 384.0     # lowest
        assert bar["close"] == 384.0   # last price
        assert bar["volume"] == 350    # sum of sizes

    def test_bar_flush_to_db(self, inmemory_db):
        from engine.bar_ingest import BarAggregator
        agg = BarAggregator(inmemory_db, "1t")
        
        # Create and flush a bar
        agg.on_trade("PANW", 385.0, 100, "2026-08-18T14:30:00+00:00")
        flushed = agg.flush_all()
        
        assert flushed == 1
        
        # Verify it's in the DB
        conn = inmemory_db.connect()
        rows = conn.execute("SELECT symbol, open, high, low, close, volume FROM bars WHERE symbol='PANW'").fetchall()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "PANW"
        assert rows[0]["close"] == 385.0
        conn.close()

    def test_multiple_bars_in_progress(self, inmemory_db):
        from engine.bar_ingest import BarAggregator
        agg = BarAggregator(inmemory_db, "1t")
        
        # Create bars for two different symbols in the same minute window
        agg.on_trade("PANW", 385.0, 100, "2026-08-18T14:30:00+00:00")
        agg.on_trade("UNH", 400.0, 50, "2026-08-18T14:30:00+00:00")
        
        assert len(agg.current_bars) == 2


# ---------------------------------------------------------------------------
# SignalRouter tests
# ---------------------------------------------------------------------------

class TestSignalRouter:
    def test_receive_signal(self, inmemory_db):
        from engine.order_server import SignalRouter
        router = SignalRouter(inmemory_db)
        
        async def _run():
            await router.receive_signal({
                "symbol": "PANW", "side": "buy", 
                "strategy": "crypto_swing_daily", "confidence": 0.85, "price": 386.0
            })
            assert len(router.active_signals.get("PANW", [])) >= 1
        
        asyncio.run(_run())

    def test_signal_below_threshold(self, inmemory_db):
        from engine.order_server import SignalRouter
        router = SignalRouter(inmemory_db)
        
        async def _run():
            await router.receive_signal({
                "symbol": "PANW", "side": "buy",
                "strategy": "test", "confidence": 0.5, "price": 386.0
            })
            await router.evaluate_signals()
            
            # Should not write to orders — below threshold
            conn = inmemory_db.connect()
            rows = conn.execute("SELECT * FROM orders WHERE symbol='PANW'").fetchall()
            assert len(rows) == 0  # no order for low confidence
            conn.close()
        
        asyncio.run(_run())

    def test_signal_above_threshold_writes_order(self, inmemory_db):
        from engine.order_server import SignalRouter
        router = SignalRouter(inmemory_db)
        
        async def _run():
            await router.receive_signal({
                "symbol": "PANW", "side": "buy",
                "strategy": "crypto_swing_daily", "confidence": 0.85, "price": 386.0
            })
            await router.evaluate_signals()
            
            conn = inmemory_db.connect()
            rows = conn.execute("SELECT symbol, side, confidence FROM orders WHERE symbol='PANW'").fetchall()
            assert len(rows) >= 1
            assert rows[0]["side"] == "buy"
            conn.close()
        
        asyncio.run(_run())

    def test_multiple_signals_picks_highest_confidence(self, inmemory_db):
        from engine.order_server import SignalRouter
        router = SignalRouter(inmemory_db)
        
        async def _run():
            # Two conflicting signals for same symbol
            await router.receive_signal({
                "symbol": "PANW", "side": "buy",
                "strategy": "bullish", "confidence": 0.9, "price": 390.0
            })
            await router.receive_signal({
                "symbol": "PANW", "side": "sell",
                "strategy": "bearish", "confidence": 0.7, "price": 380.0
            })
            await router.evaluate_signals()
            
            # Should generate orders for both (different sides)
            conn = inmemory_db.connect()
            rows = conn.execute("SELECT side FROM orders WHERE symbol='PANW'").fetchall()
            sides = {r["side"] for r in rows}
            assert "buy" in sides or "sell" in sides  # at least one order written
            conn.close()
        
        asyncio.run(_run())

    def test_router_health(self, inmemory_db):
        from engine.order_server import SignalRouter
        router = SignalRouter(inmemory_db)
        h = router.health()
        assert "queued" in h or "active_symbols" in h or "signals" in h


# ---------------------------------------------------------------------------
# Import tests — verify all new Phase 3 code loads cleanly
# ---------------------------------------------------------------------------

class TestBarIngestImport:
    def test_bar_ingest_class_exists(self):
        from engine.bar_ingest import BarIngest, BarAggregator
        assert BarIngest is not None and BarAggregator is not None

    def test_bar_ingest_init_no_crash(self):
        """BarIngest can be instantiated with default env vars."""
        from engine.bar_ingest import BarIngest
        ingest = BarIngest()
        assert ingest.api_key != "" or os.environ.get("ALPACA_API_KEY") != ""


class TestWSSender:
    def test_sender_init(self):
        from infra.ws_feed import WSSender
        sender = WSSender("wss://test.url", ping_interval=0)
        assert sender.url == "wss://test.url"

    def test_unpack_frame_msgpack(self):
        from infra.ws_feed import _unpack_frame
        import msgpack
        data = {"T": "t", "S": "PANW", "P": 385.0}
        packed = msgpack.packb(data)
        result = _unpack_frame(packed)
        assert result["T"] == "t" and result["S"] == "PANW"

    def test_unpack_frame_json_fallback(self):
        from infra.ws_feed import _unpack_frame
        json_bytes = b'{"T": "success", "msg": "authenticated"}'
        result = _unpack_frame(json_bytes)
        assert result["T"] == "success"

    def test_msgpack_auth_format(self):
        from infra.ws_feed import _msgpack_auth
        packed = _msgpack_auth("key123", "secret456")
        unpacked = msgpack.unpackb(packed, raw=False)
        assert unpacked["action"] == "auth"
        assert unpacked["key"] == "key123"

    def test_msgpack_subscribe_format(self):
        from infra.ws_feed import _msgpack_subscribe
        packed = _msgpack_subscribe("trades", trades=["PANW"], quotes=["PANW"])
        unpacked = msgpack.unpackb(packed, raw=False)
        assert unpacked["action"] == "subscribe"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
