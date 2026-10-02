"""Validation honnête des stratégies : IS/OOS + walk-forward.

But : détecter la sur-optimisation (overfitting). Un backtest optimisé sur tout
l'historique ment presque toujours — la question utile est :
« les paramètres choisis sur le passé tiennent-ils sur des données
qu'ils n'ont jamais vues ? »

Garanties anti-fuite (testées dans backend/tests/test_validation.py) :
- Les signaux sont calculés sur la série COMPLÈTE avant découpage. Les
  indicateurs du projet sont causaux (chaque bougie n'utilise que le passé :
  EMA, RSI Wilder, MACD...), donc ajouter des bougies futures ne change JAMAIS
  les signaux du passé. Aucun look-ahead.
- En walk-forward, la grille de paramètres est optimisée sur le TRAIN
  uniquement (critère : Sharpe max, avec un minimum de trades). Le TEST sert
  uniquement à évaluer les paramètres déjà choisis.
- Chaque segment est backtesté avec un capital frais, frais + slippage inclus,
  exactement comme POST /api/backtests.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import numpy as np

from app.backtesting.engine import BacktestConfig, run_backtest
from app.strategies.base import Strategy

log = logging.getLogger("tradinglab")

BARS_PER_DAY = {"1h": 24, "4h": 6, "1d": 1}
MAX_GRID_COMBOS = 250  # garde-fou : une grille plus grosse = erreur claire, pas un timeout


@dataclass
class SliceInput:
    """Une série complète + tout ce qu'il faut pour la découper."""
    tss: list[str]
    candles: dict[str, np.ndarray]  # open/high/low/close/volume
    timeframe: str


def _run_slice(
    strategy_cls: type[Strategy],
    params: dict,
    data: SliceInput,
    lo: int,
    hi: int,
    cfg: BacktestConfig,
) -> dict:
    """Signaux sur la série complète (causaux), backtest sur [lo, hi)."""
    n = len(data.tss)
    if not (0 <= lo < hi <= n):
        raise ValueError(f"segment invalide [{lo}, {hi}) pour {n} bougies")
    strategy = strategy_cls(dict(params))
    signals = strategy.generate(data.candles)  # causal : pas de fuite du futur
    tss = data.tss[lo:hi]
    result = run_backtest(
        tss,
        data.candles["open"][lo:hi],
        data.candles["high"][lo:hi],
        data.candles["low"][lo:hi],
        data.candles["close"][lo:hi],
        signals[lo:hi],
        cfg,
        timeframe=data.timeframe,
    )
    return {
        "params": strategy.params,
        "metrics": result["metrics"],
        "n_candles": hi - lo,
        "start": data.tss[lo],
        "end": data.tss[hi - 1],
    }


# ---------------------------------------------------------------- IS / OOS

def _verdict(is_m: dict, oos_m: dict) -> str:
    """Heuristique lisible — pas un jugement définitif."""
    is_s = is_m.get("sharpe_ratio") or 0.0
    oos_s = oos_m.get("sharpe_ratio") or 0.0
    oos_r = oos_m.get("total_return") or 0.0
    if is_s <= 0:
        # Cas le plus honnête : l'optimisation n'a rien trouvé de rentable,
        # même sur les données d'entraînement. Pas d'edge à valider.
        return "sans_edge"
    if oos_s >= 0.5 and oos_s >= is_s * 0.7 and oos_r > 0:
        return "robuste"
    if oos_s > 0:
        return "dégradé"
    return "sur-optimisé"


def is_oos(
    strategy_cls: type[Strategy],
    params: dict,
    param_grid: dict[str, list] | None,
    data: SliceInput,
    cfg: BacktestConfig,
    is_ratio: float = 0.7,
    min_trades: int = 20,
) -> dict:
    """Découpe IS (début) / OOS (fin) et compare.

    Avec param_grid : optimisation sur IS, évaluation des meilleurs
    paramètres sur OOS (le cas honnête). Sans : mêmes paramètres des deux côtés.
    """
    n = len(data.tss)
    if n < 200:
        raise ValueError(f"pas assez de bougies pour IS/OOS ({n} < 200)")
    if not 0.5 <= is_ratio <= 0.9:
        raise ValueError("is_ratio doit être entre 0.5 et 0.9")
    split = int(n * is_ratio)

    grid_info: dict | None = None
    if param_grid:
        best_params, best_metrics, n_combos, fallback = grid_search(
            strategy_cls, param_grid, data, 0, split, cfg, min_trades
        )
        grid_info = {
            "n_combos": n_combos,
            "best_params": best_params,
            "is_metrics": best_metrics,
            "fallback_to_defaults": fallback,
        }
        params = best_params

    is_res = _run_slice(strategy_cls, params, data, 0, split, cfg)
    oos_res = _run_slice(strategy_cls, params, data, split, n, cfg)
    return {
        "is_ratio": is_ratio,
        "split_ts": data.tss[split],
        "params": is_res["params"],
        "grid_search": grid_info,
        "is": is_res,
        "oos": oos_res,
        "degradation": {
            "sharpe_is": is_res["metrics"]["sharpe_ratio"],
            "sharpe_oos": oos_res["metrics"]["sharpe_ratio"],
            "return_is": is_res["metrics"]["total_return"],
            "return_oos": oos_res["metrics"]["total_return"],
            "verdict": _verdict(is_res["metrics"], oos_res["metrics"]),
        },
    }


# ------------------------------------------------------- recherche sur grille

