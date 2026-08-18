import os, sys, sqlite3
from pathlib import Path
import pytest

os.environ["ALPACA_ENV"] = "paper"

TRADE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRADE_ROOT))

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_trades.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, qty REAL, price REAL, status TEXT DEFAULT 'pending', timestamp TEXT);
        CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE, qty REAL DEFAULT 0, avg_cost REAL DEFAULT 0, unrealized_pl REAL DEFAULT 0, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS engine_signals (id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT, strategy TEXT, confidence REAL, engine TEXT, status TEXT DEFAULT 'pending', processed_at TEXT);
        CREATE TABLE IF NOT EXISTS tp_events (id INTEGER PRIMARY KEY, symbol TEXT, tp_level INTEGER, sell_pct REAL, profit_pct REAL, timestamp TEXT);
    """)
    conn.commit()
    yield db_file
    conn.close()

@pytest.fixture
def mock_notify():
    from infra import notify_engine
    original_send = notify_engine.NotifyEngine._send_telegram
    sent_messages = []
    def _capture(self, text):
        sent_messages.append(text)
    notify_engine.NotifyEngine._send_telegram = _capture
    engine = notify_engine.get_notify()
    yield engine, sent_messages
    notify_engine.NotifyEngine._send_telegram = original_send

@pytest.fixture
def sample_bars():
    return [
        {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0, "volume": 10000, "timestamp": f"2026-08-17T{h:02d}:00:00"}
        for h in range(8, 20)
    ]

@pytest.fixture
def sample_watchlist():
    return {
        "assets": [
            {"symbol": "PANW", "asset_class": "stock", "strategies": ["crypto_swing_daily"], "max_position_dollar": 1000},
            {"symbol": "BTC/USD", "asset_class": "crypto", "strategies": ["crypto_swing_daily"], "tp_levels": [{"level": 1, "sell_pct": 0.5, "profit_pct": 0.02}]},
        ]
    }
