"""RSI (Relative Strength Index) indicator."""


def rsi(closes: list[float], period: int = 14) -> float:
    """Calculate RSI. Returns value between 0-100.

    Args:
        closes: list of closing prices (chronological order)
        period: lookback period (default 14)

    Returns:
        RSI value (0-100), or -1 if insufficient data
    """
    if len(closes) < period + 1:
        return -1

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent_gains = [d if d > 0 else 0 for d in deltas[-period:]]
    recent_losses = [-d if d < 0 else 0 for d in deltas[-period:]]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi_value = 100 - (100 / (1 + rs))
    return rsi_value


def rsi2(closes: list[float]) -> float:
    """Larry Connors' RSI(2) — the most backtested mean-reversion indicator."""
    return rsi(closes, period=2)
