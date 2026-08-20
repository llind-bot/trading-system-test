"""WebSocket live feed handler — polls engine state and pushes real-time diffs to browser clients."""
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("US/Eastern")
except ImportError:
    # Fallback for old Python
    EASTERN = None

from dashboard.utils.db_connector import (
    get_open_positions, query_all, get_equity_curve,
    get_strategy_signals_latest, get_engine_status
)
from dashboard.utils.alpaca_sync import get_live_prices


def _eastern_now_str():
    """Return current time as Eastern string in standard format."""
    if EASTERN:
        return datetime.now(EASTERN).strftime("%Y-%m-%d %I:%M:%S %p")
    else:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Track last seen state per client for diff computation
_last_seen: dict[str, int] = {}


class LiveFeedManager:
    """Manages WebSocket connections and data polling."""

    def __init__(self):
        self.clients: set = set()  # websocket connections
        self._running = False
        self._last_positions_hash = None
        self._last_equity_id = None

    async def start(self):
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        asyncio.create_task(self._poll_loop())

    async def stop(self):
        self._running = False
        for ws in list(self.clients):
            try:
                await ws.close()
            except Exception:
                pass
        self.clients.clear()

    async def add_client(self, ws):
        """Add a WebSocket client."""
        cid = id(ws)
        self.clients.add((ws, cid))
        # force initial full broadcast
        if not hasattr(self, '_initial_broadcast'):
            self._initial_broadcast = {}
        self._initial_broadcast[cid] = True
        try:
            await asyncio.sleep(0.1)  # ensure connection fully established
            await self._broadcast({
                "type": "heartbeat",
                "timestamp": _eastern_now_str()
            })
        except Exception as e:
            print(f"[dashboard ws] add_client error: {e}")
            self.clients.discard((ws, cid))

    async def remove_client(self, ws):
        cid = id(ws)
        _last_seen.pop(cid, None)
        self.clients.discard((ws, cid))

    async def _broadcast(self, message: dict):
        """Send a message to all connected clients."""
        msg_json = json.dumps(message)
        broken = []
        for ws, cid in list(self.clients):
            try:
                await ws.send_text(msg_json)
            except Exception:
                broken.append(cid)
        for cid in broken:
            self.clients.discard((ws, cid))

    async def _poll_loop(self):
        """Periodic data collection and diff broadcast."""
        while self._running:
            try:
                await asyncio.sleep(5)  # poll every 5s

                now = _eastern_now_str()

                # Get positions with live prices
                positions_data = await self._fetch_positions()
                pos_hash = str(sorted([(p["symbol"], p["current_price"]) for p in positions_data]))

                if pos_hash != self._last_positions_hash:
                    await self._broadcast({
                        "type": "position_update",
                        "data": positions_data,
                        "timestamp": now,
                    })
                    self._last_positions_hash = pos_hash

                # Get equity snapshot
                eq_data = await self._fetch_equity()
                if eq_data:
                    latest_id = eq_data.get("id") or (eq_data[-1]["id"] if eq_data else 0)
                    if latest_id != self._last_equity_id:
                        await self._broadcast({
                            "type": "equity_update",
                            "data": eq_data,
                            "timestamp": now,
                        })
                        self._last_equity_id = latest_id

                # Send heartbeat every ~60s (every ~12 wall-clock minutes)
                now_dt = datetime.now(EASTERN) if EASTERN else datetime.now()
                if now_dt.second == 0:
                    await self._broadcast({
                        "type": "heartbeat",
                        "timestamp": _eastern_now_str(),
                    })

            except Exception as e:
                print(f"[dashboard ws] poll error: {e}")

    async def _fetch_positions(self):
        """Fetch positions with live prices. Deduplicates by base ticker."""
        rows = query_all("SELECT * FROM positions WHERE is_closed=0 ORDER BY symbol")
        if not rows:
            return []

        # Group duplicates by base ticker (AVAX/USD + AVAXUSD → AVAX)
        seen = {}
        all_symbols_raw = []
        for pos in rows:
            sym = pos["symbol"]
            all_symbols_raw.append(sym)
            base = sym.upper()
            for suffix in ["/USD", "/USDT", "/USDC", "USD", "/USDT", "USDC"]:
                if base.endswith(suffix):
                    base = base[:-len(suffix)]
                    break
            if base not in seen:
                seen[base] = {"qty": 0, "cost_basis": 0.0, "avg_cost": 0.0,
                              "symbol": sym, "asset_class": pos["asset_class"]}
            entry = seen[base]
            entry["qty"] += pos["qty"]
            entry["cost_basis"] += pos["avg_cost"] * pos["qty"]
            if entry["qty"] > 0:
                entry["avg_cost"] = entry["cost_basis"] / entry["qty"]

        # Fetch live prices for all raw symbols
        try:
            live = get_live_prices(all_symbols_raw)
        except Exception:
            live = {}

        result = []
        for base, entry in seen.items():
            current_price = None
            for sym in [entry["symbol"]]:
                lp = live.get(sym, {})
                p = lp.get("price")
                if p and p > 0:
                    current_price = p
                    break
            if not current_price:
                current_price = entry["avg_cost"]

            cost_basis = round(entry["cost_basis"], 2)
            current_value = round(current_price * entry["qty"], 2) if current_price else 0
            unrealized_pnl = round(current_value - cost_basis, 2)
            pnl_pct = round((unrealized_pnl / cost_basis) * 100, 2) if cost_basis > 0 else None

            result.append({
                "symbol": base,
                "asset_class": entry["asset_class"],
                "qty": round(entry["qty"], 6),
                "avg_cost": round(entry["avg_cost"], 6) if entry["avg_cost"] else None,
                "current_price": round(current_price, 6) if current_price else None,
                "cost_basis": cost_basis,
                "current_value": current_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": pnl_pct,
                "current_price_live": current_price > 0,
            })

        return result

    async def _fetch_equity(self):
        """Fetch equity curve data."""
        try:
            eq_data = query_all("SELECT * FROM equity_curve ORDER BY id DESC LIMIT 1")
            if eq_data and isinstance(eq_data, list) and len(eq_data) > 0:
                row = dict(eq_data[0])
                row["id"] = int(row["id"])
                return row
            return None
        except Exception:
            return None


# Singleton instance
live_feed = LiveFeedManager()
