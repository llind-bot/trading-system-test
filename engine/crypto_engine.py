"""Crypto engine — standalone strategy evaluation for crypto assets.

24/7 operation (no market hours check). Same structure as stock_engine.

Run: python3 engine/crypto_engine.py
"""
import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ALPACA_ENV", "paper")

TRADE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADE_ROOT))

from strategies.crypto_swing_daily import CryptoSwingDaily
from infra.db_pool import get_db
from infra.logger import get_logger
from infra.notify_engine import get_notify

_log = get_logger("crypto-engine")
_notify = get_notify()


class CryptoEngine:
    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self._running = False
        self.bars_db = get_db("bars")
        self.signals_db = get_db("trades")
        self.watchlist: dict[str, dict] = {}
        self.signal_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._risk_config_cache: dict[str, float] = {}  # symbol -> max_position_dollar

    def _get_risk_config(self) -> dict[str, float]:
        """Load risk limits from watchlist.yaml on first call, then cache."""
        if not self._risk_config_cache:
            import yaml
            wl_path = TRADE_ROOT / "config" / "watchlist.yaml"
            if wl_path.exists():
                with open(wl_path) as f:
                    data = yaml.safe_load(f) or {}
                for asset in data.get("assets", []):
                    sym = str(asset.get("symbol", "")).upper()
                    if sym:
                        self._risk_config_cache[sym] = float(
                            asset.get("max_position_dollar", 1000)
                        )
            else:
                # Default cap if watchlist missing
                pass
        return self._risk_config_cache

    def _get_existing_position_value(self, symbol: str) -> float:
        """Get current position value for a symbol from the positions table."""
        try:
            conn = self.signals_db.connect()
            row = conn.execute(
                "SELECT qty * current_price FROM positions WHERE symbol=? AND qty > 1e-10",
                (symbol.upper(),),
            ).fetchone()
            conn.close()
            if row and row[0]:
                return max(float(row[0]), 0)
        except Exception:
            pass
        return 0.0

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
                    strategy = CryptoSwingDaily()
                    signal_result = strategy.evaluate(bars, asset_cfg.get("strategy_params", {}))
                    
                    # Handle both dict-style (legacy) and StrategyResult object returns
                    if signal_result is None:
                        continue
                    if hasattr(signal_result, 'signal'):
                        # StrategyResult object
                        side = signal_result.signal.name if signal_result.signal else None
                        confidence = getattr(signal_result, 'confidence', 0.0)
                        reason = getattr(signal_result, 'reason', '')
                        strategy_name = getattr(signal_result, 'strategy', strat_name) if hasattr(signal_result, 'strategy') else strat_name
                    else:
                        # Dict-style (legacy)
                        side = signal_result.get("side")
                        confidence = signal_result.get("confidence", 0.0)
                        reason = signal_result.get("reason", "")
                        strategy_name = signal_result.get("strategy", strat_name)
                    
                    if side and side not in ("HOLD", None):
                        # Include the last bar's close as the signal price (fallback market price)
                        signal_price = bars[-1]["open"] if bars else 0.0
                        
                        # --- Risk check before writing to DB ---
                        if side.upper() == "BUY" and signal_price > 0:
                            risk_cfg = self._get_risk_config()
                            max_pos_dollar = float(
                                asset_cfg.get("max_position_dollar", risk_cfg.get(symbol.upper(), 1000))
                            )
                            existing_value = self._get_existing_position_value(symbol)
                            total_after = existing_value + signal_price * 1.0  # qty=1 default
                            if total_after > max_pos_dollar:
                                _log.warning("position_limit_exceeded", symbol=symbol,
                                    side=side, price=signal_price,
                                    total_after=f"${total_after:,.0f}", limit=max_pos_dollar)
                                # Write rejected signal for audit trail
                                try:
                                    rconn = self.signals_db.connect()
                                    try:
                                        rconn.execute(
                                            "INSERT INTO engine_signals "
                                            "(timestamp, symbol, side, strategy, confidence, status, reason, engine)"
                                            " VALUES (?, ?, ?, ?, ?, 'rejected', ?, ?)",
                                            (
                                                datetime.now(timezone.utc).isoformat(),
                                                symbol.upper(),
                                                side,
                                                strat_name,
                                                confidence,
                                                f"Position cap exceeded: ${total_after:,.0f} > ${max_pos_dollar:,.0f}",
                                                "crypto-engine",
                                            ),
                                        )
                                        rconn.commit()
                                    finally:
                                        rconn.close()
                                except Exception as e:
                                    _log.error("rejected_signal_write_error", symbol=symbol, error=str(e))
                                continue
                        
                        self._write_signal_to_db(symbol, {
                            "side": side,
                            "confidence": confidence,
                            "reason": reason,
                            "strategy": strategy_name,
                            "price": signal_price,
                        })
                    
                    # Always log evaluation result to signal_evaluations for dashboard visibility
                    self._log_evaluation(symbol, strat_name, side or "HOLD", confidence, reason)
                    
                    # Also write to engine_signals (for strategy history grid in dashboard)
                    self._write_to_engine_signals(symbol, strat_name, side or "HOLD", confidence, reason)
                except Exception as e:
                    _log.error("strategy_eval_error", symbol=symbol, strategy=strat_name, error=str(e))

    def _fetch_new_bars(self) -> dict[str, list[dict]]:
        """Fetch crypto bar data from DB pool."""
        conn = self.bars_db.connect()
        result = {}
        try:
            for symbol in self.watchlist.keys():
                rows = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM bars_crypto "
                    "WHERE symbol = ? AND bar_type='240t' ORDER BY timestamp ASC",
                    (symbol,),
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

    def _write_to_engine_signals(self, symbol: str, strategy_name: str, side: str, confidence: float, reason: str):
        """Write evaluation to engine_signals for dashboard strategy history grid.

        Uses status='eval' so the order_server (which only processes 'pending') won't consume it.
        The dashboard's Evaluation Grid queries status IN ('eval', 'pending').
        """
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO engine_signals (symbol, side, strategy, confidence, status, timestamp)
                VALUES (?, ?, ?, ?, 'eval', ?)
            """, (
                symbol,
                side,
                strategy_name,
                confidence,
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        except Exception as e:
            _log.warning("engine_signals_write_error", symbol=symbol, error=str(e))
        finally:
            conn.close()

    def _log_evaluation(self, symbol: str, strategy_name: str, side: str, confidence: float, reason: str):
        """Write evaluation result to signal_evaluations for dashboard."""
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO signal_evaluations 
                    (symbol, strategy, side, confidence, reason, engine)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                strategy_name,
                side,
                confidence,
                reason[:500] if reason else '',
                'crypto-engine',
            ))
            conn.commit()
        except Exception as e:
            _log.warning("eval_write_error", symbol=symbol, error=str(e))
        finally:
            conn.close()

    def _write_signal_to_db(self, symbol: str, signal_result: dict):
        """Write signal to DB."""
        conn = self.signals_db.connect()
        try:
            conn.execute("""
                INSERT INTO engine_signals (symbol, side, strategy, confidence, status, reason, price, engine, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol.upper(),
                signal_result["side"],
                signal_result.get("strategy", "crypto_swing_daily"),
                signal_result.get("confidence", 0.0),
                'pending',
                signal_result.get("reason", ''),
                signal_result.get("price", 0.0),
                'crypto-engine',
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    async def run(self):
        """Main event loop — runs 24/7."""
        self._running = True
        await self._load_config()
        
        _log.info("engine_start", watchlist=list(self.watchlist.keys()))
        
        _tick_count = 0
        while self._running:
            try:
                await self._tick()
                _tick_count += 1
                if _tick_count % 6 == 0:  # Log every ~3 minutes
                    total_bars = sum(len(v) for v in self._fetch_new_bars().values())
                    _log.info('engine_progress', tick=_tick_count, total_bars_in_db=total_bars)
                await asyncio.sleep(1)  # Prevent CPU burn
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
