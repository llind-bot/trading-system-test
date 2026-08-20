"""Alpaca dual-stream WebSocket feed — stock (SIP) + crypto, independent queues.

Mirrors the working CryptoStockWebSocketFeed from the old repo: msgpack Content-Type
header + msgpack auth + msgpack subscribe for BOTH endpoints.

Added automatic reconnection with exponential backoff so streams survive transient
disconnects (network blips, server-side drops, etc.) instead of dying silently.
"""

import asyncio
import json
import logging
import msgpack
import os
import time
from typing import Optional

from infra.logger import get_logger, StructuredMessage

_log = get_logger("ws-feed")


def _unpack_frame(raw):
    """Unwrap a WS frame — msgpack first, fallback to JSON."""
    if isinstance(raw, bytes):
        return msgpack.unpackb(raw, raw=False)
    return json.loads(raw)


class WSFeed:
    """Alpaca dual-stream feed.  Stock (SIP) and crypto are independent."""

    STOCK_URL = "wss://stream.data.alpaca.markets/v2/sip"
    CRYPTO_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

    DEFAULT_STOCK_SUBS = [
        "AAPL", "MSFT", "GOOGL", "AMZN",
        "MSTR", "SPCX", "NVDA", "V", "UNH", "PANW",
    ]
    DEFAULT_CRYPTO_SUBS = ["BTC/USD", "ETH/USD"]

    # Reconnection config
    RECONNECT_MIN_DELAY = 2      # seconds
    RECONNECT_MAX_DELAY = 60     # seconds
    RECONNECT_BACKOFF_FACTOR = 1.5

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self._stock_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=10000)
        self._crypto_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=10000)

        # API keys
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")

        # Stream refs
        self._stock_ws = None
        self._crypto_ws = None
        self._stock_sender_task: Optional[asyncio.Task] = None
        self._crypto_sender_task: Optional[asyncio.Task] = None
        self._running = False

        # Subscription lists (populated before start)
        self._stock_symbols: list[str] = []
        self._crypto_symbols: list[str] = []

        # Tick counters
        self._stock_tick_count = 0
        self._crypto_tick_count = 0
        self._last_stock_tick = time.time()
        self._last_crypto_tick = time.time()

        # Reconnection state per stream
        self._reconnect_delays: dict[str, float] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}

    # -- subscription setup ----------------------------------------------

    def set_stock_symbols(self, symbols: list[str]) -> None:
        self._stock_symbols = list(symbols)

    def set_crypto_symbols(self, symbols: list[str]) -> None:
        self._crypto_symbols = list(symbols)

    # -- queue access for consumers --------------------------------------

    def get_queue(self, stream: str) -> asyncio.Queue[bytes]:
        if stream == "stock":
            return self._stock_queue
        elif stream == "crypto":
            return self._crypto_queue
        raise ValueError(f"Unknown stream: {stream!r}")

    # -- connection / auth -----------------------------------------------

    async def _try_connect(self, stream_name: str) -> bool:
        """Connect + auth + subscribe for one stream.  Mirrors the old working code exactly."""
        import websockets

        if stream_name == "stock":
            url = self.STOCK_URL
            ping_interval = 30  # SIP responds to pings
        elif stream_name == "crypto":
            url = self.CRYPTO_URL
            ping_interval = None  # crypto does NOT accept client pings
        else:
            raise ValueError(stream_name)

        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    extra_headers={"Content-Type": "application/msgpack"},
                    ping_interval=ping_interval,
                    close_timeout=5,
                    max_queue=1024,
                ),
                timeout=5.0,
            )
        except Exception as e:
            _log.error("ws_connect_failed", stream=stream_name, error=str(e))
            return False

        if stream_name == "stock":
            self._stock_ws = ws
        else:
            self._crypto_ws = ws

        # 1. Welcome frame
        try:
            raw_welcome = await asyncio.wait_for(ws.recv(), timeout=5.0)
            welcome = _unpack_frame(raw_welcome)
            if isinstance(welcome, list):
                welcome = welcome[0]
            if welcome.get("T") == "success" and welcome.get("msg") == "connected":
                _log.debug("ws_connected", stream=url)
        except asyncio.TimeoutError:
            _log.warning("ws_no_welcome", stream=stream_name, url=url)

        # 2. Auth via msgpack (SAME for both stock and crypto)
        auth_msg = {"action": "auth", "key": self._api_key, "secret": self._secret_key}
        await ws.send(msgpack.packb(auth_msg))

        try:
            raw_auth = await asyncio.wait_for(ws.recv(), timeout=5.0)
            auth_resp = _unpack_frame(raw_auth)
            if isinstance(auth_resp, list):
                auth_resp = auth_resp[0]

            if auth_resp.get("T") == "error":
                detail = json.dumps(auth_resp) if isinstance(auth_resp, dict) else str(auth_resp)
                _log.error(StructuredMessage("ws_auth_failed", stream=stream_name, frame=detail))
                await ws.close()
                return False

            if auth_resp.get("T") == "success" and auth_resp.get("msg") == "authenticated":
                _log.info(StructuredMessage("ws_authenticated", stream=stream_name))
        except asyncio.TimeoutError:
            _log.error(StructuredMessage("ws_auth_timeout", stream=stream_name, url=url))
            await ws.close()
            return False

        # 3. Subscribe via msgpack (SAME format for both)
        if stream_name == "stock":
            syms = self._stock_symbols or self.DEFAULT_STOCK_SUBS
            sub_msg = {"action": "subscribe", "trades": syms, "quotes": syms}
        else:  # crypto
            syms = self._crypto_symbols or self.DEFAULT_CRYPTO_SUBS
            sub_msg = {"action": "subscribe", "trades": syms, "quotes": syms, "bars": syms}

        await ws.send(msgpack.packb(sub_msg))

        try:
            raw_sub = await asyncio.wait_for(ws.recv(), timeout=5.0)
            sub_resp = _unpack_frame(raw_sub)
            if isinstance(sub_resp, list):
                sub_resp = sub_resp[0]

            trade_count = len(sub_resp.get("trades", [])) if isinstance(sub_resp, dict) else 0
            full_sub = sub_resp if isinstance(sub_resp, dict) else {}
            _log.info(StructuredMessage(
                "ws_subscribed", 
                stream=stream_name, 
                trades=trade_count,
                subscribed_symbols=sub_msg.get("trades", []),
                response_keys=list(full_sub.keys()),
                full_response=str(full_sub)[:500]
            ))
        except asyncio.TimeoutError:
            _log.warning("ws_subscribe_timeout", stream=stream_name)

        return True

    async def _recv_loop(self, stream_name: str, ws, queue: asyncio.Queue):
        """Persistent recv loop — push raw frames to queue. Reconnects on disconnect."""
        import websockets as ws_module
        delay = self.RECONNECT_MIN_DELAY

        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                # Reset reconnect delay on success
                self._reconnect_delays[stream_name] = self.RECONNECT_MIN_DELAY

                try:
                    queue.put_nowait(raw)
                except asyncio.QueueFull:
                    _log.warning("ws_queue_full", stream=stream_name)
                    while queue.qsize() > 5000:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                # Only count actual data frames (not auth/welcome/subscribe frames) as ticks.
                # We identify data frames by checking if they look like trade/bars data.
                try:
                    frame = _unpack_frame(raw)
                    is_data = False
                    if isinstance(frame, dict):
                        T = frame.get("T") or frame.get("type")
                        # Trade event: has "T" in ("t", "trade")
                        if T in ("t", "trade"):
                            is_data = True
                        # Crypto bar event: has nested "o" (OHLCV)
                        elif "o" in frame and isinstance(frame.get("o"), dict):
                            is_data = True
                    elif isinstance(frame, list):
                        # Check if any item in the array is data
                        for item in frame:
                            if isinstance(item, dict):
                                T = item.get("T") or item.get("type")
                                if T in ("t", "trade"):
                                    is_data = True
                                    break
                                elif "o" in item and isinstance(item.get("o"), dict):
                                    is_data = True
                                    break

                    if is_data and stream_name == "crypto":
                        try:
                            # Extract symbol from the frame for per-symbol tracking
                            sym = (frame.get("S") or frame.get("s") or 
                                   (frame.get("o", {}) if isinstance(frame, dict) else {}).get("symbol", "?"))
                            T = frame.get("T") or frame.get("type") or "?"
                            # Log first occurrence of each symbol per type
                            _log.info(StructuredMessage(
                                "ws_crypto_frame",
                                symbol=sym,
                                frame_type=T,
                                raw_keys=list(frame.keys()) if isinstance(frame, dict) else len(frame),
                                raw_sample=str(frame)[:200]
                            ))
                        except Exception:
                            pass
                    
                    if is_data:
                        self._stock_tick_count += 1 if stream_name == "stock" else 0
                        self._crypto_tick_count += 1 if stream_name == "crypto" else 0
                        now = time.time()
                        if stream_name == "stock":
                            self._last_stock_tick = now
                        else:
                            self._last_crypto_tick = now
                except Exception:
                    # If we can't parse it, still deliver the frame but don't count as tick
                    pass

            except asyncio.TimeoutError:
                continue  # keep trying
            except ws_module.exceptions.ConnectionClosed:
                _log.info("ws_stream_closed", stream=stream_name)
                break
            except Exception as e:
                _log.warning("ws_sender_loop_error", stream=stream_name, error=str(e))
                break

        # Stream died — reconnect with backoff
        while self._running:
            current_delay = min(delay, self.RECONNECT_MAX_DELAY)
            _log.info(
                "ws_reconnecting",
                stream=stream_name,
                delay_s=current_delay,
            )
            await asyncio.sleep(current_delay)
            delay = delay * self.RECONNECT_BACKOFF_FACTOR

            # Clean up old ws/task references
            old_ws = getattr(self, f"_{stream_name}_ws")
            if old_ws:
                try:
                    await old_ws.close()
                except Exception:
                    pass
            old_task = getattr(self, f"_{stream_name}_sender_task", None)
            if old_task and not old_task.done():
                old_task.cancel()

            # Clear stale tick state so we don't report stale data after reconnect
            if stream_name == "stock":
                self._last_stock_tick = time.time()
            else:
                self._last_crypto_tick = time.time()

            success = await self._try_connect(stream_name)
            if success:
                _log.info("ws_reconnected", stream=stream_name)
                # Start new recv loop for this stream
                queue = self._stock_queue if stream_name == "stock" else self._crypto_queue
                ws_ref = self._stock_ws if stream_name == "stock" else self._crypto_ws
                task = asyncio.create_task(self._recv_loop(stream_name, ws_ref, queue))

                # Store the new sender task reference for proper cleanup
                attr = f"{stream_name}_sender_task"
                setattr(self, attr, task)
                self._stream_tasks[stream_name] = task

                delay = self.RECONNECT_MIN_DELAY  # Reset backoff on success
            else:
                _log.error("ws_reconnect_failed", stream=stream_name)
                # Don't reset delay — keep backing off

    # -- lifecycle -------------------------------------------------------

    async def start(self, timeout: float = 30.0) -> dict:
        self._running = True
        stock_ok = await self._try_connect("stock")
        crypto_ok = await self._try_connect("crypto")

        if not stock_ok and not crypto_ok:
            _log.error("ws_all_streams_failed")
            return {"stock_connected": False, "crypto_connected": False}

        # Start recv loops for each connected stream
        if stock_ok:
            queue = self._stock_queue
            ws_ref = self._stock_ws
            task = asyncio.create_task(self._recv_loop("stock", ws_ref, queue))
            self._stock_sender_task = task
            self._stream_tasks["stock"] = task

        if crypto_ok:
            queue = self._crypto_queue
            ws_ref = self._crypto_ws
            task = asyncio.create_task(self._recv_loop("crypto", ws_ref, queue))
            self._crypto_sender_task = task
            self._stream_tasks["crypto"] = task

        _log.info(
            "ws_feed_started",
            stock=stock_ok,
            crypto=crypto_ok,
        )
        return self.health()

    async def stop(self) -> None:
        self._running = False
        for ws in (self._stock_ws, self._crypto_ws):
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        for stream_name, task in list(self._stream_tasks.items()):
            if task and not task.done():
                task.cancel()
        self._stream_tasks.clear()

    def health(self) -> dict:
        return {
            "stock_connected": self._stock_ws is not None and self._running,
            "crypto_connected": self._crypto_ws is not None and self._running,
            "stock_ticks": self._stock_tick_count,
            "crypto_ticks": self._crypto_tick_count,
            "stock_last_tick_s": round(time.time() - self._last_stock_tick, 1),
            "crypto_last_tick_s": round(time.time() - self._last_crypto_tick, 1),
        }

    def is_stale(self, stream: str = "stock", threshold_s: int = 120) -> bool:
        last = self._last_stock_tick if stream == "stock" else self._last_crypto_tick
        return (time.time() - last) > threshold_s


# ---------------------------------------------------------------------------
# convenience — async generator to drain a queue with stale detection
# ---------------------------------------------------------------------------

async def drain_queue(feed: WSFeed, stream: str, *, stale_threshold: int = 120):
    q = feed.get_queue(stream)
    while True:
        try:
            raw = await asyncio.wait_for(q.get(), timeout=1.0)
            yield raw
        except asyncio.TimeoutError:
            if feed.is_stale(stream, stale_threshold):
                _log.warning("ws_stream_stale", stream=stream)
