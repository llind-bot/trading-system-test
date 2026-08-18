"""Strategy base class — all strategies inherit from this.

Each strategy:
- Receives OHLCV bar data
- Outputs a signal: BUY / SELL / HOLD with confidence score (0-1)
- Is registered to the StrategyRegistry for execution
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategyResult:
    """Output from a strategy evaluation."""
    signal: Signal
    confidence: float          # 0.0 - 1.0
    reason: str                # why this signal was generated
    entry_price: float = 0.0
    stop_loss_price: float = 0.0

    @property
    def is_tradable(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL)


class BaseStrategy(ABC):
    """All strategies must implement evaluate()."""

    NAME: str = "BaseStrategy"
    DESCRIPTION: str = ""

    @abstractmethod
    def evaluate(self, bars: list[dict], params: dict) -> StrategyResult:
        """Evaluate OHLCV bars and return a signal.

        Args:
            bars: list of OHLCV dicts (most recent first or chronological)
            params: strategy-specific parameters from config

        Returns:
            StrategyResult with signal, confidence, reason
        """
        pass

    def warm_up_bars_needed(self) -> int:
        """Minimum number of bars needed before this strategy can produce signals."""
        return 20  # default
