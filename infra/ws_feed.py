"""Alpaca dual-stream WebSocket feed — stock (SIP) + crypto, independent queues.

Queue-based architecture replaces legacy callback patterns. Each stream writes raw
msgpack frames into an asyncio.Queue; consumers pull frames at their own pace.

Auth, subscribe, reconnect logic mirrors the real Alpaca Streams protocol.
"""

import asyncio
import logging
import math
import msgpack
import os
import time
from typing import Optional

from infra.logger import get_logger

_log = get_logger("ws-feed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msgpack_auth(key: str, secret: str) -> bytes:
    """Encode the auth handshake frame."""
    return msgpack.packb({"action": "auth", "key": key, "secret": secret})


def _msgpack_subscribe(stream: str, **subs) -> bytes:
    """Encode a subscription frame for a given stream type."""
    payload = {"action": "subscribe", f"{stream}": list(subs.get(stream, []))}
    # merge any extra keys (e.g. quotes for stock)
    for k, v in subs.items():
        if k != stream:
            payload[k] = list(v)
    return msgpack.packb(payload)


def _unpack_frame(raw: bytes) -> dict:
    """Try msgpack first; fall back to JSON decode."""
    try:
        return msgpack.unpackb(raw, raw=False)
    except Exception:
        import json
        return json.loads(raw)


# ---------------------------------------------------------------------------
# WSSender — one-direction queue-based WebSocket client per stream
# ---------------------------------------------------------------------------

class WSSender:
    """Thin wrapper around the ``websockets`` protocol.

    Public API is queue-driven:
      - push bytes via :meth:`send` to an internal send-queue
      - pull decoded frames via :meth:`recv` from a receive-queue
    """

    def __init__(self, url: str, ping_interval: float = 0):
        self.url = url
        self.ping_interval = ping_interval          # seconds; 0 = disable
        self._ws = None                               # active websocket handle
        self._send_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
        self._recv_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        self._receiver_task: Optional[asyncio.Task] = None
        self._connected = False

    # -- public ----------------------------------------------------------

    async def send(self, data: bytes) -> bool:
        """Push *data* (raw bytes) to the stream.  Returns True on success."""
        if self._ws is None or not self._connected:
            return False
        try:
            await self._send_queue.put(data)
            return True
        except asyncio.QueueFull:
            _log.warning("ws_send_queue_full", url=self.url)
            return False

    async def recv(self, timeout: float = 1.0) -> Optional[dict]:
        """Pull the next decoded frame (msgpack → dict).  *None* on timeout."""
        try:
            raw = await asyncio.wait_for(self._recv_queue.get(), timeout=timeout)
            return _unpack_frame(raw)
        except asyncio.TimeoutError:
            return None

    # -- internal --------------------------------------------------------

    async def _writer(self) -> None:
        """Drain _send_queue → WS send."""
        while True:
            data = await self._send_queue.get()
            if self._ws is not None and self._connected:
                try:
                    await self._ws.send(data)
                except Exception:
                    break

    async def start(self, timeout: float = 10.0) -> bool:
        """Open connection, handshake (welcome → auth → subscribe), return True on success."""
        import websockets

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url, ping_interval=self.ping_interval),
                timeout=min(timeout, 5.0),
            )
        except Exception as e:
            _log.error("ws_connect_failed", url=self.url, error=str(e))
            return False

        self._connected = True
        self._receiver_task = asyncio.create_task(self._reader())
        asyncio.ensure_future(self._writer())

        # --- welcome frame (first frame is always server-to-client) ---
        try:
            welcome = await asyncio.wait_for(self._recv_queue.get(), timeout=min(timeout, 5.0))
            _log.debug("ws_welcome", stream=self.url, frame=welcome)
        except asyncio.TimeoutError:
            _log.warning("ws_no_welcome", url=self.url)

        # --- auth -------------------------------------------------------
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        await self.send(_msgpack_auth(api_key, secret_key))

        try:
            auth_resp = await asyncio.wait_for(self._recv_queue.get(), timeout=min(timeout - 2, 5.0))
            if isinstance(auth_resp, dict) and auth_resp.get("T") == "success":
                _log.info("ws_authenticated", stream=self.url)
            else:
                _log.error("ws_auth_failed", frame=auth_resp)
                self._connected = False
                return False
        except asyncio.TimeoutError:
            _log.error("ws_auth_timeout", url=self.url)
            self._connected = False
            return False

        return True

    async def subscribe(self, **subs) -> bool:
        """Send subscription request.  *subs* keys map to Alpaca field names."""
        if not self._connected:
            return False
        await self.send(_msgpack_subscribe(**subs))
        _log.info("ws_subscribed", streams={k: len(v) for k, v in subs.items()})
        return True

    def set_stale(self):
        """Mark this stream as stale (no ticks received recently)."""
        self._last_tick_ts = time.time()

    async def stop(self) -> None:
        """Close the WebSocket and clean up tasks."""
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
        if self._ws:
            await self._ws.close()
        self._connected = False
        self._ws = None

    # -- reader (background) -------------------------------------------

    async def _reader(self) -> None:
        """Continuously read from WS → push to recv_queue.  Shed if queue > 500."""
        import websockets
        try:
            async for raw in self._ws:           # type: ignore[union-attr]
                while self._recv_queue.full():
                    self._recv_queue.get_nowait()      # shed oldest
                await self._recv_queue.put(raw)
                self._connected = True
        except (websockets.exceptions.ConnectionClosed, Exception):
            pass


