"""Indicateurs de volatilité : Bandes de Bollinger, ATR (Wilder)."""

from __future__ import annotations

import numpy as np

from app.indicators.trend import _as_float, sma


def bollinger_bands(
    values, period: int = 20, std: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(bande haute, bande médiane = SMA, bande basse). Écart-type population."""
    x = _as_float(values)
    n = len(x)
    mid = sma(x, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    if period > 0 and n >= period:
        cumsum = np.cumsum(np.insert(x, 0, 0.0))
        cumsum2 = np.cumsum(np.insert(x * x, 0, 0.0))
        mean = (cumsum[period:] - cumsum[:-period]) / period
        var = (cumsum2[period:] - cumsum2[:-period]) / period - mean ** 2
        sd = np.sqrt(np.maximum(var, 0.0))
        upper[period - 1:] = mean + std * sd
        lower[period - 1:] = mean - std * sd
    return upper, mid, lower


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """Average True Range (lissage de Wilder). Première valeur à l'index `period`."""
    h, lo, c = _as_float(high), _as_float(low), _as_float(close)
    n = len(c)
    out = np.full(n, np.nan)
    if period <= 0 or n <= period or not (len(h) == n and len(lo) == n):
        return out
    prev_close = np.roll(c, 1)
    prev_close[0] = c[0]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close)))
    out[period] = float(np.mean(tr[1:period + 1]))
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out
