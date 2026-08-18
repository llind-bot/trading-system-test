"""Tests for the notification engine.

Verifies: rate limiting, dry-run mode (paper), Telegram API formatting."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ALPACA_ENV"] = "paper"

from infra.notify_engine import NotifyEngine


class TestNotifyEngine:
    """Test the notification engine."""

    def test_dry_run_mode_in_paper(self):
        """In paper mode, notify logs but doesn't send Telegram."""
        engine = NotifyEngine()
        
        # Paper mode — no real sends
        with patch.dict(os.environ, {"ALPACA_ENV": "paper"}):
            engine.initialize()
            
            # Capture stdout for dry-run output
            captured = []
            original_print = print
            
            def capture(text):
                if "[NOTIFY dry-run]" in str(text):
                    captured.append(text)
            
            with patch("builtins.print", side_effect=capture):
                engine.notify("test_event", "test message")
        
        assert len(captured) == 1
        assert "dry-run" in str(captured[0])

    def test_rate_limiting(self, temp_db):
        """Same event type within cooldown is ignored."""
        from infra import notify_engine
        engine = notify_engine.NotifyEngine()
        
        with patch.object(engine, '_send_telegram', return_value=None):
            # First notification should go through (mock)
            engine.initialize()
            
            # Simulate sending first notification
            original_sent = engine._last_sent.get('rate_test', 0)
            engine.notify('rate_test', 'first message')
            first_sent = engine._last_sent['rate_test']
            
            # Immediately try again — should be ignored
            import time
            time.sleep(0.1)  # Small delay but still within cooldown
            engine.notify('rate_test', 'second message (should be ignored)')
            
            second_sent = engine._last_sent['rate_test']
            
            assert first_sent == second_sent, "Rate limiting should prevent duplicate sends"

    def test_different_events_have_separate_limits(self):
        """Different event types have independent rate limits."""
        engine = NotifyEngine()
        
        with patch.object(engine, '_send_telegram', return_value=None):
            engine.initialize()
            
            # Send both events at once
            engine.notify('event_a', 'message a')
            engine.notify('event_b', 'message b')
            
            assert 'event_a' in engine._last_sent
            assert 'event_b' in engine._last_sent


class TestNotifySeverity:
    """Test notification severity levels."""

    def test_severity_emoji(self):
        """Each severity level maps to correct emoji."""
        import io, sys
        
        from unittest.mock import patch
        with patch("infra.notify_engine.NotifyEngine._send_telegram") as mock_send:
            engine = NotifyEngine()
            
            # Capture dry-run output
            old_stdout = sys.stdout
            sys.stdout = captured = io.StringIO()
            
            try:
                engine.initialize()
                
                # CRITICAL
                engine.notify("critical_event", "critical message", severity="CRITICAL")
                output1 = captured.getvalue().strip()
                captured.truncate(0)
                captured.seek(0)
                
                # WARNING  
                engine.notify("warning_event", "warning message", severity="WARNING")
                output2 = captured.getvalue().strip()
            finally:
                sys.stdout = old_stdout
            
            assert "🔴" in output1 or "CRITICAL" in output1
            assert "🟡" in output2 or "WARNING" in output2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
