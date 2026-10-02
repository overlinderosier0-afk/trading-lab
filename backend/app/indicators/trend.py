"""Indicateurs de tendance : SMA, EMA (numpy, sans dépendance lourde)."""

from __future__ import annotations

import numpy as np


def _as_float(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def sma(values, period: int) -> np.ndarray:
    """Moyenne mobile simple. NaN sur les `period - 1` premières valeurs."""
    x = _as_float(values)
    n = len(x)
    out = np.full(n, np.nan)
    if period > 0 and n >= period:
        cumsum = np.cumsum(np.insert(x, 0, 0.0))
        out[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values, period: int) -> np.ndarray:
    """Moyenne mobile exponentielle (k = 2/(period+1), amorcée par la SMA).

    NaN sur les `period - 1` premières valeurs.
    """
    x = _as_float(values)
    n = len(x)
    out = np.full(n, np.nan)
    if period > 0 and n >= period:
        k = 2.0 / (period + 1)
        out[period - 1] = float(np.mean(x[:period]))
        for i in range(period, n):
            out[i] = x[i] * k + out[i - 1] * (1.0 - k)
    return out
