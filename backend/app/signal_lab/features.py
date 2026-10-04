"""Signal Lab — extraction des caractéristiques (features).

Fonctions pures et sans état : reçoivent des tableaux OHLCV (bougies closes,
ordre chronologique) et retournent un dict de features numériques + lectures
brutes pour l'affichage. Aucune décision directionnelle ici.
"""

from __future__ import annotations

import numpy as np

from app.indicators.momentum import macd, rsi
from app.indicators.trend import ema
from app.indicators.volatility import atr

# Nombre minimal de bougies closes pour un calcul fiable (EMA 200 + marges).
MIN_CANDLES = 230


def _last_valid(values: np.ndarray) -> float:
    """Dernière valeur non-NaN (NaN si aucune)."""
    v = np.asarray(values, dtype=float)
    mask = ~np.isnan(v)
    return float(v[mask][-1]) if mask.any() else float("nan")


def compute_features(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """Calcule toutes les features sur la dernière bougie close.

    Lève ValueError si pas assez de bougies.
    """
    o = np.asarray(open_, dtype=float)
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    n = len(c)
    if n < MIN_CANDLES:
        raise ValueError(
            f"pas assez de bougies : {n} < {MIN_CANDLES} (EMA 200 + marge)"
        )

    ema20 = ema(c, 20)
    ema50 = ema(c, 50)
    ema200 = ema(c, 200)
    rsi14 = rsi(c, 14)
    macd_line, macd_signal, macd_hist = macd(c, 12, 26, 9)
    atr14 = atr(h, lo, c, 14)

    e20, e50, e200 = _last_valid(ema20), _last_valid(ema50), _last_valid(ema200)
    r = _last_valid(rsi14)
    mh = _last_valid(macd_hist)
    ml = _last_valid(macd_line)
    a = _last_valid(atr14)
    last_close = float(c[-1])
    last_open = float(o[-1])
    last_high = float(h[-1])
    last_low = float(lo[-1])

    if not np.isfinite(a) or a <= 0:
        raise ValueError("ATR invalide sur les données fournies")

    # --- Momentum : variation du prix sur 10 bougies, en unités d'ATR
    roc10_atr = float((c[-1] - c[-11]) / a) if n >= 11 else 0.0

    # --- Price action : dernière bougie
    body = last_close - last_open
    body_atr = float(body / a)
    candle_range = last_high - last_low
    upper_wick = last_high - max(last_close, last_open)
    lower_wick = min(last_close, last_open) - last_low
    upper_wick_atr = float(upper_wick / a) if candle_range > 0 else 0.0
    lower_wick_atr = float(lower_wick / a) if candle_range > 0 else 0.0

    # Bougies consécutives dans le même sens (max 5 regardées)
    closes = c[-6:]
    ups = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    downs = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    streak = ups - downs  # +5 .. -5

    # --- Structure : plus haut / plus bas sur 60 bougies (hors 5 dernières,
    # pour ne pas prendre un extrême en formation comme référence)
    window = h[-65:-5] if n >= 65 else h[:-5]
    window_l = lo[-65:-5] if n >= 65 else lo[:-5]
    swing_high = float(np.max(window))
    swing_low = float(np.min(window_l))
    dist_res_atr = float((swing_high - last_close) / a)
    dist_sup_atr = float((last_close - swing_low) / a)
    breakout_up = bool(last_close > swing_high)
    breakout_down = bool(last_close < swing_low)

    # --- Volatilité : régime actuel vs médiane (100 dernières valeurs d'ATR%)
    atr_pct_series = atr(h, lo, c, 14) / np.maximum(c, 1e-12) * 100.0
    valid_pct = atr_pct_series[~np.isnan(atr_pct_series)][-100:]
    atr_pct = float(valid_pct[-1])
    median_atr_pct = float(np.median(valid_pct)) if len(valid_pct) else atr_pct
    vol_ratio = float(atr_pct / median_atr_pct) if median_atr_pct > 0 else 1.0

    # Histogramme MACD normalisé (unités d'ATR) pour un poids borné
    macd_hist_atr = float(mh / a) if np.isfinite(mh) else 0.0

    return {
        # bruts (affichage)
        "close": last_close,
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "rsi14": float(r) if np.isfinite(r) else 50.0,
        "macd_hist": float(mh) if np.isfinite(mh) else 0.0,
        "macd_line": float(ml) if np.isfinite(ml) else 0.0,
        "atr": a,
        "atr_pct": atr_pct,
        # normalisés (scoring)
        "ema20_ema50_atr": float((e20 - e50) / a),
        "ema50_ema200_atr": float((e50 - e200) / a),
        "price_ema50_atr": float((last_close - e50) / a),
        "price_ema200_atr": float((last_close - e200) / a),
        "rsi14_norm": float(r) if np.isfinite(r) else 50.0,
        "macd_hist_atr": macd_hist_atr,
        "roc10_atr": roc10_atr,
        "body_atr": body_atr,
        "upper_wick_atr": upper_wick_atr,
        "lower_wick_atr": lower_wick_atr,
        "streak": int(streak),
        "dist_res_atr": dist_res_atr,
        "dist_sup_atr": dist_sup_atr,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "vol_ratio": vol_ratio,
        "volume": float(v[-1]),
    }
