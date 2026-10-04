"""Signal Lab — moteur de scoring multi-facteurs.

Le score est une SOMME PONDÉRÉE de facteurs techniques, dans [-100, +100].
Il mesure la force et l'alignement du modèle — PAS une probabilité de gain.
La "probabilité" honnête vient de la calibration sur les signaux résolus
(voir store.calibration_stats).

Poids (total 100) :
    Trend        30  (EMA 20/50/200, prix vs EMA)
    Momentum     25  (RSI 14, MACD histo, variation 10 bougies)
    Price Action 20  (corps/mèches dernière bougie, série directionnelle)
    Structure    15  (breakout, distance support/résistance)
    Volatilité   10  (régime lisible vs chaotique — peut être négatif)

Direction : BUY si total >= seuil, SELL si total <= -seuil, sinon NEUTRAL
(pas de signal forcé — un modèle honnête sait dire "je ne sais pas").
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- réglages
WEIGHTS = {
    "trend": 30.0,
    "momentum": 25.0,
    "price_action": 20.0,
    "structure": 15.0,
    "volatility": 10.0,
}
assert abs(sum(WEIGHTS.values()) - 100.0) < 1e-9

DIRECTION_THRESHOLD = 15.0  # en dessous : NEUTRAL
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.0

FACTOR_LABELS = {
    "trend": "Trend",
    "momentum": "Momentum",
    "price_action": "Price Action",
    "structure": "Structure",
    "volatility": "Volatilité",
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _scale_sep(sep_atr: float, full_at: float = 0.5) -> float:
    """Séparation en unités d'ATR -> facteur 0..1 (1 si >= full_at)."""
    return _clip(abs(sep_atr) / full_at, 0.0, 1.0)


def _score_trend(f: dict) -> tuple[float, list[str]]:
    """Alignement des EMA et position du prix : 30 pts."""
    w = WEIGHTS["trend"]
    pts = 0.0
    notes: list[str] = []

    s1 = _scale_sep(f["ema20_ema50_atr"])
    d1 = math.copysign(1, f["ema20_ema50_atr"]) if f["ema20_ema50_atr"] != 0 else 0
    pts += d1 * 10.0 * s1
    notes.append(
        f"EMA20 {'au-dessus' if d1 > 0 else 'en dessous' if d1 < 0 else 'collée à'} "
        f"EMA50 ({f['ema20_ema50_atr']:+.2f} ATR)"
    )

    s2 = _scale_sep(f["ema50_ema200_atr"])
    d2 = math.copysign(1, f["ema50_ema200_atr"]) if f["ema50_ema200_atr"] != 0 else 0
    pts += d2 * 10.0 * s2
    notes.append(
        f"EMA50 {'au-dessus' if d2 > 0 else 'en dessous' if d2 < 0 else 'collée à'} "
        f"EMA200 ({f['ema50_ema200_atr']:+.2f} ATR)"
    )

    d3 = 1 if f["price_ema50_atr"] > 0 else (-1 if f["price_ema50_atr"] < 0 else 0)
    pts += d3 * 5.0 * _scale_sep(f["price_ema50_atr"], 1.0)
    d4 = 1 if f["price_ema200_atr"] > 0 else (-1 if f["price_ema200_atr"] < 0 else 0)
    pts += d4 * 5.0 * _scale_sep(f["price_ema200_atr"], 1.0)
    notes.append(
        f"Prix {'au-dessus' if d3 > 0 else 'en dessous'} EMA50, "
        f"{'au-dessus' if d4 > 0 else 'en dessous'} EMA200"
    )
    return _clip(pts, -w, w), notes


def _score_momentum(f: dict) -> tuple[float, list[str]]:
    """RSI, MACD, variation récente : 25 pts."""
    w = WEIGHTS["momentum"]
    pts = 0.0
    notes: list[str] = []

    rsi_v = f["rsi14_norm"]
    rsi_pts = _clip((rsi_v - 50.0) / 50.0, -1.0, 1.0) * 12.5
    # Zone extrême : le momentum peut se retourner — on écrête le bonus.
    if rsi_v > 80 or rsi_v < 20:
        rsi_pts *= 0.5
        notes.append(f"RSI {rsi_v:.0f} : zone extrême, momentum écrêté (risque de retournement)")
    else:
        notes.append(f"RSI 14 = {rsi_v:.1f}")
    pts += rsi_pts

    macd_pts = _clip(f["macd_hist_atr"] * 12.5, -6.25, 6.25)
    pts += macd_pts
    notes.append(
        f"MACD histo {'positif' if f['macd_hist_atr'] > 0 else 'négatif'} "
        f"({f['macd_hist_atr']:+.2f} ATR)"
    )

    roc_pts = _clip(f["roc10_atr"] * 6.25, -6.25, 6.25)
    pts += roc_pts
    notes.append(f"Variation 10 bougies : {f['roc10_atr']:+.2f} ATR")
    return _clip(pts, -w, w), notes


