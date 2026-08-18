"""Bollinger Bands indicator."""


def bollinger_bands(closes: list[float], period: int = 20, multiplier: float = 2.0):
    """Calculate Bollinger Bands. Returns (upper, middle, lower, bandwidth).

    Returns None if insufficient data.
    """
    if len(closes) < period:
        return None

    recent = closes[-period:]
    middle = sum(recent) / period
    std = (sum((x - middle) ** 2 for x in recent) / period) ** 0.5
    upper = middle + multiplier * std
    lower = middle - multiplier * std
    bandwidth = (upper - lower) / middle if middle != 0 else 0

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
        "std": std,
    }
