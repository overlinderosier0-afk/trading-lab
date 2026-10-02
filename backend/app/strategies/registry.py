"""Registre des stratégies disponibles."""

from app.strategies.base import Signal, Strategy
from app.strategies.ema_rsi import EmaRsiCross

STRATEGIES: dict[str, type[Strategy]] = {
    EmaRsiCross.name: EmaRsiCross,
}

__all__ = ["STRATEGIES", "Signal", "Strategy"]
