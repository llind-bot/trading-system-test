"""Unified position limit check — single source of truth for all buy-position checks.

Merged from:
  - src/telegram/order_interceptor.py::check_position_limit()
  - src/execution/order_executor.py::_check_position_limit()

All callers MUST use this module. Do NOT duplicate position-limit logic elsewhere.

Usage:
    from src.risk.position_check import check_position_limit

    allowed, reason = check_position_limit("BTC/USD", "buy", 100.0)
    if not allowed:
        print(f"Blocked: {reason}")
"""

from typing import Optional


# ── Known stock universe for classification fallback ───────────────────────
KNOWN_STOCKS = frozenset({
    "AAPL", "MSFT", "TSLA", "NVDA", "GOOGL", "AMZN", "META", "NFLX", "AMD",
    "INTC", "CRM", "ADBE", "PYPL", "SHOP", "SQ", "ROD", "JPM", "BAC",
    "WMT", "DIS", "VZ", "PFE", "KO", "PEP", "TMO", "AVGO", "LLY", "COST",
})


def check_position_limit(
    symbol: str,
    side: str,
    requested_amount: float,
    dollar_mode: bool = True,
) -> tuple[bool, str]:
    """Check whether a buy order would exceed the position limit for this symbol.

    Args:
        symbol: Symbol like "BTC/USD" or "AAPL"
        side: "buy" (only buy orders are checked; sells return allowed=True)
        requested_amount: Dollar notional (if dollar_mode) or quantity (if not)
        dollar_mode: If True, requested_amount is a dollar value; if False, it's quantity

    Returns:
        (allowed: bool, reason: str)
        - allowed=True  → order can proceed
        - allowed=False → reason explains why (e.g. "position limit exceeded")
    """
    # Sells never have position limits — they reduce exposure
    if side.lower() != "buy":
        return True, ""

    try:
        from src.persistence.config_loader import get_config
        wl_cfg = get_config("watchlist") or {}
        assets = wl_cfg.get("assets", [])
        max_pos = 100
        min_order = 50 if not dollar_mode else 20
        _wl_defaults = wl_cfg.get("defaults", {}) or {}

        for asset in assets:
            sym = asset.get("symbol", "").upper()
            norm_asset = sym.replace("/", "").upper()
            req_sym = symbol.upper().replace("/USD", "")
            if norm_asset == req_sym:
                max_pos = asset.get("max_position_dollar", 100)
                min_order = asset.get("min_order_value", 20)
                break

        # Apply per-class defaults when no matching asset found
        if max_pos == 100:
            sym_base = symbol.replace("/USD", "").upper()
            is_crypto = sym_base not in KNOWN_STOCKS
            _class_defaults = _wl_defaults.get("crypto" if is_crypto else "stock", {})
            max_pos = _class_defaults.get("max_position_dollar", 1500 if is_crypto else 1000)
            min_order = _class_defaults.get("min_order_value", 20 if is_crypto else 50)

    except Exception:
        # Absolute fail-safe
        sym_base = symbol.replace("/USD", "").upper()
        is_crypto_2 = sym_base not in KNOWN_STOCKS
        try:
            from src.persistence.config_loader import get_config
            _wf = (get_config("watchlist") or {}).get("defaults", {}) or {}
            _cd = _wf.get("crypto" if is_crypto_2 else "stock", {})
            max_pos = _cd.get("max_position_dollar", 1500 if is_crypto_2 else 1000)
        except Exception:
            max_pos = 100

    # ── Live cost basis from Alpaca positions ───────────────────────────
    live_basis = 0.0
    try:
        from src.persistence.credentials import load_credentials
        from src.data.alpaca_rest import AlpacaRestClient
        client = AlpacaRestClient(
            load_credentials().alpaca.api_key,
            load_credentials().alpaca.secret_key,
            load_credentials().alpaca.paper,
        )
        positions = client.get_positions()
        sym_norm = _to_alpaca(symbol)
        for p in positions:
            if _to_alpaca(p.symbol) == sym_norm and abs(float(p.qty)) > 1e-10:
                live_basis = float(p.qty) * float(p.avg_entry_price)
                break
    except Exception:
        pass

    # Include ~0.6% taker fees on existing position
    total_deployed = live_basis * 1.006

    remaining = max_pos - total_deployed
    if remaining < 0:
        remaining = 0

    # ── For qty buys, get price to compute notional ─────────────────────
    total_would_be_dollar = None
    if dollar_mode:
        total_would_be_dollar = total_deployed + requested_amount
        over_cap = total_would_be_dollar > max_pos
    else:
        # Need live price for qty buys — already checked in order_router place_order()
        # For this simple check, use a conservative estimate
        over_cap = (total_deployed + requested_amount) > max_pos

    if over_cap or remaining < min_order:
        return False, f"Position limit exceeded for {symbol} (${max_pos:,.0f} cap)"

    return True, ""


def _to_alpaca(symbol: str) -> str:
    """Convert symbol to Alpaca format (BTC/USD → BTC)."""
    # Import here to avoid circular deps at module load time
    from src.data.symbol_utils import to_alpaca as _to_alpaca_raw
    return _to_alpaca_raw(symbol)
