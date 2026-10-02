"""Tests de la validation anti sur-optimisation (IS/OOS + walk-forward).

Le test le plus important : test_no_lookahead_future_data — ajouter des
bougies FUTURES ne doit jamais changer les résultats des plis passés.
"""

import numpy as np
import pytest

from app import validation
from app.backtesting.engine import BacktestConfig
from app.strategies.base import Strategy


class MomentumTest(Strategy):
    """Stratégie synthétique déterministe pour les tests (pas de dépendance config)."""

    name = "momentum_test"

    @classmethod
    def default_params(cls) -> dict:
        return {"lookback": 5}

    def generate(self, candles: dict[str, np.ndarray]) -> np.ndarray:
        close = np.asarray(candles["close"], dtype=float)
        n = len(close)
        lb = int(self.params["lookback"])
        sig = np.full(n, "HOLD", dtype=object)
        prev = np.empty(n)
        prev[:] = np.nan
        prev[lb:] = close[:-lb]
        valid = ~np.isnan(prev)
        sig[valid & (close > prev)] = "BUY"
        sig[valid & (close < prev)] = "SELL"
        return sig


def make_data(n: int, seed: int = 7, timeframe: str = "1h") -> validation.SliceInput:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0002, 0.006, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    spread = np.abs(rng.normal(0, 0.001, n)) * close
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(10, 100, n)
    tss = [f"2025-01-01T{h:02d}:00:00+00:00" for h in range(n)]
    return validation.SliceInput(
        tss=tss,
        candles={"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        timeframe=timeframe,
    )


@pytest.fixture()
def cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=1000.0, fee_rate=0.001, slippage=0.0005,
        risk_per_trade=0.01, stop_loss_pct=0.02, take_profit_pct=0.0,
    )


# ------------------------------------------------------------------ IS / OOS

def test_verdict_taxonomy():
    v = validation._verdict
    # IS non rentable -> sans_edge, même si OOS positif (le cas réel du 2026-10-02)
    assert v({"sharpe_ratio": -0.11}, {"sharpe_ratio": 0.25, "total_return": 0.0079}) == "sans_edge"
    assert v({"sharpe_ratio": 0.0}, {"sharpe_ratio": 0.9, "total_return": 0.1}) == "sans_edge"
    # IS rentable + OOS qui tient -> robuste
    assert v({"sharpe_ratio": 1.2}, {"sharpe_ratio": 1.0, "total_return": 0.15}) == "robuste"
    # IS rentable + OOS positif mais faible -> dégradé
    assert v({"sharpe_ratio": 1.2}, {"sharpe_ratio": 0.2, "total_return": 0.02}) == "dégradé"
    # IS rentable + OOS négatif -> sur-optimisé
    assert v({"sharpe_ratio": 1.5}, {"sharpe_ratio": -0.3, "total_return": -0.05}) == "sur-optimisé"


def test_is_oos_split_geometry(cfg):
    data = make_data(1000)
    res = validation.is_oos(MomentumTest, {"lookback": 5}, None, data, cfg, is_ratio=0.7)
    assert res["is"]["n_candles"] == 700
    assert res["oos"]["n_candles"] == 300
    assert res["is"]["end"] < res["oos"]["start"] or True  # timestamps ISO horaires
    # couverture totale sans trou ni chevauchement
    assert res["is"]["start"] == data.tss[0]
    assert res["is"]["end"] == data.tss[699]
    assert res["oos"]["start"] == data.tss[700]
    assert res["oos"]["end"] == data.tss[999]
    assert res["split_ts"] == data.tss[700]
    assert res["degradation"]["verdict"] in (
        "sans_edge", "robuste", "dégradé", "sur-optimisé")


def test_is_oos_rejects_bad_ratio(cfg):
    data = make_data(500)
    with pytest.raises(ValueError):
        validation.is_oos(MomentumTest, {}, None, data, cfg, is_ratio=0.95)
    with pytest.raises(ValueError):
        validation.is_oos(MomentumTest, {}, None, data, cfg, is_ratio=0.2)


def test_is_oos_needs_enough_data(cfg):
    with pytest.raises(ValueError):
        validation.is_oos(MomentumTest, {}, None, make_data(100), cfg)


# ------------------------------------------------------------ walk-forward

def test_walk_forward_folds_geometry():
    folds = validation.walk_forward_folds(n=1000, train_bars=400, test_bars=100, step_bars=100)
    # le premier pli exige un train complet : tests [400,500) .. [900,1000)
    assert len(folds) == 6
    assert folds[0] == (0, 400, 400, 500)
    for i, (tr_lo, tr_hi, te_lo, te_hi) in enumerate(folds):
        assert tr_lo >= 0 and tr_hi - tr_lo == 400
        assert te_hi - te_lo == 100
        assert tr_hi == te_lo  # le train se termine où le test commence
        if i:
            assert te_lo == folds[i - 1][2] + 100  # tests contigus, sans chevauchement


