"""Order server — receives signals and routes them via SignalRouter.

SignalRouter evaluates confidence thresholds, picks the best signal per symbol,
and writes order directives to the DB pool.
"""

import asyncio
import json
import logging
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
from infra.logger import get_logger, JSONFormatter


_log = get_logger("order-server")


def _safe_info(logger: logging.Logger, msg: str, **extra):
    """Log INFO with extra fields in a way that works both with and without handlers."""
    record = logger.makeRecord(
        logger.name, logging.INFO, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = extra
    logger.handle(record)


def _safe_error(logger: logging.Logger, msg: str, **extra):
    """Log ERROR with extra fields safely."""
    record = logger.makeRecord(
        logger.name, logging.ERROR, "(unknown)", 0, msg, (), None
    )
    if extra:
        record.extra_fields = extra
    logger.handle(record)


# ---------------------------------------------------------------------------
# SignalRouter
# ---------------------------------------------------------------------------

class SignalRouter:
    """Routes incoming signals to active strategies.

    Receives signal dicts on an internal asyncio.Queue, then evaluates them
    — picking the highest-confidence signal per symbol when multiple arrive,
    and generating order directives when confidence exceeds a threshold.
    """

    DEFAULT_CONFIDENCE_THRESHOLD = 0.7

    def __init__(self, db_pool, confidence_threshold: float | None = None):
        self.db = db_pool
        self.confidence_threshold = confidence_threshold or self.DEFAULT_CONFIDENCE_THRESHOLD
        self.signal_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self.active_signals: dict[str, list[dict]] = {}   # symbol → list of signal dicts

    async def receive_signal(self, signal_dict: dict) -> None:
        """Queue a new signal for evaluation."""
        await self.signal_queue.put(signal_dict)
        sym = signal_dict.get("symbol", "UNKNOWN")
        if sym not in self.active_signals:
            self.active_signals[sym] = []
        self.active_signals[sym].append(signal_dict)
        _safe_info(_log, "signal_received",
                   symbol=sym,
                   side=signal_dict.get("side"),
                   confidence=signal_dict.get("confidence", 0),
                   strategy=signal_dict.get("strategy", ""))

    async def evaluate_signals(self) -> list[dict]:
        """Drain the queue, evaluate, and write order directives to DB.

        Returns a list of generated order dicts (one per symbol with signals above threshold).
        """
        orders = []

        while not self.signal_queue.empty():
            try:
                sig = self.signal_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            sym = sig.get("symbol", "UNKNOWN")
            confidence = float(sig.get("confidence", 0))

            if confidence < self.confidence_threshold:
                _safe_info(_log, "signal_below_threshold",
                           symbol=sym,
                           confidence=confidence,
                           threshold=self.confidence_threshold)
                continue

            # Pick the highest-confidence signal for this symbol
            signals = self.active_signals.get(sym, [])
            if len(signals) > 1:
                best = max(signals, key=lambda s: float(s.get("confidence", 0)))
                _safe_info(_log, "signal_selected_best",
                           symbol=sym,
                           selected_confidence=float(best.get("confidence", 0)),
                           others_count=len(signals) - 1)
            else:
                best = signals[0] if signals else sig

            # Generate order directive and write to DB
            side = best.get("side", "buy")
            price = float(best.get("price", 0))
            source = best.get("strategy", "unknown")

            try:
                conn = self.db.connect()
                try:
                    conn.execute(
                        """INSERT INTO orders (symbol, side, qty, price, source, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            sym.upper(),
                            side,
                            best.get("qty", 1),
                            price,
                            source,
                            "pending",
                            datetime.now(ZoneInfo("America/New_York")).isoformat(),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                _safe_error(_log, "order_write_error", symbol=sym, error=str(e))
                continue

            orders.append({"symbol": sym.upper(), "side": side, "price": price, "source": source})
            _safe_info(_log, "order_directive_written",
                       symbol=sym.upper(),
                       side=side,
                       price=price,
                       source=source)

        return orders

    def health(self) -> dict:
        """Return signal router health."""
        return {
            "queued": self.signal_queue.qsize(),
            "active_symbols": len(self.active_signals),
        }


# ---------------------------------------------------------------------------
# OrderServer — ties SignalRouter into the main event loop
# ---------------------------------------------------------------------------

class OrderServer:
    """Main process — polls signals DB, routes through SignalRouter."""

    def __init__(self, poll_interval: int = 10, confidence_threshold: float | None = None):
        self.poll_interval = poll_interval
        self._running = False
        self.signals_db = get_db("trades")
        self.order_pool = get_db("trading")
        self.router = SignalRouter(self.order_pool, confidence_threshold)

    async def _process_signals_from_db(self) -> list[dict]:
        """Read pending signals from the trades DB."""
        conn = self.signals_db.connect()
        pending = []
        try:
            rows = conn.execute(
                """SELECT id, symbol, side, strategy, confidence
                   FROM engine_signals
                   WHERE status = 'pending'
                   ORDER BY timestamp ASC"""
            ).fetchall()
            for row in rows:
                pending.append({
                    "id": row[0],
                    "symbol": row[1],
                    "side": row[2],
                    "strategy": row[3],
                    "confidence": float(row[4]) if row[4] else 0,
                })

            # Mark as processed in DB
            ids = [r[0] for r in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE engine_signals SET status = 'processed', processed_at = ? WHERE id IN ({placeholders})",
                    [datetime.now().isoformat()] + ids,
                )
                conn.commit()
        finally:
            conn.close()

        self.signals_db.checkpoint()
        return pending

    async def run(self) -> None:
        """Main event loop."""
        self._running = True
        _safe_info(_log, "order_server_start")

        while self._running:
            try:
                # 1. Pull signals from DB
                db_signals = await self._process_signals_from_db()
                for sig in db_signals:
                    await self.router.receive_signal(sig)

                # 2. Evaluate and route
                orders = await self.router.evaluate_signals()

                if orders:
                    _safe_info(_log, "orders_generated",
                               count=len(orders), symbols=[o["symbol"] for o in orders])

            except Exception as e:
                _safe_error(_log, "order_server_error", detail=str(e))

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main():
    server = OrderServer()

    def _sh(sig, frame):
        _safe_info(_log, "shutdown", signal=sig)
        server.stop()

    signal.signal(signal.SIGINT, _sh)
    signal.signal(signal.SIGTERM, _sh)

    try:
        await server.run()
    except Exception as e:
        _safe_error(_log, "fatal_error", detail=str(e))
    finally:
        server.stop()


if __name__ == "__main__":
    asyncio.run(main())