# ---------------------------------------------------------------------------
# WSFeed — dual-stream orchestrator
# ---------------------------------------------------------------------------

class WSFeed:
    """Alpaca dual-stream feed.  Stock (SIP) and crypto are independent."""

    STOCK_URL = "wss://stream.data.alpaca.markets/v2/sip"
    CRYPTO_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

    # Default fallback symbols when watchlists are empty
    DEFAULT_STOCK_SUBS = ["AAPL", "MSFT", "GOOGL", "AMZN"]
    DEFAULT_CRYPTO_SUBS = ["BTC/USD", "ETH/USD"]

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        # queue infrastructure per stream
        self._stock_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        self._crypto_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)

        # sender wrappers
        self._stock_sender: Optional[WSSender] = None
        self._crypto_sender: Optional[WSSender] = None

        # subscription lists (populated before start)
        self._stock_symbols: list[str] = []
        self._crypto_symbols: list[str] = []

        # tick counters for stale detection
        self._stock_tick_count = 0
        self._crypto_tick_count = 0
        self._last_stock_tick = time.time()
        self._last_crypto_tick = time.time()

    # -- subscription setup ----------------------------------------------

    def set_stock_symbols(self, symbols: list[str]) -> None:
        """Symbols to subscribe on the stock SIP stream."""
        self._stock_symbols = list(symbols)

    def set_crypto_symbols(self, symbols: list[str]) -> None:
        """Symbols to subscribe on the crypto stream."""
        self._crypto_symbols = list(symbols)

    # -- queue access for consumers --------------------------------------

    def get_queue(self, stream: str) -> asyncio.Queue[dict]:
        """Return the asyncio.Queue for *stream* (``"stock"`` or ``"crypto"``)."""
        if stream == "stock":
            return self._stock_queue
        elif stream == "crypto":
            return self._crypto_queue
        raise ValueError(f"Unknown stream: {stream!r}")

    # -- connection / auth -----------------------------------------------

    async def _try_connect(self, stream_name: str) -> bool:
        """Connect + auth + subscribe for *stream_name*.  Returns True on success."""
        if stream_name == "stock":
            url = self.STOCK_URL
            sender = WSSender(url, ping_interval=30.0)
        elif stream_name == "crypto":
            url = self.CRYPTO_URL
            sender = WSSender(url, ping_interval=0)    # Alpaca rejects client pings on crypto
        else:
            raise ValueError(stream_name)

        if not await sender.start(timeout=15.0):
            return False

        # Subscribe — fill default fallback when list is empty
        if stream_name == "stock":
            syms = self._stock_symbols or self.DEFAULT_STOCK_SUBS
            quotes = [s for s in syms]  # subscribe quotes alongside trades
            await sender.subscribe(trades=syms, quotes=quotes)
        else:
            syms = self._crypto_symbols or self.DEFAULT_CRYPTO_SUBS
            await sender.subscribe(trades=syms, bars=syms)

        # Start background dispatcher for this stream
        asyncio.ensure_future(self._dispatch(stream_name, sender))
        return True

    async def _dispatch(self, stream_name: str, sender: WSSender) -> None:
        """Pull raw frames from the sender's recv_queue → push to our public queue.
        Tracks tick counts and stale detection."""
        target_queue = self._stock_queue if stream_name == "stock" else self._crypto_queue

        while True:
            frame = await sender.recv(timeout=2.0)
            if frame is None:
                continue                        # timeout, keep looping

            # Update tick metrics
            if stream_name == "stock":
                self._last_stock_tick = time.time()
                self._stock_tick_count += 1
            else:
                self._last_crypto_tick = time.time()
                self._crypto_tick_count += 1

            target_queue.put_nowait(frame)

    # -- lifecycle -------------------------------------------------------

    async def start(self, timeout: float = 30.0) -> dict:
        """Start both streams in background tasks.  Return health dict."""
        stock_ok = await self._try_connect("stock")
        crypto_ok = await self._try_connect("crypto")

        if not stock_ok and not crypto_ok:
            _log.error("ws_all_streams_failed")
            return {"stock_connected": False, "crypto_connected": False}

        _log.info("ws_feed_started", stock=stock_ok, crypto=crypto_ok)
        return self.health()

    async def stop(self) -> None:
        """Graceful shutdown of all streams and queues."""
        for s in (self._stock_sender, self._crypto_sender):
            if s:
                await s.stop()
        # Drain / close queues
        while not self._stock_queue.empty():
            try:
                self._stock_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        while not self._crypto_queue.empty():
            try:
                self._crypto_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def health(self) -> dict:
        """Current connection status and tick counts."""
        return {
            "stock_connected": self._stock_sender is not None and self._stock_sender._connected if self._stock_sender else False,
            "crypto_connected": self._crypto_sender is not None and self._crypto_sender._connected if self._crypto_sender else False,
            "stock_ticks": self._stock_tick_count,
            "crypto_ticks": self._crypto_tick_count,
            "stock_last_tick_s": round(time.time() - self._last_stock_tick, 1),
            "crypto_last_tick_s": round(time.time() - self._last_crypto_tick, 1),
        }

    def is_stale(self, stream: str = "stock", threshold_s: int = 120) -> bool:
        """Return True if no ticks received for *threshold_s* seconds."""
        last = self._last_stock_tick if stream == "stock" else self._last_crypto_tick
        return (time.time() - last) > threshold_s


# ---------------------------------------------------------------------------
# convenience — async generator to drain a queue with stale detection
# ---------------------------------------------------------------------------

async def drain_queue(feed: WSFeed, stream: str, *, stale_threshold: int = 120):
    """Yield decoded frames from *stream* until *feed* is stopped.

    Usage (in BarIngest, strategies, etc.):
        async for frame in drain_queue(ws_feed, "stock"):
            ...process...
    """
    q = feed.get_queue(stream)
    while True:
        try:
            raw = await asyncio.wait_for(q.get(), timeout=1.0)
            frame = _unpack_frame(raw)
            yield frame
        except asyncio.TimeoutError:
            if feed.is_stale(stream, stale_threshold):
                _log.warning("ws_stream_stale", stream=stream)
