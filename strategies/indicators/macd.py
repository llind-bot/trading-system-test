"""MACD (Moving Average Convergence Divergence) indicator."""

import math


def ema(values: list[float], period: int) -> list[float]:
    """Calculate EMA for a series of values. Returns full list of EMA values."""
    if len(values) < 1:
        return []

    k = 2 / (period + 1)
    emas = [values[0]]
    for i in range(1, len(values)):
        ema_val = k * values[i] + (1 - k) * emas[-1]
        emas.append(ema_val)
    return emas


def macd(closes: list[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9):
    """Calculate MACD. Returns (macd_line, signal_line, histogram).

    Returns (-1, -1, -1) if insufficient data.
    """
    if len(closes) < slow_period + signal_period:
        return (-1, -1, -1)

    ema_fast = ema(closes, fast_period)
    ema_slow = ema(closes, slow_period)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

    # Signal line is EMA of MACD line
    signal_vals = ema(macd_line[-signal_period:], signal_period) if len(macd_line) >= signal_period else [-1]
    signal_line = signal_vals[-1]

    histogram = macd_line[-1] - signal_line
    return (macd_line[-1], signal_line, histogram)
