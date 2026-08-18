"""Dollar-based position sizing calculator.

Sizing is based on dollar risk (max loss), not percentage of portfolio.
This keeps position sizes consistent regardless of account growth.
"""


def calculate_qty(risk_dollar: float, entry_price: float, stop_loss_price: float) -> float:
    """Calculate quantity using fixed-dollar risk sizing.

    qty = risk_dollar / |entry_price - stop_loss_price|

    Clamped to ensure no fractional shares < 1 for stocks (if needed).
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        return 0

    price_distance = abs(entry_price - stop_loss_price)
    if price_distance == 0:
        return 0

    qty = risk_dollar / price_distance
    return max(qty, 0)


def clamp_to_caps(qty: float, entry_price: float,
                  max_position_dollar: float, min_order_value: float) -> float:
    """Clamp quantity to position caps and minimum order value."""
    # Maximum based on position dollar cap
    max_qty_by_cap = max_position_dollar / entry_price if entry_price > 0 else float('inf')
    qty = min(qty, max_qty_by_cap)

    # Minimum based on minimum order value
    min_qty_by_min = min_order_value / entry_price if entry_price > 0 else 0
    qty = max(qty, min_qty_by_min)

    return max(0, qty)


def calculate_tp_target(avg_cost: float, profit_pct: float) -> float:
    """Calculate take-profit price from average cost and profit percentage."""
    return avg_cost * (1 + profit_pct / 100)


def calculate_profit_dollar(current_price: float, avg_cost: float, qty: float) -> float:
    """Calculate realized or unrealized profit/loss in dollars."""
    return (current_price - avg_cost) * qty


def calculate_tp_progress(avg_cost: float, current_price: float) -> float:
    """Calculate current profit percentage on a position."""
    if avg_cost <= 0:
        return 0.0
    return ((current_price - avg_cost) / avg_cost) * 100
