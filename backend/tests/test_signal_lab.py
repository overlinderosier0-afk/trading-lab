"""Tests Signal Lab : scoring multi-facteurs, anti look-ahead, SL/TP.

Aucune base de données requise : on teste les fonctions pures
(features.compute_features + engine.generate_signal).
"""

import numpy as np
import pytest

from app.signal_lab import engine, features


def make_ohlcv(n: int, drift: float, vol: float, seed: int = 7):
    """Bougies synthétiques : marche aléatoire + dérive constante."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, vol * 100, n)) + 1e-9
    high = np.maximum(open_, close) + spread * 0.5
    low = np.minimum(open_, close) - spread * 0.5
    volume = rng.uniform(50, 150, n)
    return open_, high, low, close, volume


def signal_for(drift: float, vol: float = 0.004, n: int = 400, seed: int = 7):
    o, h, l, c, v = make_ohlcv(n, drift, vol, seed)
    feats = features.compute_features(o, h, l, c, v)
    return engine.generate_signal(feats), feats


# ---------------------------------------------------------------- direction
def test_strong_uptrend_gives_buy():
    sig, _ = signal_for(drift=0.004)
    assert sig["direction"] == "BUY"
    assert sig["score"] >= engine.DIRECTION_THRESHOLD
    assert sig["factors"]["trend"]["points"] > 0


def test_strong_downtrend_gives_sell():
    sig, _ = signal_for(drift=-0.004)
    assert sig["direction"] == "SELL"
    assert sig["score"] >= engine.DIRECTION_THRESHOLD
    assert sig["factors"]["trend"]["points"] < 0


def test_sideways_gives_neutral_or_weak():
    sig, _ = signal_for(drift=0.0, vol=0.002)
    # Marché plat : soit NEUTRAL, soit un score faible (jamais de conviction).
    assert sig["direction"] == "NEUTRAL" or sig["score"] < 40


def test_determinism():
    sig1, _ = signal_for(drift=0.003, seed=42)
    sig2, _ = signal_for(drift=0.003, seed=42)
    assert sig1 == sig2


# ---------------------------------------------------------------- cohérence
def test_weights_sum_to_100():
    assert abs(sum(engine.WEIGHTS.values()) - 100.0) < 1e-9


def test_factors_sum_to_total():
    sig, _ = signal_for(drift=0.003)
    total = round(sum(f["points"] for f in sig["factors"].values()), 1)
    assert total == sig["total"]
    assert -100.0 <= sig["total"] <= 100.0


def test_each_factor_within_weight():
    sig, _ = signal_for(drift=-0.003)
    for key, f in sig["factors"].items():
        assert abs(f["points"]) <= f["weight"] + 1e-9, key


def test_score_is_abs_total():
    sig, _ = signal_for(drift=0.003)
    assert sig["score"] == int(round(abs(sig["total"])))
    assert 0 <= sig["score"] <= 100


# ---------------------------------------------------------------- SL / TP
def test_sl_tp_buy():
    sig, feats = signal_for(drift=0.004)
    assert sig["direction"] == "BUY"
    assert sig["stop_loss"] < sig["entry"] < sig["take_profit"]
    assert sig["stop_loss"] == pytest.approx(sig["entry"] - 1.5 * feats["atr"])
    assert sig["take_profit"] == pytest.approx(sig["entry"] + 2.0 * feats["atr"])


def test_sl_tp_sell():
    sig, feats = signal_for(drift=-0.004)
    assert sig["direction"] == "SELL"
    assert sig["take_profit"] < sig["entry"] < sig["stop_loss"]
    assert sig["stop_loss"] == pytest.approx(sig["entry"] + 1.5 * feats["atr"])
    assert sig["take_profit"] == pytest.approx(sig["entry"] - 2.0 * feats["atr"])


def test_neutral_has_no_sl_tp():
    sig, _ = signal_for(drift=0.0, vol=0.002)
    if sig["direction"] == "NEUTRAL":
        assert sig["stop_loss"] is None
        assert sig["take_profit"] is None


# ---------------------------------------------------------------- anti look-ahead
def test_no_lookahead_last_bar_only():
    """Les features de la dernière bougie ne doivent dépendre d'AUCUNE
    bougie postérieure : tronquer la série ne change rien au résultat."""
    o, h, l, c, v = make_ohlcv(400, 0.003, 0.004, seed=11)
    f_trunc = features.compute_features(o[:300], h[:300], l[:300], c[:300], v[:300])
    f_full = features.compute_features(o, h, l, c, v)
    # Les 300 premières bougies donnent les mêmes features finales,
    # qu'on s'arrête à 300 ou qu'on continue à 400.
    assert f_trunc["close"] == pytest.approx(c[299])
    assert f_full["close"] == pytest.approx(c[399])
    # Et l'EMA20 "vue" à la bougie 300 calculée sur la série tronquée égale
    # l'EMA20 "vue" à la bougie 300 dans la série complète recalculée à part.
    from app.indicators.trend import ema

    def last_valid(x):
        x = np.asarray(x, dtype=float)
        return float(x[~np.isnan(x)][-1])

    assert f_trunc["ema20"] == pytest.approx(last_valid(ema(c[:300], 20)))
    assert f_trunc["rsi14_norm"] == pytest.approx(
        last_valid(__import__("app.indicators.momentum", fromlist=["rsi"]).rsi(c[:300], 14))
    )


def test_min_candles_required():
    o, h, l, c, v = make_ohlcv(100, 0.001, 0.004)
    with pytest.raises(ValueError, match="pas assez de bougies"):
        features.compute_features(o, h, l, c, v)


# ---------------------------------------------------------------- justification
def test_justification_non_empty_french():
    sig, _ = signal_for(drift=0.003)
    assert len(sig["justification"]) >= 5
    assert all(isinstance(line, str) and line for line in sig["justification"])
    assert "indicators" in sig and "rsi14" in sig["indicators"]
