"""Indicateurs de momentum : RSI (Wilder), MACD."""

from __future__ import annotations

import numpy as np

from app.indicators.trend import _as_float, ema


def rsi(values, period: int = 14) -> np.ndarray:
    """RSI de Wilder (lissage exponentiel des gains/pertes moyens).

    Première valeur à l'index `period`, NaN avant.
    """
    x = _as_float(values)
    n = len(x)
    out = np.full(n, np.nan)
    if period <= 0 or n <= period:
        return out
    delta = np.diff(x)  # n-1 variations
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)

    def _rsi_value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    avg_gain = float(np.mean(gain[:period]))
    avg_loss = float(np.mean(loss[:period]))
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, n):
        # gain[i-1] = variation qui se termine à l'index i
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def macd(
    values, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD : (ligne MACD, ligne de signal, histogramme).

    La ligne de signal est l'EMA de la ligne MACD sur sa partie valide.
    """
    x = _as_float(values)
    n = len(x)
    macd_line = ema(x, fast) - ema(x, slow)  # NaN jusqu'à slow-1
    signal_line = np.full(n, np.nan)
    if n >= slow + signal - 1 and slow > 0:
        valid = macd_line[slow - 1:]
        signal_line[slow - 1:] = ema(valid, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
