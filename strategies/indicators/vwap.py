"""VWAP (Volume Weighted Average Price) indicator."""


def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]):
    """Calculate VWAP. Returns VWAP value.

    Args:
        highs, lows, closes: price series
        volumes: corresponding volume series

    Returns:
        VWAP value or -1 if insufficient data
    """
    typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    tpv_sum = sum(tp * v for tp, v in zip(typical_prices, volumes))
    vol_sum = sum(volumes)

    if vol_sum == 0:
        return -1
    return tpv_sum / vol_sum