def _check_grid(strategy_cls: type[Strategy], param_grid: dict[str, list]) -> list[tuple[str, list]]:
    """Valide la grille : clés = vrais paramètres, valeurs non vides, taille bornée."""
    valid_keys = set(strategy_cls.default_params())
    items = []
    for key, values in param_grid.items():
        if key not in valid_keys:
            raise ValueError(
                f"paramètre inconnu '{key}' pour {strategy_cls.name} "
                f"(attendus : {sorted(valid_keys)})"
            )
        vals = list(values)
        if not vals:
            raise ValueError(f"grille vide pour le paramètre '{key}'")
        items.append((key, vals))
    combos = 1
    for _, vals in items:
        combos *= len(vals)
    if not items:
        raise ValueError("grille vide : fournissez au moins un paramètre à tester")
    if combos > MAX_GRID_COMBOS:
        raise ValueError(
            f"grille trop grosse : {combos} combinaisons (max {MAX_GRID_COMBOS})"
        )
    return items


def grid_search(
    strategy_cls: type[Strategy],
    param_grid: dict[str, list],
    data: SliceInput,
    lo: int,
    hi: int,
    cfg: BacktestConfig,
    min_trades: int = 20,
) -> tuple[dict, dict, int, bool]:
    """Teste chaque combinaison sur [lo, hi). Retourne (params, metrics, n_combos, fallback).

    Critère : Sharpe max parmi les combos avec n_trades >= min_trades.
    Si aucun ne qualifie : repli sur les paramètres par défaut (fallback=True).
    """
    items = _check_grid(strategy_cls, param_grid)
    keys = [k for k, _ in items]
    combos = list(itertools.product(*[v for _, v in items]))

    best_params: dict | None = None
    best_metrics: dict | None = None
    best_sharpe = float("-inf")
    for combo in combos:
        params = dict(zip(keys, combo))
        res = _run_slice(strategy_cls, params, data, lo, hi, cfg)
        m = res["metrics"]
        if m["n_trades"] < min_trades:
            continue
        sharpe = m["sharpe_ratio"] or 0.0
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = res["params"]
            best_metrics = m

    if best_params is None:
        log.warning("grid_search : aucun combo >= %d trades, repli défauts",
                    min_trades, extra={"event": "GRID_FALLBACK"})
        res = _run_slice(strategy_cls, {}, data, lo, hi, cfg)
        return res["params"], res["metrics"], len(combos), True
    return best_params, best_metrics, len(combos), False


# ------------------------------------------------------------ walk-forward

def walk_forward_folds(
    n: int, train_bars: int, test_bars: int, step_bars: int
) -> list[tuple[int, int, int, int]]:
    """Plis roulants : pour chaque pli, TRAIN précède strictement TEST.

    Pli k : test = [k*step, k*step+test), train = [k*step-train, k*step).
    Le premier pli exige train complet (pas de warmup partiel).
    """
    if min(train_bars, test_bars, step_bars) <= 0:
        raise ValueError("train/test/step doivent être > 0")
    # le premier pli exige un train COMPLET : on démarre au premier k valide
    k = (train_bars + step_bars - 1) // step_bars  # ceil(train/step)
    folds = []
    while True:
        test_lo = k * step_bars
        test_hi = test_lo + test_bars
        train_lo = test_lo - train_bars
        if test_hi > n:
            break
        folds.append((train_lo, test_lo, test_lo, test_hi))
        k += 1
    if not folds:
        raise ValueError(
            f"pas assez de données : {n} bougies pour train={train_bars} "
            f"test={test_bars} step={step_bars}"
        )
    return folds


def walk_forward(
    strategy_cls: type[Strategy],
    param_grid: dict[str, list],
    data: SliceInput,
    cfg: BacktestConfig,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
    min_trades: int = 20,
) -> dict:
    """Walk-forward roulant : optimise sur TRAIN, évalue sur TEST, pli par pli."""
    bpd = BARS_PER_DAY.get(data.timeframe)
    if bpd is None:
        raise ValueError(f"timeframe non supporté pour le walk-forward : {data.timeframe}")
    train_bars = train_days * bpd
    test_bars = test_days * bpd
    step_bars = (step_days or test_days) * bpd

    n = len(data.tss)
    folds_idx = walk_forward_folds(n, train_bars, test_bars, step_bars)

    folds = []
    for train_lo, train_hi, test_lo, test_hi in folds_idx:
        best_params, train_metrics, n_combos, fallback = grid_search(
            strategy_cls, param_grid, data, train_lo, train_hi, cfg, min_trades
        )
        test_res = _run_slice(strategy_cls, best_params, data, test_lo, test_hi, cfg)
        folds.append({
            "train_start": data.tss[train_lo],
            "train_end": data.tss[train_hi - 1],
            "test_start": data.tss[test_lo],
            "test_end": data.tss[test_hi - 1],
            "n_combos": n_combos,
            "fallback_to_defaults": fallback,
            "best_params": best_params,
            "train_metrics": train_metrics,
            "test_metrics": test_res["metrics"],
        })

    test_returns = [f["test_metrics"]["total_return"] or 0.0 for f in folds]
    test_sharpes = [f["test_metrics"]["sharpe_ratio"] or 0.0 for f in folds]
    profitable = sum(1 for r in test_returns if r > 0)
    compounded = float(np.prod([1 + r for r in test_returns]) - 1)
    return {
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days or test_days,
        "n_folds": len(folds),
        "folds": folds,
        "aggregate": {
            "profitable_folds": profitable,
            "profitable_ratio": round(profitable / len(folds), 4),
            "avg_test_return": round(float(np.mean(test_returns)), 6),
            "avg_test_sharpe": round(float(np.mean(test_sharpes)), 4),
            "compounded_test_return": round(compounded, 6),
            "best_fold_return": round(float(np.max(test_returns)), 6),
            "worst_fold_return": round(float(np.min(test_returns)), 6),
        },
    }
