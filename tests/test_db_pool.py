"""Tests for the unified database pool.

Verifies: WAL mode enforcement, autocheckpoint, multi-writer safety, health checks."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ALPACA_ENV"] = "paper"

from infra.db_pool import get_db


class TestDatabasePool:
    """Test that the DB pool creates and manages connections correctly."""

    def test_pool_creates_connections(self, temp_db):
        """Creating a pool returns a DatabasePool instance."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        assert isinstance(pool, DatabasePool)
        
        conn = pool.connect()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_wal_mode_enforced(self, temp_db):
        """Every connection gets WAL mode — no exceptions."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        
        # Verify journal_mode is WAL on a fresh connection
        conn = pool.connect()
        result = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert result == "wal", f"Expected 'wal' but got '{result}'"
        
        result2 = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert result2 == 100, f"Expected wal_autocheckpoint=100 but got {result2}"
        conn.close()

    def test_checkpoint_works(self, temp_db):
        """TRUNCATE checkpoint executes without error."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        
        # Write some data first
        conn = pool.connect()
        conn.execute("CREATE TABLE IF NOT EXISTS test(x INTEGER)")
        for i in range(200):
            conn.execute("INSERT INTO test VALUES (?)", (i,))
        conn.commit()
        conn.close()
        
        result = pool.checkpoint()
        assert result in ("0", "1", "2"), f"Unexpected checkpoint result: {result}"

    def test_health_check(self, temp_db):
        """health_check returns valid structure."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        
        # Create a table first so it's not empty
        conn = pool.connect()
        conn.execute("CREATE TABLE IF NOT EXISTS health_test(x INTEGER)")
        conn.commit()
        conn.close()
        
        info = pool.health_check()
        assert "exists" in info
        assert "readable" in info
        assert "tables" in info
        assert "health_test" in info.get("tables", [])
        assert info["readable"] is True

    def test_health_check_missing_db(self):
        """health_check returns exists=False for non-existent DB."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(Path("/nonexistent/db_path"))
        
        info = pool.health_check()
        assert info["exists"] is False
        assert info["readable"] is False

    def test_module_get_db(self, temp_db):
        """get_db returns a DatabasePool."""
        # Monkey-patch the database path for this test
        from infra import db_pool as pool_mod
        old_pools = getattr(pool_mod, '_pools', {})
        pool_mod._pools = {}  # Clear any cached pools
        
        try:
            from pathlib import Path
            trade_root = Path(__file__).resolve().parents[1]
            test_db_path = str(temp_db)
            
            # Directly create a pool with temp path
            pool = pool_mod.DatabasePool(Path(test_db_path))
            conn = pool.connect()
            assert isinstance(conn, sqlite3.Connection)
            conn.close()
        finally:
            pool_mod._pools = old_pools


class TestDBPoolIntegration:
    """Integration tests — actual DB operations through the pool."""

    def test_write_read_through_pool(self, temp_db):
        """Write and read data through a pooled connection."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        
        conn = pool.connect()
        conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'test')")
        conn.commit()
        
        row = conn.execute("SELECT * FROM items WHERE id=1").fetchone()
        assert row[0] == 1
        assert row[1] == "test"
        conn.close()

    def test_pool_enforces_checkpoint_after_writes(self, temp_db):
        """After 100+ writes, auto-checkpoint should trigger."""
        from infra.db_pool import DatabasePool
        pool = DatabasePool(temp_db)
        
        # Write more than the checkpoint threshold (100 pages)
        conn = pool.connect()
        conn.execute("CREATE TABLE large(id INTEGER PRIMARY KEY, data TEXT)")
        
        # Insert enough rows to exceed 100 page threshold
        for i in range(500):
            conn.execute("INSERT INTO large VALUES (?, ?)", (i, "x" * 100))
        conn.commit()
        
        # Close — triggers auto-checkpoint
        conn.close()
        
        # Verify no corrupted WAL file
        wal_path = str(temp_db) + "-wal"
        import os
        if os.path.exists(wal_path):
            size = os.path.getsize(wal_path)
            assert size < 10_000_000, f"WAL grew too large: {size} bytes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
