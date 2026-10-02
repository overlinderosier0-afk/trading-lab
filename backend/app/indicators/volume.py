"""Indicateurs de volume : moyenne mobile du volume."""

from __future__ import annotations

import numpy as np

from app.indicators.trend import sma


def volume_sma(volume, period: int = 20) -> np.ndarray:
    """Volume moyen sur `period` bougies (SMA du volume)."""
    return sma(volume, period)
