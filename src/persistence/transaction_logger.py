"""Transaction logger / symbol normalization — stub for dashboard compatibility.

The full transaction logging logic lives in the engine's order router at fill time.
This module provides `symbol_for_db` for the dashboard API layer.
"""


def symbol_for_db(symbol: str) -> str:
    """Normalize any symbol format to DB canonical form.
    
    Alpaca/BTCUSD  -> BTC/USD
    AVAXUSD        -> AVAX/USD  
    MSTR           -> MSTR (stocks unchanged)
    BTC/USD        -> BTC/USD (already normalized, no-op)
    """
    if not symbol:
        return ""
    
    upper = symbol.upper().strip()
    
    # Already in DB format with slash — return as-is
    if "/" in upper:
        return upper
    
    # Strip trailing "USD" and uppercase
    base = upper.replace("USD", "")
    
    if not base:
        return upper
    
    # Check if it's a known crypto symbol or BTC/ETH/SOL family → append /USD
    crypto_bases = {"BTC", "ETH", "SOL", "DOGE", "XRP", "LTC", "AVAX", "ADA", 
                    "UNI", "LINK", "DOT", "ATOM", "MATIC", "NEAR", "FIL", "ARB",
                    "OP", "PEPE", "WIF", "FET"}
    
    # BTCUSD -> BTC (already matched), ETHUSD -> ETH, etc.
    if base in crypto_bases:
        return f"{base}/USD"
    
    # Generic heuristic: if it ends with USD, the symbol before USD is the base
    # Otherwise treat as a stock ticker — keep as-is
    if upper.endswith("USD") and len(upper) > 3:
        base = upper[:-3]
        return f"{base}/USD"
    
    # Stock ticker or unrecognized — keep original case but uppercase
    return upper


# Backwards compatibility alias
def to_db_symbol(symbol: str) -> str:
    """Alias for symbol_for_db."""
    return symbol_for_db(symbol)
