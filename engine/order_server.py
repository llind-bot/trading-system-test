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

from strategies.risk.manager import RiskManager, RiskCheckResult
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
        self._risk_mgr: RiskManager | None = None

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

    def _ensure_risk_mgr(self) -> RiskManager:
        """Lazily initialize RiskManager with config from risk_limits.yaml."""
        if self._risk_mgr is None:
            import yaml
            config_path = TRADE_ROOT / "config" / "risk_limits.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    risk_config = yaml.safe_load(f) or {}
            else:
                # Default risk limits if file is missing
                risk_config = {
                    "global": {
                        "max_drawdown_dollar": -1000,
                        "daily_loss_limit_dollar": -300,
                        "min_cash_reserve_dollar": 5000,
                        "max_total_exposure_dollar": 50000,
                        "max_concurrent_positions": 10,
                    }
                }
            self._risk_mgr = RiskManager(risk_config, {})
        return self._risk_mgr

    def _get_current_equity(self) -> float:
        """Fetch current account equity via Alpaca REST. Default to $93000 if unavailable."""
        try:
            import http.client
            import json as _json
            env_file = TRADE_ROOT / "config" / ".env"
            if not env_file.exists():
                return 93000.0
            with open(env_file) as f:
                for line in f:
                    if line.startswith("ALPACA_API_KEY="):
                        api_key = line.strip().split("=", 1)[1].strip()
                        break
                else:
                    return 93000.0
            # Use Alpaca paper trading account endpoint
            conn = http.client.HTTPSConnection("paper-api.alpaca.markets")
            conn.request("GET", "/v2/account", headers={"Authorization": f"Bearer {api_key}", "Alpaca-API-Key": api_key})
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            account = _json.loads(data)
            return float(account.get("equity", 93000.0))
        except Exception:
            return 93000.0

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
            qty = best.get("qty", 1)

            # --- Risk check (if BUY) ---
            if side.upper() == "BUY" and price > 0:
                order_value = float(price * qty)
                risk_dollar = order_value * confidence  # approximate risk
                current_equity = self._get_current_equity()
                risk_mgr = self._ensure_risk_mgr()
                check = risk_mgr.pre_trade_check(
                    symbol=sym.upper(),
                    order_value_dollar=order_value,
                    risk_dollar=risk_dollar,
                    current_equity=current_equity,
                )
                if not check.approved:
                    _safe_info(_log, "signal_risk_rejected",
                               symbol=sym.upper(),
                               reason=check.reason,
                               order_value=order_value,
                               equity=current_equity)
                    # Write rejected signal to engine_signals for audit trail
                    try:
                        conn = self.db.connect()
                        try:
                            conn.execute(
                                """INSERT INTO engine_signals 
                                   (timestamp, symbol, side, strategy, confidence, status, reason, engine)
                                   VALUES (?, ?, ?, ?, ?, 'rejected', ?, 'order-server-risk")
                           """,
                                (
                                    datetime.now(ZoneInfo("America/New_York")).isoformat(),
                                    sym.upper(),
                                    side,
                                    source,
                                    best.get("confidence", 0),
                                    check.reason,
                                ),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    except Exception as e:
                        _safe_error(_log, "rejected_signal_write_error",
                                    symbol=sym.upper(), error=str(e))
                    continue  # skip writing to orders

            try:
                conn = self.db.connect()
                try:
                    # Ensure orders table exists
                    try:
                        conn.execute("SELECT 1 FROM orders LIMIT 0")
                    except Exception:
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS orders (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                symbol TEXT NOT NULL, side TEXT NOT NULL,
                                qty REAL NOT NULL, price REAL NOT NULL,
                                source TEXT, status TEXT NOT NULL DEFAULT 'pending',
                                confidence REAL, strategy TEXT, created_at TEXT
                            )""")
                        conn.commit()
                    
                    conn.execute(
                        """INSERT INTO orders (symbol, side, qty, price, source, status,
                           confidence, strategy, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            sym.upper(),
                            side,
                            best.get("qty", 1),
                            price,
                            source,
                            "pending",
                            best.get("confidence", 0),
                            best.get("strategy", ""),
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
        self.order_pool = get_db("bars")
        self.router = SignalRouter(self.order_pool, confidence_threshold)

    async def _process_signals_from_db(self) -> list[dict]:
        """Read pending signals from the trades DB."""
        conn = self.signals_db.connect()
        pending = []
        try:
            rows = conn.execute(
                """SELECT id, symbol, side, strategy, confidence
                   FROM engine_signals
                   WHERE status IN ('pending', 'eval')
                   ORDER BY timestamp ASC"""
            ).fetchall()
            for row in rows:
                pending.append({
                    "id": row[0],
                    "symbol": row[1],
                    "side": row[2],
                    "strategy": row[3],
                    "confidence": float(row[4]) if row[4] else 0,
                    "reason": row[5] if len(row) > 5 else "",
                })

            # Mark as processed in DB
            ids = [r[0] for r in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE engine_signals SET status = 'processed', processed_at = ? WHERE id IN ({placeholders})",
                    [datetime.now(timezone.utc).isoformat()] + ids,
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