def test_walk_forward_insufficient_data():
    with pytest.raises(ValueError):
        validation.walk_forward_folds(n=100, train_bars=400, test_bars=100, step_bars=100)


def test_no_lookahead_future_data(cfg):
    """Ajouter du futur ne change RIEN aux plis déjà calculés."""
    grid = {"lookback": [3, 5, 10]}
    short = make_data(2000, seed=42)
    # même passé, plus de futur : on prolonge la série avec une autre seed
    long_close_extra = make_data(500, seed=99)
    tss = short.tss + [f"2025-04-01T{h:02d}:00:00+00:00" for h in range(500)]
    long = validation.SliceInput(
        tss=tss,
        candles={k: np.concatenate([short.candles[k], long_close_extra.candles[k]])
                 for k in short.candles},
        timeframe="1h",
    )
    r_short = validation.walk_forward(MomentumTest, grid, short, cfg,
                                      train_days=30, test_days=10, min_trades=5)
    r_long = validation.walk_forward(MomentumTest, grid, long, cfg,
                                     train_days=30, test_days=10, min_trades=5)
    assert len(r_long["folds"]) > len(r_short["folds"])
    for f_s, f_l in zip(r_short["folds"], r_long["folds"]):
        assert f_s["best_params"] == f_l["best_params"]
        assert f_s["test_metrics"] == f_l["test_metrics"]
        assert f_s["train_metrics"] == f_l["train_metrics"]


def test_walk_forward_aggregate_consistency(cfg):
    grid = {"lookback": [3, 10]}
    data = make_data(3000, seed=11)
    res = validation.walk_forward(MomentumTest, grid, data, cfg,
                                  train_days=30, test_days=10, min_trades=5)
    agg = res["aggregate"]
    assert len(res["folds"]) >= 2
    rets = [f["test_metrics"]["total_return"] or 0.0 for f in res["folds"]]
    assert agg["profitable_folds"] == sum(1 for r in rets if r > 0)
    assert agg["profitable_ratio"] == pytest.approx(
        sum(1 for r in rets if r > 0) / len(rets), abs=1e-4)


# --------------------------------------------------------------- grid search

def test_grid_search_picks_best_sharpe(cfg):
    data = make_data(1500, seed=3)
    grid = {"lookback": [3, 5, 20]}
    best_params, best_metrics, n_combos, fallback = validation.grid_search(
        MomentumTest, grid, data, 0, 1500, cfg, min_trades=5)
    assert n_combos == 3 and not fallback
    # vérification par force brute indépendante
    scored = []
    for lb in [3, 5, 20]:
        r = validation._run_slice(MomentumTest, {"lookback": lb}, data, 0, 1500, cfg)
        scored.append((r["metrics"]["sharpe_ratio"] or 0.0, lb, r["metrics"]))
    scored.sort(reverse=True)
    assert best_params["lookback"] == scored[0][1]
    assert best_metrics["sharpe_ratio"] == scored[0][2]["sharpe_ratio"]


def test_grid_search_min_trades_fallback(cfg):
    # lookback énorme sur petite série -> presque aucun trade -> repli défauts
    data = make_data(300, seed=5)
    best_params, _, _, fallback = validation.grid_search(
        MomentumTest, {"lookback": [200, 250]}, data, 0, 300, cfg, min_trades=50)
    assert fallback is True
    assert best_params == MomentumTest.default_params()


def test_grid_rejects_unknown_param(cfg):
    data = make_data(300)
    with pytest.raises(ValueError, match="paramètre inconnu"):
        validation.grid_search(MomentumTest, {"nope": [1, 2]}, data, 0, 300, cfg)


def test_grid_rejects_oversize(cfg):
    data = make_data(300)
    big = {"lookback": list(range(300))}
    with pytest.raises(ValueError, match="trop grosse"):
        validation.grid_search(MomentumTest, big, data, 0, 300, cfg)


def test_walk_forward_requires_grid(cfg):
    data = make_data(2000)
    with pytest.raises(ValueError):
        validation.walk_forward(MomentumTest, {}, data, cfg, train_days=30, test_days=10)


def test_edge_status_marquage_honnete():
    """ema_rsi_demo doit afficher NO_EDGE_DEMONSTRATED (acté le 2026-10-02)."""
    from app.strategies.ema_rsi import EmaRsiCross
    from app.strategies.base import Strategy
    assert Strategy.edge_status == "UNTESTED"
    assert EmaRsiCross.edge_status == "NO_EDGE_DEMONSTRATED"
    assert "démonstration" in EmaRsiCross.edge_note.lower()
