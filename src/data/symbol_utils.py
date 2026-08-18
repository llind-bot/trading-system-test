# Symbol normalization utilities — SINGLE source of truth.
# Watchlist format (canonical): "BTC/USD" — human readable with slash.
# Alpaca API format:          "BTCUSD"  — no slash, all caps.
# Both directions supported via to_watchlist() / to_alpaca().

def _normalize(s: str) -> str:
    """Strip all slashes and 'USD' suffix, uppercase. Used as canonical key."""
    return s.upper().replace("/", "").replace("USD", "")


def to_alpaca(symbol: str) -> str:
    """Normalize any symbol format to Alpaca's BTCUSD style (no slash)."""
    return _normalize(symbol)


def to_watchlist(symbol: str) -> str:
    """Normalize any symbol format to watchlist canonical "BTC/USD" style."""
    base = _normalize(symbol)  # strip everything → "BTC"
    if base.endswith("USD"):
        base = base[:-3]  # remove trailing USD → "BTC"
    return f"{base}/USD"


def is_crypto(symbol: str) -> bool:
    """Check if a symbol represents a crypto asset."""
    upper = symbol.upper()
    return (upper.endswith("/USD") 
            or upper.endswith("USD") 
            or "/USD" in upper 
            or _normalize(symbol).rstrip("USD") in {"BTC", "ETH", "SOL", "DOGE", "XRP", "LTC"})


# Pre-computed set of normalized crypto base tickers for quick lookups.
CRYPTO_BASES = frozenset({"BTC", "ETH", "SOL", "DOGE", "XRP", "LTC"})


def format_crypto_price(price: float, buffer_pct: float) -> str:
    """Format a crypto limit price with enough decimals to avoid zero-ing out micro-prices.

    For prices >= $0.01 rounds to 2dp (normal crypto). For <$0.01 uses enough dp so the
    buffered price never becomes 0.0 -- e.g. PEPE at ~$0.000009 * 1.10 = 0.0000099.
    """
    import decimal
    buffered = price * (1 + buffer_pct)
    if buffered >= 0.01:
        return str(round(buffered, 2))
    # Use Decimal for precise formatting of micro-prices — no float noise
    d = decimal.Decimal(str(price)) * decimal.Decimal(str(1 + buffer_pct))
    d = d.quantize(decimal.Decimal('1E-9'), rounding=decimal.ROUND_HALF_UP)
    # Strip trailing zeros but keep at least 2 significant decimal places
    s = str(d.normalize())
    if '.' in s:
        dp = len(s.split('.')[0]) + len(s.split('.')[-1]) - 1  # digits after leading zeros
    else:
        dp = 9
    fmt_dp = max(dp, 9)
    result = d.quantize(decimal.Decimal(f'1E-{fmt_dp}'))
    return str(result.normalize())


def crypto_limit(price: float, buy: bool) -> str:
    """Build a crypto limit price string with proper decimal precision."""
    buffer = 0.10 if buy else -0.10
    return format_crypto_price(price, buffer)

