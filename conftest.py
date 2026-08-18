import os
import sys
from pathlib import Path

# Force paper mode
os.environ["ALPACA_ENV"] = "paper"

# Add test repo root to path so imports work
TRADE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADE_ROOT))
