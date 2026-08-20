"""Unified database access for the trading system.

Single source of truth — all SQLite connections go through this pool.
No direct sqlite3.connect() anywhere in the new system.

WAL mode enforced + autocheckpoint on every connection.
Thread-safe (asyncio-compatible).
"""

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional


class DatabasePool:
    """Connection pool for one database file. WAL + checkpoint always enforced."""

    def __init__(self, db_path: Path):
        self.db_path = str(db_path.resolve())
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        """Create a new connection with WAL mode and autocheckpoint enforced."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        # WAL settings — always, no exceptions
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=100")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row  # dict-like access by default
        return conn

    def checkpoint(self) -> str:
        """Run a TRUNCATE checkpoint on all open connections. Returns 'ok' or error message."""
        try:
            conn = self.connect()
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.close()
            return str(result[0])  # 0=ok, 1=retry, 2=passive
        except Exception as e:
            return f"checkpoint error: {e}"

    def health_check(self) -> dict:
        """Check if the database is accessible and has expected structure."""
        info = {"path": self.db_path, "exists": os.path.exists(self.db_path), "readable": False}
        if not info["exists"]:
            return info
        try:
            conn = self.connect()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            info["readable"] = True
            info["tables"] = [t[0] for t in tables]
            info["wal_size"] = _get_wal_size(self.db_path)
            conn.close()
        except Exception as e:
            info["error"] = str(e)
        return info


# Module-level pool instances — create on first use
_pools: dict[str, DatabasePool] = {}
_pool_lock = threading.Lock()


def get_db(name: str) -> DatabasePool:
    """Get or create a database pool by name.

    Args:
        name: Pool identifier (e.g., 'trades', 'trading')

    Returns:
        DatabasePool instance
    """
    if name not in _pools:
        with _pool_lock:
            if name not in _pools:
                # Resolve path relative to trading system test root
                trade_root = Path(__file__).resolve().parents[1]
                db_path = trade_root / "database"
                db_path.mkdir(parents=True, exist_ok=True)
                if name == "trades":
                    db_file = db_path / "trades.db"
                elif name == "bars":
                    db_file = db_path / "bars.db"
                else:
                    # Generic — use name as filename
                    db_file = db_path / f"{name}.db"
                _pools[name] = DatabasePool(db_file)
    return _pools[name]


def _get_wal_size(db_path: str) -> int:
    """Return WAL file size in bytes, or 0 if no WAL exists."""
    wal_path = db_path + "-wal"
    try:
        return os.path.getsize(wal_path)
    except (OSError, FileNotFoundError):
        return 0
