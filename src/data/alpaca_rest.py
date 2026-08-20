"""Alpaca REST API client — unified for stocks and crypto.

All REST calls go through this client. Handles auth, retries, error responses.
"""

import time
from dataclasses import dataclass
from typing import Optional
from src.data.symbol_utils import to_alpaca

# Default base URLs — overridable via ALPACA_BASE_URL env var / .env
ALPACA_DEFAULT_URLS = {
    True: "https://paper-api.alpaca.markets",   # paper trading API
    False: "https://api.alpaca.markets",          # live trading API
}


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    daily_pnl: float
    realized_pnl: float
    non_marginable_buying_power: float

    @classmethod
    def from_response(cls, data: dict):
        return cls(
            equity=float(data.get("equity") or 0),
            cash=float(data.get("cash") or 0),
            buying_power=float(data.get("buying_power") or 0),
            daily_pnl=float(data.get("daily_pnl") or data.get("daytradingbuyingpower", 0) or 0),
            realized_pnl=float(data.get("realized_pnl") or 0),
            non_marginable_buying_power=float(data.get("non_marginable_buying_power") or 0),
        )


@dataclass
class Position:
    symbol: str
    asset_class: str
    qty: float
    avg_entry_price: float
    current_market_price: float
    market_value: float
    unrealized_pnl: float
    today_pnl: float

    @property
    def is_crypto(self) -> bool:
        """Determine if this position is crypto based on class or symbol."""
        return self.asset_class == "crypto" or "/USD" in self.symbol

    @property
    def current_price(self) -> float:
        """Alias for current_market_price for backwards compatibility."""
        return self.current_market_price

    @classmethod
    def from_response(cls, data: dict):
        return cls(
            symbol=data["symbol"],
            asset_class=data.get("asset_class", "unknown"),
            qty=float(data.get("qty", 0)),
            avg_entry_price=float(data.get("avg_entry_price") or 0),
            current_market_price=float(data.get("current_price") or 0),
            market_value=float(data.get("market_value") or 0),
            unrealized_pnl=float(data.get("unrealized_pl") or data.get("unrealized_pnl") or 0),
            today_pnl=float(data.get("today_pl") or data.get("today_pnl") or 0),
        )


@dataclass
class Asset:
    symbol: str
    name: str
    asset_class: str
    exchange: str
    tradable: bool
    fractionable: bool
    status: str


@dataclass
class Quote:
    symbol: str
    bid_price: float
    ask_price: float
    last_price: float

    @classmethod
    def from_response(cls, data: dict):
        return cls(
            symbol=data.get("symbol", ""),
            bid_price=float(data.get("bp", 0)),
            ask_price=float(data.get("ap", 0)),
            last_price=float(data.get("lp", 0)),
        )


