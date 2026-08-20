"""Live price sync wrapper — fetches prices from Alpaca REST with caching."""
import time
from dashboard.config.settings import TRADE_ROOT
import sys
sys.path.insert(0, str(TRADE_ROOT / "src"))
from data.alpaca_rest import AlpacaRestClient
from persistence.credentials import load_credentials


class PriceCache:
    """Simple TTL price cache keyed by symbol."""
    def __init__(self, ttl=30):
        self.ttl = ttl
        self._cache = {}

    def get(self, symbol):
        if symbol in self._cache:
            entry = self._cache[symbol]
            if time.time() - entry["ts"] < self.ttl:
                return entry["price"], entry["error"]
        return None

    def set(self, symbol, price, error=None):
        self._cache[symbol] = {"price": price, "error": error, "ts": time.time()}

    def clear(self):
        self._cache.clear()


_cache = PriceCache(ttl=30)


def get_live_prices(symbols):
    """Fetch live prices for a list of symbols from Alpaca REST.
    Returns dict: {symbol: {"price": float, "error": str|None}}"""
    if not symbols:
        return {}

    creds = load_credentials()
    client = AlpacaRestClient(
        api_key=creds.alpaca.api_key,
        secret_key=creds.alpaca.secret_key,
        paper=creds.alpaca.paper,
        base_url=creds.alpaca.effective_base_url,
    )

    results = {}
    positions = client.get_positions()

    # Build lookup from Alpaca response (normalized symbols)
    for pos in positions:
        sym = getattr(pos, 'symbol', '').upper()
        clean_sym = sym.replace("/", "")  # BTCUSD -> might need /
        price = float(getattr(pos, 'current_market_price') or getattr(pos, 'current_price', 0))
        results[sym] = {"price": price if price > 0 else None, "error": None}

    # Also try Alpaca quotes for each symbol (handles normalized vs with-slash)
    from data.symbol_utils import to_alpaca
    missing = set()
    for sym in symbols:
        price_cache_hit = _cache.get(sym)
        if price_cache_hit is not None and price_cache_hit[0] is not None:
            results[sym] = {"price": price_cache_hit[0], "error": None}
            continue
        missing.add(sym)

    # Fetch from Alpaca quotes API for any cache misses
    for sym in list(missing):
        try:
            # Normalize symbol for Alpaca (remove slashes)
            alpaca_sym = sym.upper().replace("/", "")
            quote = client.get_quote(alpaca_sym)
            if quote:
                # Handle both dict and Quote object returns
                if isinstance(quote, dict):
                    bp = quote.get("bp") or quote.get("bid_price") or quote.get("ask_price")
                else:
                    bp = getattr(quote, 'bid_price', None) or getattr(quote, 'ask_price', None)
                if bp:
                    price = float(bp)
                    _cache.set(sym, price)
                    results[sym] = {"price": price, "error": None}
                    continue
            # Fallback: check our cached positions
            if positions:
                for pos in positions:
                    p_sym = getattr(pos, 'symbol', '').upper().replace("/", "")
                    d_sym = alpaca_sym.replace("/", "")
                    if p_sym == d_sym and float(getattr(pos, 'qty', 0)) > 0:
                        price = float(getattr(pos, 'current_market_price') or getattr(pos, 'current_price', 0))
                        _cache.set(sym, price)
                        results[sym] = {"price": price, "error": None}
                        break
        except Exception as e:
            results[sym] = {"price": None, "error": str(e)}

    return results
