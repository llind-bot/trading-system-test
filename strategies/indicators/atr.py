"""ATR (Average True Range) indicator."""


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14):
    """Calculate ATR. Returns average true range value.

    Returns -1 if insufficient data.
    """
    tr_values = []
    for i in range(1, len(highs)):
        tr_val = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_values.append(tr_val)

    if len(tr_values) < period:
        return -1
    return sum(tr_values[-period:]) / period
