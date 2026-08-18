"""Simple Moving Average (SMA) indicator."""


def sma(closes: list[float], period: int) -> float:
    """Calculate SMA. Returns value or -1 if insufficient data."""
    if len(closes) < period:
        return -1
    return sum(closes[-period:]) / period
