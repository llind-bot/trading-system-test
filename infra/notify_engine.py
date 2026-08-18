"""Error notification pipeline for the trading system.

Sends Telegram notifications on critical events.
Rate-limited to prevent spam (max 1 per event type per hour).
Dry-run mode when ALPACA_ENV=paper — logs but doesn't send.
"""

import os
import time
from collections import defaultdict
from pathlib import Path

# Lazy import to avoid dependency issues during testing
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class NotifyEngine:
    """Rate-limited Telegram notification engine."""

    def __init__(self):
        self._last_sent: dict[str, float] = defaultdict(float)
        self._cooldown_seconds = 3600  # 1 hour per event type
        self.enabled = False
        self.bot_token = ""
        self.chat_id = ""

    def initialize(self):
        """Load config and determine if notifications should be active."""
        trade_root = Path(__file__).resolve().parents[1]
        env_path = trade_root / "config" / ".env"
        
        if not env_path.exists():
            return  # No config — nothing to do
        
        # Read .env for bot token and chat ID
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    self.bot_token = line.split("=", 1)[1].strip()
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    self.chat_id = line.split("=", 1)[1].strip()
        
        # Always disabled in paper mode (no real notifications)
        env_val = os.environ.get("ALPACA_ENV", "")
        if "paper" in env_val:
            self.enabled = False  # Dry-run: log only
        else:
            self.enabled = True
        
        if not HAS_REQUESTS or not self.bot_token or not self.chat_id:
            self.enabled = False

    def notify(self, event_type: str, message: str, severity: str = "WARNING"):
        """Send a notification (rate-limited).
        
        Args:
            event_type: Unique identifier for this event type (e.g., 'engine_crash', 'db_corruption')
            message: Human-readable description
            severity: CRITICAL | WARNING | INFO
        """
        if not self.bot_token or not self.chat_id:
            return  # Config not loaded
        
        # Rate limiting
        now = time.time()
        last = self._last_sent[event_type]
        if now - last < self._cooldown_seconds:
            return  # Still in cooldown
        
        self._last_sent[event_type] = now
        
        # Format notification
        emoji = "🔴" if severity == "CRITICAL" else "🟡" if severity == "WARNING" else "ℹ️"
        text = f"{emoji} *{severity}* — {event_type}\n\n{message}"
        
        if self.enabled:
            self._send_telegram(text)
        else:
            # Dry-run: log it instead
            print(f"[NOTIFY dry-run] {emoji} {severity}: {event_type} — {message}")

    def _send_telegram(self, text: str):
        """POST to Telegram Bot API."""
        if not HAS_REQUESTS:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }, timeout=5)
        except Exception:
            pass  # Don't break the engine if notification fails


# Module-level singleton instance
_engine = NotifyEngine()


def get_notify():
    """Get the global notify engine instance."""
    _engine.initialize()
    return _engine
