"""Portfolio correlation filter.

Prevents entering too many positions in correlated assets (e.g., two tech stocks).
Simplified: checks asset class overlap for now; full correlation matrix can be added later.
"""


def check_correlation(open_symbols: list[str], new_symbol: str, max_per_class: int = 5) -> bool:
    """Check if adding new_symbol would exceed per-class position limit.

    Args:
        open_symbols: currently open symbol list (e.g., ["AAPL", "MSFT", "BTC/USD"])
        new_symbol: the symbol we want to add
        max_per_class: maximum concurrent positions per asset class

    Returns:
        True if allowed, False if correlation limit would be exceeded
    """
    # Simple class-based correlation check
    open_classes = {}
    for sym in open_symbols:
        cls = "crypto" if sym.endswith("/USD") else "stock"
        open_classes[cls] = open_classes.get(cls, 0) + 1

    new_class = "crypto" if new_symbol.endswith("/USD") else "stock"
    return open_classes.get(new_class, 0) < max_per_class


def check_max_concurrent(open_count: int, max_positions: int) -> bool:
    """Check if we can add another position."""
    return open_count < max_positions
