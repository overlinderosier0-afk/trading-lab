"""Signal Lab — évaluation observationnelle : fonctions pures.

Aucun accès réseau ni base ici : les bougies 1m et l'horloge sont injectées.
Référence normative : §2 du prompt (docs/signal-lab-evaluation.md).

Unité d'analyse = (signal, horizon) : aucune fonction ne mélange des horizons.
NEUTRAL n'est jamais stocké donc jamais évalué (return indéfini sans direction).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median

CALC_VERSION = 1

# Tranches de score, §5 (bornes testées une par une).
SCORE_RANGES = ["0-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90-100"]


def _as_aware(dt: datetime | None) -> datetime | None:
    """Normalise un datetime naïf en UTC (défensif : la base renvoie de l'aware)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def signal_evaluability(signal, max_lag_seconds: float) -> tuple[bool, str | None]:
    """Unique source de vérité de l'évaluabilité d'un signal.

    Utilisée par le worker (création des PENDING, traitement des dus) ET par
    toute l'API (stats, summary, breakdown, listes, compteurs) : un même
    signal reçoit toujours le même verdict des deux côtés.

    `signal` : dict-like avec `direction`, `entry_timestamp`, `created_at`
    (datetimes aware de préférence ; les naïfs sont lus comme UTC).

    Retourne (True, None) si évaluable, sinon (False, motif) avec motif ∈
    {'neutral_direction', 'entry_timestamp_not_derivable', 'entry_lag'} :
    - NEUTRAL : aucun return défini (jamais évalué, jamais compté) ;
    - entry_timestamp NULL : entrée non dérivable (backfill impossible) ;
    - entry_lag = created_at − entry_timestamp > max_lag_seconds : une partie
      de la fenêtre d'évaluation précède la création du signal (retard du
      moteur, insertion a posteriori…) → mesure non propre.
    """
    direction = signal.get("direction")
    if direction not in ("BUY", "SELL"):
        return False, "neutral_direction"
    entry_timestamp = _as_aware(signal.get("entry_timestamp"))
    if entry_timestamp is None:
        return False, "entry_timestamp_not_derivable"
    created_at = _as_aware(signal.get("created_at"))
    if created_at is None:
        # Sans created_at on ne peut pas borner le retard : conservateur.
        return False, "entry_lag"
    lag = (created_at - entry_timestamp).total_seconds()
    if lag > max_lag_seconds:
        return False, "entry_lag"
    return True, None


def _to_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def round_up_to_minute(ts: datetime) -> datetime:
    """Plafond à la minute : une minute pile reste inchangée.

    La bougie 1m qui ouvre exactement à cet instant ne contient aucun prix
    antérieur à l'entrée : l'inclure ne crée pas de look-ahead.
    """
    base = ts.replace(second=0, microsecond=0)
    if ts <= base:
        return base
    return base + timedelta(minutes=1)


def select_window_candles(
    candles_1m: list[dict],
    entry_timestamp: datetime,
    horizon_minutes: int,
) -> list[dict]:
    """Fenêtre anti look-ahead d'un horizon.

    Ne retient que les bougies 1m avec open_time >= entry arrondi au plafond
    de la minute ET close_time <= T_end (= entry + horizon). La bougie qui
    contient l'instant d'entrée est donc exclue (son open est antérieur).
    """
    t_end_ms = _to_ms(entry_timestamp + timedelta(minutes=horizon_minutes))
    start_ms = _to_ms(round_up_to_minute(entry_timestamp))
    kept = [
        c for c in candles_1m
        if c["open_ms"] >= start_ms and c["close_ms"] <= t_end_ms
    ]
    kept.sort(key=lambda c: c["open_ms"])
    return kept


def compute_evaluation(
    entry_price: float,
    direction: str,
    candles_1m: list[dict],
    horizon_minutes: int,
    entry_timestamp: datetime,
) -> dict | None:
    """Évalue un (signal, horizon). Retourne None si aucune bougie évaluable.

    MFE >= 0 >= MAE par construction (l'entrée est le point de départ).
    Invariants : MAE <= min(0, return) et MFE >= max(0, return).
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError(f"direction non évaluable : {direction!r}")
    if not entry_price:
        return None
    window = select_window_candles(candles_1m, entry_timestamp, horizon_minutes)
    if not window:
        return None
    last = window[-1]
    exit_price = float(last["close"])
    exit_timestamp = _from_ms(int(last["close_ms"]))
    if direction == "BUY":
        return_pct = (exit_price - entry_price) / entry_price * 100.0
    else:
        return_pct = (entry_price - exit_price) / entry_price * 100.0
    direction_correct = True if return_pct > 0 else (False if return_pct < 0 else None)
    highs = [float(c["high"]) for c in window]
    lows = [float(c["low"]) for c in window]
    if direction == "BUY":
        mfe_pct = (max(highs) - entry_price) / entry_price * 100.0
        mae_pct = (min(lows) - entry_price) / entry_price * 100.0
    else:
        mfe_pct = (entry_price - min(lows)) / entry_price * 100.0
        mae_pct = (entry_price - max(highs)) / entry_price * 100.0
    return {
        "exit_price": exit_price,
        "exit_timestamp": exit_timestamp,
        "return_pct": return_pct,
        "direction_correct": direction_correct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "candles_used": len(window),
        "calc_version": CALC_VERSION,
    }


def net_return_pct(gross_pct: float | None, cost_bps: float) -> float | None:
    """Return net = brut − coût aller-retour. Calculé à la lecture, jamais stocké."""
    if gross_pct is None:
        return None
    return gross_pct - cost_bps / 100.0


def paper_pnl_usdt(return_pct: float | None, stake_usdt: float) -> float | None:
    """P&L virtuel SIMULATED : ce n'est pas une transaction."""
    if return_pct is None:
        return None
    return stake_usdt * return_pct / 100.0


def _finite_or_none(v) -> float | None:
    """Garantit : jamais NaN ni Infinity dans l'API (§2)."""
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def aggregate_stats(rows: list[dict]) -> dict:
    """Stats sur des évaluations COMPLETED d'UN SEUL horizon.

    rows : dicts avec return_pct, direction_correct, mfe_pct, mae_pct.
    Valeurs indéfinies → None (jamais NaN/Infinity).
    """
    n = len(rows)
    wins = sum(1 for r in rows if r.get("direction_correct") is True)
    losses = sum(1 for r in rows if r.get("direction_correct") is False)
    neutrals = n - wins - losses
    rets = [r["return_pct"] for r in rows if r.get("return_pct") is not None]
    mfes = [r["mfe_pct"] for r in rows if r.get("mfe_pct") is not None]
    maes = [r["mae_pct"] for r in rows if r.get("mae_pct") is not None]
    gross_profit = sum(r for r in rets if r > 0)
    gross_loss = sum(r for r in rets if r < 0)  # négatif ou 0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss else None
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "neutrals": neutrals,
        "win_rate": _finite_or_none(wins / n) if n else None,
        "loss_rate": _finite_or_none(losses / n) if n else None,
        "neutral_rate": _finite_or_none(neutrals / n) if n else None,
        "average_return": _finite_or_none(sum(rets) / len(rets)) if rets else None,
        "median_return": _finite_or_none(median(rets)) if rets else None,
        "total_return": _finite_or_none(sum(rets)) if rets else None,
        "profit_factor": _finite_or_none(profit_factor),
        "average_mfe": _finite_or_none(sum(mfes) / len(mfes)) if mfes else None,
        "average_mae": _finite_or_none(sum(maes) / len(maes)) if maes else None,
        "sample_quality": sample_quality(n),
    }


def sample_quality(n: int) -> str:
    """Qualité d'échantillon par (groupe × horizon), §7."""
    if n < 30:
        return "Insufficient sample"
    if n < 100:
        return "Early evidence"
    if n < 300:
        return "Moderate sample"
    return "Large sample"


def score_bucket(score: int) -> str:
    """Tranche de score §5 : 0-39, 40-49, …, 90-100."""
    bounds = [(39, "0-39"), (49, "40-49"), (59, "50-59"), (69, "60-69"),
              (79, "70-79"), (89, "80-89"), (100, "90-100")]
    for hi, label in bounds:
        if score <= hi:
            return label
    return "90-100"
