"""Dashboard configuration."""
import os
from pathlib import Path

TRADE_ROOT = Path(__file__).resolve().parent.parent.parent  # trading-system-test/
DB_PATH = str(TRADE_ROOT / "database" / "trades.db")       # trades, positions, equity_curve, errors, strategies
POSITIONS_DB = DB_PATH                              # positions (same DB)
BAR_DB = str(TRADE_ROOT / "database" / "bars.db")  # bars_crypto, bars_stock,
                                                      # engine_activity, engine_signals
CONFIG_DIR = TRADE_ROOT / "config"
LOGS_DIR = TRADE_ROOT / "logs"

# Server settings
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8081"))
DB_PATH = DB_PATH
TRADE_ROOT = TRADE_ROOT
