"""Single source of truth for all API credentials.

Every module that needs API access imports load_credentials() from here.
Credentials are stored in config/.env — never hardcoded anywhere.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / ".env"


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key: str
    secret_key: str
    paper: bool = True
    base_url: str = ""
    stock_stream_url: str = ""
    crypto_stream_url: str = ""

    @property
    def effective_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        # Default to paper-api for paper trading, live API otherwise
        return (
            "https://paper-api.alpaca.markets" if self.paper
            else "https://api.alpaca.markets"
        )

    @property
    def effective_stock_stream_url(self) -> str:
        if self.stock_stream_url:
            return self.stock_stream_url
        return "wss://stream.data.alpaca.markets/v2/sip"

    @property
    def effective_crypto_stream_url(self) -> str:
        if self.crypto_stream_url:
            return self.crypto_stream_url
        return "wss://stream.data.alpaca.markets/v1beta3/crypto/us"


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class AllCredentials:
    alpaca: AlpacaCredentials
    telegram: TelegramCredentials


def load_credentials() -> AllCredentials:
    """Load ALL credentials from config/.env. Single point of truth."""
    env = _load_env_file(_CONFIG_PATH)

    # Validate required fields
    required = [
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
    ]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise EnvironmentError(
            f"Missing credentials in {_CONFIG_PATH}: {', '.join(missing)}"
        )

    # Check environment variable overrides first (higher priority)
    api_key = os.environ.get("ALPACA_API_KEY", env.get("ALPACA_API_KEY"))
    secret_key = os.environ.get("ALPACA_SECRET_KEY", env.get("ALPACA_SECRET_KEY"))
    paper_str = os.environ.get("ALPACA_PAPER", env.get("ALPACA_PAPER", "true"))
    base_url = os.environ.get("ALPACA_BASE_URL", env.get("ALPACA_BASE_URL"))
    stock_stream_url = os.environ.get("ALPACA_STOCK_STREAM_URL", env.get("ALPACA_STOCK_STREAM_URL"))
    crypto_stream_url = os.environ.get("ALPACA_CRYPTO_STREAM_URL", env.get("ALPACA_CRYPTO_STREAM_URL"))
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", env.get("TELEGRAM_BOT_TOKEN"))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", env.get("TELEGRAM_CHAT_ID"))

    if not all([api_key, secret_key, bot_token, chat_id]):
        raise EnvironmentError("Some credentials are missing (checked both .env and env vars)")

    alpaca = AlpacaCredentials(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper_str.lower() == "true",
        base_url=base_url or "",
        stock_stream_url=stock_stream_url or "",
        crypto_stream_url=crypto_stream_url or "",
    )
    telegram = TelegramCredentials(
        bot_token=bot_token,
        chat_id=chat_id,
    )

    creds = AllCredentials(alpaca=alpaca, telegram=telegram)

    # Validate (non-fatal warnings only)
    if creds.alpaca.api_key and creds.alpaca.secret_key:
        try:
            validate_credentials(creds.alpaca)
        except Exception:
            pass  # validation failure is caught downstream

    if creds.telegram.bot_token and creds.telegram.chat_id:
        try:
            validate_telegram(creds.telegram)
        except Exception:
            pass  # validation failure is caught downstream

    return creds


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. No external dependencies."""
    env = {}
    if not path.exists():
        raise FileNotFoundError(f"Credentials file not found: {path}")

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()

    return env


def validate_credentials(alpaca_creds: AlpacaCredentials) -> None:
    """Quick validation — attempt a lightweight API call to verify creds work."""
    try:
        import requests
        resp = requests.get(
            f"{alpaca_creds.base_url}/v2/account",
            headers={
                "APCA-API-KEY-ID": alpaca_creds.api_key,
                "APCA-API-SECRET-KEY": alpaca_creds.secret_key,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            raise ConnectionError(
                f"Alpaca credentials invalid — HTTP {resp.status_code}: {resp.text}"
            )
    except ImportError:
        # requests not available — skip validation
        pass
    except Exception as e:
        raise ConnectionError(f"Alpaca credential validation failed: {e}")


def validate_telegram(telegram_creds: TelegramCredentials) -> None:
    """Verify bot token works by fetching me."""
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{telegram_creds.bot_token}/getMe",
            timeout=5,
        )
        if resp.status_code != 200:
            raise ConnectionError(f"Telegram bot token invalid — HTTP {resp.status_code}")
    except ImportError:
        pass
    except Exception as e:
        raise ConnectionError(f"Telegram credential validation failed: {e}")


if __name__ == "__main__":
    # Quick validation run (for CLI testing)
    creds = load_credentials()
    try:
        validate_credentials(creds.alpaca)
        print("✅ Alpaca credentials validated")
    except Exception as e:
        print(f"⚠️  Alpaca credential warning: {e}", file=sys.stderr)

    try:
        validate_telegram(creds.telegram)
        print("✅ Telegram credentials validated")
    except Exception as e:
        print(f"⚠️  Telegram credential warning: {e}", file=sys.stderr)

    print(f"📋 Paper mode: {creds.alpaca.paper}")
    print(f"📱 Chat ID: {creds.telegram.chat_id}")