def _score_price_action(f: dict) -> tuple[float, list[str]]:
    """Dernière bougie : corps, mèches, série : 20 pts."""
    w = WEIGHTS["price_action"]
    pts = 0.0
    notes: list[str] = []

    body_pts = _clip(f["body_atr"] * 10.0, -8.0, 8.0)
    pts += body_pts
    notes.append(
        f"Dernière bougie {'haussière' if f['body_atr'] > 0 else 'baissière'} "
        f"(corps {f['body_atr']:+.2f} ATR)"
    )

    # Mèches de rejet : borne haute -> pression vendeuse, et inversement.
    if f["upper_wick_atr"] > 0.5 and f["body_atr"] < 0:
        pts -= 4.0
        notes.append(f"Rejet haut (mèche {f['upper_wick_atr']:.2f} ATR) : pression vendeuse")
    elif f["lower_wick_atr"] > 0.5 and f["body_atr"] > 0:
        pts += 4.0
        notes.append(f"Rejet bas (mèche {f['lower_wick_atr']:.2f} ATR) : pression acheteuse")

    streak = f["streak"]
    streak_pts = _clip(streak * 1.6, -8.0, 8.0)
    pts += streak_pts
    if streak != 0:
        notes.append(f"Série directionnelle : {abs(streak)} bougies "
                     f"{'haussières' if streak > 0 else 'baissières'} de suite")
    return _clip(pts, -w, w), notes


def _score_structure(f: dict) -> tuple[float, list[str]]:
    """Breakouts et distances aux extrêmes 60 bougies : 15 pts."""
    w = WEIGHTS["structure"]
    pts = 0.0
    notes: list[str] = []

    if f["breakout_up"]:
        pts += 8.0
        notes.append("Cassure du plus haut 60 bougies (breakout haussier)")
    elif f["breakout_down"]:
        pts -= 8.0
        notes.append("Cassure du plus bas 60 bougies (breakout baissier)")

    # Vent contraire : résistance proche (frein pour BUY), support proche (frein pour SELL)
    if 0 < f["dist_res_atr"] < 0.5:
        pts -= 4.0
        notes.append(f"Résistance très proche ({f['dist_res_atr']:.2f} ATR au-dessus)")
    elif 0 < f["dist_sup_atr"] < 0.5:
        pts += 4.0
        notes.append(f"Support très proche ({f['dist_sup_atr']:.2f} ATR en dessous)")

    if not f["breakout_up"] and not f["breakout_down"]:
        notes.append(
            f"Range 60 bougies : {f['dist_sup_atr']:.1f} ATR au-dessus du support, "
            f"{f['dist_res_atr']:.1f} ATR sous la résistance"
        )
    return _clip(pts, -w, w), notes


def _score_volatility(f: dict) -> tuple[float, list[str]]:
    """Régime de volatilité : 10 pts. Lisible = bonus, chaotique = malus."""
    w = WEIGHTS["volatility"]
    ratio = f["vol_ratio"]
    notes: list[str] = [f"Volatilité actuelle : {ratio:.2f}x la médiane (ATR%)"]
    if 0.75 <= ratio <= 1.5:
        return 10.0, notes + ["Régime normal : conditions lisibles"]
    if ratio > 3.0 or ratio < 0.33:
        return -10.0, notes + ["Régime extrême : conditions illisibles, prudence"]
    # Interpolation linéaire entre les deux zones
    if ratio > 1.5:
        score = 10.0 - (ratio - 1.5) / (3.0 - 1.5) * 20.0
    else:
        score = 10.0 - (0.75 - ratio) / (0.75 - 0.33) * 20.0
    return _clip(score, -w, w), notes


def generate_signal(
    features: dict,
    direction_threshold: float = DIRECTION_THRESHOLD,
    sl_mult: float = SL_ATR_MULT,
    tp_mult: float = TP_ATR_MULT,
) -> dict:
    """Score un jeu de features -> signal complet (dict JSON-sérialisable)."""
    factor_fns = {
        "trend": _score_trend,
        "momentum": _score_momentum,
        "price_action": _score_price_action,
        "structure": _score_structure,
        "volatility": _score_volatility,
    }
    factors = {}
    total = 0.0
    justification: list[str] = []
    for key, fn in factor_fns.items():
        pts, notes = fn(features)
        pts = round(pts, 1)
        factors[key] = {
            "label": FACTOR_LABELS[key],
            "weight": WEIGHTS[key],
            "points": pts,
        }
        total += pts
        justification.extend(notes)

    total = round(_clip(total, -100.0, 100.0), 1)
    if total >= direction_threshold:
        direction = "BUY"
    elif total <= -direction_threshold:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    score = int(round(abs(total)))
    atr_v = features["atr"]
    entry = features["close"]
    if direction == "BUY":
        sl = entry - sl_mult * atr_v
        tp = entry + tp_mult * atr_v
    elif direction == "SELL":
        sl = entry + sl_mult * atr_v
        tp = entry - tp_mult * atr_v
    else:
        sl = tp = None

    return {
        "direction": direction,
        "score": score,  # /100 — force du modèle, PAS une probabilité
        "total": total,
        "factors": factors,
        "justification": justification,
        "entry": round(entry, 8),
        "stop_loss": round(sl, 8) if sl is not None else None,
        "take_profit": round(tp, 8) if tp is not None else None,
        "atr": round(atr_v, 8),
        "indicators": {
            "ema20": round(features["ema20"], 8),
            "ema50": round(features["ema50"], 8),
            "ema200": round(features["ema200"], 8),
            "rsi14": round(features["rsi14"], 2),
            "macd_hist": round(features["macd_hist"], 8),
            "atr_pct": round(features["atr_pct"], 3),
        },
    }
