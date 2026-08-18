"""ADX (Average Directional Index) indicator."""


def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14):
    """Calculate ADX. Returns ADX value.

    Returns -1 if insufficient data.
    """
    if len(highs) < period + 1:
        return -1

    # Calculate directional movement
    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

    # True range
    tr = []
    for i in range(1, len(highs)):
        tr_val = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr.append(tr_val)

    if len(tr) < period:
        return -1

    atr = sum(tr[-period:]) / period
    plus_di = (sum(plus_dm[-period:]) / period / atr * 100) if atr > 0 else 0
    minus_di = (sum(minus_dm[-period:]) / period / atr * 100) if atr > 0 else 0

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx  # Note: true ADX smooths DX over multiple periods; this is simplified