class AlpacaRestClient:
    """REST API client for Alpaca Trading API."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True, base_url: Optional[str] = None):
        self.api_key = api_key
        self.secret_key = secret_key
        # Prefer explicit base_url, fall back to .env via effective_base_url if available,
        # then fall back to the default for paper/live mode.
        if base_url:
            self.base_url = base_url
        else:
            self.base_url = ALPACA_DEFAULT_URLS[paper]
        self.session = None  # lazy import

    def _session(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
            self.session.headers.update({
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Content-Type": "application/json",
            })
        return self.session

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        """GET request with retry on rate limit."""
        session = self._session()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = session.get(f"{self.base_url}{path}", params=params, timeout=10)
                if resp.status_code == 429:  # rate limited
                    wait = min(60 * (attempt + 1), 60)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    return None
                return resp.json()
            except Exception:
                if attempt == max_retries - 1:
                    raise
        return None

    def _post(self, path: str, data: dict) -> Optional[dict]:
        """POST request with retry."""
        session = self._session()
        for attempt in range(3):
            try:
                resp = session.post(f"{self.base_url}{path}", json=data, timeout=10)
                if resp.status_code == 429:
                    time.sleep(min(60 * (attempt + 1), 60))
                    continue
                return resp.json() if resp.status_code in (200, 201) else None
            except Exception:
                if attempt == 2:
                    raise
        return None

    def get_account(self) -> Optional[Account]:
        """Get current account state."""
        data = self._get("/v2/account")
        if data:
            return Account.from_response(data)
        return None

    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        data = self._get("/v2/positions")
        if data:
            return [Position.from_response(p) for p in data]
        return []

    def get_asset(self, symbol: str) -> Optional[Asset]:
        """Look up a single asset by symbol."""
        data = self._get(f"/v2/assets/{symbol}")
        if data and data.get("status") == "active":
            return Asset(
                symbol=data["symbol"],
                name=data.get("name", ""),
                asset_class=data.get("class", "unknown"),
                exchange=data.get("exchange", ""),
                tradable=data.get("tradable", False),
                fractionable=data.get("fractionable", False),
                status=data["status"],
            )
        return None

    def get_assets(self, **filters) -> list[Asset]:
        """List all assets with optional filters."""
        data = self._get("/v2/assets", params=filters)
        if data:
            return [
                Asset(symbol=a["symbol"], name=a.get("name", ""),
                      asset_class=a.get("class", "unknown"), exchange=a.get("exchange", ""),
                      tradable=a.get("tradable", False), fractionable=a.get("fractionable", False),
                      status=a.get("status", "unknown"))
                for a in data if a.get("status") == "active"
            ]
        return []

    def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get latest quote for a symbol using free-tier-friendly APIs.
        
        Strategy:
        1. Try stock quotes API first (gives bid/ask)
        2. Fall back to crypto bars or Coinbase for unknown symbols
        """
        import requests as req_lib
        
        # Normalize: MSTR -> MSTR/USD, BTCUSD -> BTC/USD, etc.
        norm = to_alpaca(symbol)  # e.g., 'BTC/USD' or 'MSTR/USD'
        sym_base = norm.split("/")[0] if "/" in norm else symbol
        
        # ── Detect asset class: known stocks vs crypto ──
        KNOWN_STOCKS = {"AAPL","MSFT","TSLA","NVDA","GOOGL","AMZN","META",
                        "NFLX","AMD","INTC","CRM","ADBE","PYPL","SHOP","SQ",
                        "ROD","JPM","BAC","WMT","DIS","VZ","PFE","KO","PEP",
                        "TMO","AVGO","LLY","COST"}
        KNOWN_CRYPTO = {"BTC","ETH","SOL","DOGE","XRP","LTC","ADA","AVAX",
                        "LINK","UNI","PEPE","SHIB","BNB","MSTR"}  # include MSTR as fallback
        
        is_known_stock = sym_base.upper() in KNOWN_STOCKS
        is_known_crypto = sym_base.upper() in KNOWN_CRYPTO
        
        def _try_stock_quotes():
            """Try Alpaca stock quotes API."""
            try:
                resp = req_lib.get(
                    f"https://data.alpaca.markets/v2/stocks/{sym_base}/quotes/latest",
                    headers={"Apca-Api-Key-ID": self.api_key,
                             "Apca-Api-Secret-Key": self.secret_key},
                    timeout=5
                )
                if resp.status_code == 200:
                    data = resp.json()
                    q = data.get("quote", {})
                    bp, ap = q.get("bp"), q.get("ap")
                    if bp and ap:
                        return Quote(symbol=symbol, bid_price=float(bp),
                                     ask_price=float(ap), last_price=float(ap))
            except Exception:
                pass
            return None
        
        def _try_stock_bars():
            """Try Alpaca stock bars API as fallback."""
            try:
                resp = req_lib.get(
                    f"https://data.alpaca.markets/v2/stocks/{sym_base}/bars",
                    headers={"Apca-Api-Key-ID": self.api_key,
                             "Apca-Api-Secret-Key": self.secret_key},
                    params={"timeframe": "1D", "limit": 1}, timeout=5
                )
                if resp.status_code == 200:
                    data = resp.json()
                    bars = data.get("bars", {})
                    if bars:
                        latest = list(bars.values())[-1] if isinstance(bars, dict) else bars[-1]
                        lp = float(latest.get("c", 0))
                        if lp > 0:
                            return Quote(symbol=symbol, bid_price=lp*0.9995,
                                         ask_price=lp*1.0005, last_price=lp)
            except Exception:
                pass
            return None
        
        def _try_crypto_bars():
            """Try Alpaca crypto bars."""
            try:
                resp = req_lib.get(
                    f"https://data.alpaca.markets/v1beta3/crypto/us/bars",
                    headers={"Apca-Api-Key-ID": self.api_key,
                             "Apca-Api-Secret-Key": self.secret_key},
                    params={"symbols": norm, "timeframe": "1Min"}, timeout=5
                )
                if resp.status_code == 200:
                    data = resp.json()
                    bars = data.get("bars", {})
                    if isinstance(bars, dict) and norm in bars:
                        latest = bars[norm][-1] if bars[norm] else None
                        if latest and float(latest.get("c", 0)) > 0:
                            return Quote(symbol=symbol,
                                         bid_price=float(latest.get("b", latest.get("bp", 0))),
                                         ask_price=float(latest.get("a", latest.get("ap", 0))),
                                         last_price=float(latest["c"]))
            except Exception:
                pass
            return None
        
        def _try_coinbase():
            """Try Coinbase public API for crypto."""
            try:
                base = norm.replace("/USD", "")
                resp = req_lib.get(
                    f"https://api.coinbase.com/v2/prices/{base}-USD/spot", timeout=5
                )
                if resp.status_code == 200:
                    price = float(resp.json()["data"]["amount"])
                    return Quote(symbol=symbol, bid_price=price*0.999,
                                 ask_price=price*1.001, last_price=price)
            except Exception:
                pass
            return None
        
        # ── Routing logic ──
        if is_known_stock:
            r = _try_stock_quotes()
            if r: return r
            return _try_stock_bars()
        elif is_known_crypto:
            r = _try_crypto_bars()
            if r: return r
            return _try_coinbase()
        else:
            # Unknown symbol — try stock first (covers MSTR, new tickers, etc.)
            r = _try_stock_quotes()
            if r: return r
            r = _try_crypto_bars()
            if r: return r
            r = _try_stock_bars()
            if r: return r
            return _try_coinbase()
    
    def place_order(self, order: dict) -> Optional[dict]:
        """Place an order. Returns the order response or None on failure.
        
        On failure, attaches `error_text` and `error_status` attributes
        to *self* so callers can read them for diagnosis.
        
        Returns:
            dict with order data on success (HTTP 200/201)
            None if request fails (exception) or returns non-success HTTP status
        """
        session = self._session()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = session.post(f"{self.base_url}/v2/orders", json=order, timeout=10)
                if resp.status_code == 429:  # rate limited
                    wait = min(60 * (attempt + 1), 60)
                    time.sleep(wait)
                    continue
                if resp.status_code in (200, 201):
                    return resp.json()
                # Non-success — attach error details on self for caller to inspect
                self.error_text = resp.text[:500]
                self.error_status = resp.status_code
                return None
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
        return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success (HTTP 204)."""
        session = self._session()
        max_retries = 3
        for attempt in range(3):
            try:
                resp = session.delete(f"{self.base_url}/v2/orders/{order_id}", timeout=10)
                if resp.status_code == 429:
                    wait = min(60 * (attempt + 1), 60)
                    continue
                return resp.status_code in (200, 204)
            except Exception:
                if attempt == 2:
                    raise
        return False

    def get_open_orders(self) -> list:
        """Get all open orders."""
        data = self._get("/v2/orders?status=open")
        return data or []

    def get_crypto_bars(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> list:
        """Fetch historical crypto bars from Alpaca market data API.
        
        Returns list of objects with .price (close), .volume, and OHLC attributes
        populated from the bar response. Uses 1min timeframe since that's what
        Alpaca's free-tier data API returns for crypto.
        """
        import requests
        # Alpaca crypto bars API requires slash format (BTC/USD, not BTCUSD)
        if "/" not in symbol.upper():
            sym = symbol + "/USD"
        else:
            sym = symbol
        md_url = f"https://data.alpaca.markets/v1beta3/crypto/us/bars"
        params = {"symbols": sym, "timeframe": "1min", "limit": str(limit)}
        resp = requests.get(md_url, headers={
            "Apca-Api-Key-ID": self.api_key,
            "Apca-Api-Secret-Key": self.secret_key
        }, params=params, timeout=10)
        if resp.status_code == 200:
            bars_data = resp.json().get("bars", {})
            sym_bars = bars_data.get(sym, [])
            result = []
            for b in sym_bars:
                # Full OHLCV from Alpaca bar response
                obj = type('Bar', (), {
                    'open': float(b.get('o', 0)),
                    'high': float(b.get('h', 0)),
                    'low': float(b.get('l', 0)),
                    'close': float(b.get('c', 0)),
                    'price': float(b.get('c', 0)),  # alias
                    'volume': float(b.get('v', b.get('volume', 0))),
                })()
                result.append(obj)
            return result
        self.error_text = resp.text[:500]
        self.error_status = resp.status_code
        return []

    def get_stock_bars(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> list:
        """Fetch historical stock bars from Alpaca market data API."""
        import requests
        # Alpaca stocks API expects timeframe like '1Min', '5Min' not '1m', '5m'
        alpaca_tf = {'1m': '1Min', '5m': '5Min', '15m': '15Min', '1h': '1Hour',
                     '4h': '4Hour', '1d': '1Day'}.get(timeframe, timeframe)
        md_url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        params = {"timeframe": alpaca_tf, "limit": str(limit)}
        resp = requests.get(md_url, headers={
            "Apca-Api-Key-ID": self.api_key,
            "Apca-Api-Secret-Key": self.secret_key
        }, params=params, timeout=10)
        if resp.status_code == 200:
            bars = resp.json().get("bars") or []
            result = []
            for b in bars:
                result.append(type('Bar', (), {
                    'price': float(b.get('c', 0)),
                    'volume': float(b.get('v', 0))
                })())
            return result
        self.error_text = resp.text[:500]
        self.error_status = resp.status_code
        return []
