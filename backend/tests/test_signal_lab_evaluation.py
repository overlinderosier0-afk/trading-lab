"""Tests Phase 2 — évaluation observationnelle : fonctions pures.

Aucune DB, aucun réseau, aucune horloge réelle : bougies 1m construites à la
main, timestamps fixes. Chaque test vérifie un comportement distinct.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.signal_lab import evaluation as ev

UTC = timezone.utc
T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)  # minute pile
MS = 60_000


def candle(open_dt, open_, high, low, close):
    o = int(open_dt.timestamp() * 1000)
    return {"open_ms": o, "open": open_, "high": high, "low": low,
            "close": close, "volume": 10.0, "close_ms": o + MS}


def flat_series(start, n, price=100.0):
    """n bougies 1m plates à `price`, dès `start` (open time)."""
    return [candle(start + timedelta(minutes=i), price, price, price, price)
            for i in range(n)]


def with_close(candles, idx, close, high=None, low=None):
    c = dict(candles[idx])
    c["close"] = close
    c["high"] = high if high is not None else max(c["high"], close)
    c["low"] = low if low is not None else min(c["low"], close)
    candles[idx] = c
    return candles


# ---------------------------------------------------------------- return
def test_return_buy_positive():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 101.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(1.0)
    assert r["direction_correct"] is True


def test_return_buy_negative():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 99.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(-1.0)
    assert r["direction_correct"] is False


def test_return_sell_positive():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 99.0)
    r = ev.compute_evaluation(100.0, "SELL", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(1.0)
    assert r["direction_correct"] is True


def test_return_sell_negative():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 101.0)
    r = ev.compute_evaluation(100.0, "SELL", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(-1.0)
    assert r["direction_correct"] is False


def test_exit_equals_entry_is_neutral():
    cs = flat_series(T0, 5, 100.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(0.0)
    assert r["direction_correct"] is None  # ni WIN ni LOSS


def test_invalid_direction_raises():
    cs = flat_series(T0, 5, 100.0)
    with pytest.raises(ValueError):
        ev.compute_evaluation(100.0, "NEUTRAL", cs, 5, T0)


def test_entry_price_zero_returns_none():
    cs = flat_series(T0, 5, 100.0)
    assert ev.compute_evaluation(0.0, "BUY", cs, 5, T0) is None


# ---------------------------------------------------------------- MFE / MAE
def test_mfe_mae_buy():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 1, 100.0, high=103.0, low=100.0)
    with_close(cs, 3, 100.0, high=100.0, low=97.0)
    with_close(cs, 4, 101.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["mfe_pct"] == pytest.approx(3.0)
    assert r["mae_pct"] == pytest.approx(-3.0)


def test_mfe_mae_sell():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 1, 100.0, high=103.0, low=100.0)
    with_close(cs, 3, 100.0, high=100.0, low=97.0)
    with_close(cs, 4, 99.0)
    r = ev.compute_evaluation(100.0, "SELL", cs, 5, T0)
    assert r["mfe_pct"] == pytest.approx(3.0)   # (100-97)/100
    assert r["mae_pct"] == pytest.approx(-3.0)  # (100-103)/100


def test_invariants_mfe_mae():
    import random
    rng = random.Random(42)
    for _ in range(50):
        n = rng.randint(1, 20)
        cs = []
        p = 100.0
        for i in range(n):
            o = p
            c = p * (1 + rng.uniform(-0.02, 0.02))
            h = max(o, c) * (1 + rng.uniform(0, 0.01))
            l = min(o, c) * (1 - rng.uniform(0, 0.01))
            cs.append(candle(T0 + timedelta(minutes=i), o, h, l, c))
            p = c
        for direction in ("BUY", "SELL"):
            r = ev.compute_evaluation(100.0, direction, cs, n, T0)
            assert r["mfe_pct"] >= 0 >= r["mae_pct"]
            assert r["mae_pct"] <= min(0, r["return_pct"]) + 1e-9
            assert r["mfe_pct"] >= max(0, r["return_pct"]) - 1e-9


def test_symmetry_buy_sell():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 102.0, high=102.5, low=99.0)
    b = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    s = ev.compute_evaluation(100.0, "SELL", cs, 5, T0)
    assert b["return_pct"] == pytest.approx(-s["return_pct"])
    assert b["mfe_pct"] == pytest.approx(2.5)
    assert s["mfe_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------- fenêtre anti look-ahead
def test_entry_candle_excluded():
    # Bougie [11:59, 12:00) contient des prix < T0 : exclue même si
    # son close est postérieur à l'entrée.
    cs = [candle(T0 - timedelta(minutes=1), 90.0, 200.0, 90.0, 200.0)]
    cs += flat_series(T0, 5, 100.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["candles_used"] == 5
    assert r["mfe_pct"] == pytest.approx(0.0)  # le spike 200 est ignoré


def test_empty_window_returns_none():
    assert ev.compute_evaluation(100.0, "BUY", [], 5, T0) is None


def test_ongoing_candle_excluded():
    cs = flat_series(T0, 6, 100.0)  # la 6e clôt à 12:06 > T_end 12:05
    with_close(cs, 5, 150.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["candles_used"] == 5
    assert r["exit_price"] == pytest.approx(100.0)


def test_future_candle_excluded():
    cs = flat_series(T0, 5, 100.0)
    cs.append(candle(T0 + timedelta(hours=2), 100.0, 500.0, 100.0, 500.0))
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["candles_used"] == 5
    assert r["exit_price"] == pytest.approx(100.0)


def test_round_up_exact_minute_keeps_candle():
    # Entrée pile sur une minute : la bougie qui ouvre à cet instant est OK
    # (aucun prix antérieur à l'entrée dedans).
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 101.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["candles_used"] == 5


def test_round_up_mid_minute_skips_partial():
    # Entrée à 12:00:30 : première bougie évaluable = [12:01, 12:02).
    entry = T0 + timedelta(seconds=30)
    cs = [candle(T0, 100.0, 150.0, 100.0, 150.0)]  # [12:00,12:01) : exclue
    cs += flat_series(T0 + timedelta(minutes=1), 4, 100.0)
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, entry)
    assert r["candles_used"] == 4
    assert r["mfe_pct"] == pytest.approx(0.0)


def test_exit_is_last_closed_candle_within_window():
    cs = flat_series(T0, 10, 100.0)
    with_close(cs, 4, 105.0)   # dernière <= T_end (12:05)
    with_close(cs, 9, 200.0)   # hors fenêtre
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["exit_price"] == pytest.approx(105.0)
    assert r["exit_timestamp"] == T0 + timedelta(minutes=5)


def test_unsorted_candles_still_work():
    cs = flat_series(T0, 5, 100.0)
    with_close(cs, 4, 101.0)
    cs = cs[::-1]
    r = ev.compute_evaluation(100.0, "BUY", cs, 5, T0)
    assert r["return_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------- golden
def test_golden_reference():
    # BUY, entry 100000 → +0,40 % (5m), −0,20 % (15m), +1,00 % (30m), +2,00 % (60m)
    cs = flat_series(T0, 60, 100000.0)
    with_close(cs, 4, 100400.0)    # [12:04,12:05)
    with_close(cs, 14, 99800.0)    # [12:14,12:15)
    with_close(cs, 29, 101000.0)   # [12:29,12:30)
    with_close(cs, 59, 102000.0)   # [12:59,13:00)
    r5 = ev.compute_evaluation(100000.0, "BUY", cs, 5, T0)
    r15 = ev.compute_evaluation(100000.0, "BUY", cs, 15, T0)
    r30 = ev.compute_evaluation(100000.0, "BUY", cs, 30, T0)
    r60 = ev.compute_evaluation(100000.0, "BUY", cs, 60, T0)
    assert r5["return_pct"] == pytest.approx(0.40)
    assert r15["return_pct"] == pytest.approx(-0.20)
    assert r30["return_pct"] == pytest.approx(1.00)
    assert r60["return_pct"] == pytest.approx(2.00)


# ---------------------------------------------------------------- équivalence 1m / 5m
def _aggregate_5m(candles_1m):
    """Agrège des bougies 1m en bougies 5m (open 1re, close dernière,
    high=max, low=min) — même convention que le store de bougies."""
    out = []
    for i in range(0, len(candles_1m), 5):
        grp = candles_1m[i:i + 5]
        out.append({
            "open_ms": grp[0]["open_ms"],
            "close_ms": grp[-1]["close_ms"],
            "open": grp[0]["open"],
            "high": max(c["high"] for c in grp),
            "low": min(c["low"] for c in grp),
            "close": grp[-1]["close"],
            "volume": sum(c["volume"] for c in grp),
        })
    return out


def _relief_series(start, n, price=100.0):
    """n bougies 1m avec du relief (high/low variés), dès `start`."""
    cs, px = [], price
    for i in range(n):
        o = px
        h = px + 0.30 + (i % 7) * 0.05
        l = px - 0.25 - (i % 5) * 0.04
        c = px + 0.10 - (i % 3) * 0.08
        cs.append(candle(start + timedelta(minutes=i), o, h, l, c))
        px = c
    return cs


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("horizon", [5, 15, 30, 60])
def test_equivalence_1m_5m(direction, horizon):
    """Le worker mesure sur des 1m à la demande ; le store ne garde que des
    5m. Sur des fenêtres échantillons, les deux granularités doivent donner
    le même exit, return, MFE et MAE (décision #1)."""
    cs1m = _relief_series(T0, 60)
    cs5m = _aggregate_5m(cs1m)
    assert len(cs5m) == 12
    r1 = ev.compute_evaluation(100.0, direction, cs1m, horizon, T0)
    r5 = ev.compute_evaluation(100.0, direction, cs5m, horizon, T0)
    assert r1 is not None and r5 is not None
    for key in ("exit_price", "return_pct", "mfe_pct", "mae_pct"):
        assert r1[key] == pytest.approx(r5[key], abs=1e-9), key
    # Invariants MFE/MAE conservés des deux côtés.
    assert r1["mfe_pct"] >= max(0.0, r1["return_pct"])
    assert r1["mae_pct"] <= min(0.0, r1["return_pct"])


# ---------------------------------------------------------------- stats
def _row(ret, correct=True, mfe=1.0, mae=-1.0):
    return {"return_pct": ret, "direction_correct": correct,
            "mfe_pct": mfe, "mae_pct": mae}


def test_score_bucket_bounds():
    assert ev.score_bucket(0) == "0-39"
    assert ev.score_bucket(39) == "0-39"
    assert ev.score_bucket(40) == "40-49"
    assert ev.score_bucket(49) == "40-49"
    assert ev.score_bucket(50) == "50-59"
    assert ev.score_bucket(69) == "60-69"
    assert ev.score_bucket(79) == "70-79"
    assert ev.score_bucket(89) == "80-89"
    assert ev.score_bucket(90) == "90-100"
    assert ev.score_bucket(100) == "90-100"


def test_profit_factor_normal():
    rows = [_row(2.0), _row(1.0), _row(-1.0)]
    s = ev.aggregate_stats(rows)
    assert s["profit_factor"] == pytest.approx(3.0)


def test_profit_factor_no_loss_is_none():
    rows = [_row(2.0), _row(1.0)]
    assert ev.aggregate_stats(rows)["profit_factor"] is None


def test_profit_factor_no_profit_is_zero():
    rows = [_row(-2.0), _row(-1.0)]
    assert ev.aggregate_stats(rows)["profit_factor"] == pytest.approx(0.0)


def test_aggregate_empty():
    s = ev.aggregate_stats([])
    assert s["n"] == 0
    assert s["win_rate"] is None
    assert s["average_return"] is None
    assert s["median_return"] is None
    assert s["total_return"] is None
    assert s["profit_factor"] is None
    assert s["average_mfe"] is None
    assert s["average_mae"] is None
    assert s["sample_quality"] == "Insufficient sample"


def test_median_odd_and_even():
    assert ev.aggregate_stats([_row(1.0), _row(3.0), _row(2.0)])["median_return"] == pytest.approx(2.0)
    assert ev.aggregate_stats([_row(1.0), _row(3.0)])["median_return"] == pytest.approx(2.0)


def test_sample_quality_thresholds():
    assert ev.sample_quality(0) == "Insufficient sample"
    assert ev.sample_quality(29) == "Insufficient sample"
    assert ev.sample_quality(30) == "Early evidence"
    assert ev.sample_quality(99) == "Early evidence"
    assert ev.sample_quality(100) == "Moderate sample"
    assert ev.sample_quality(299) == "Moderate sample"
    assert ev.sample_quality(300) == "Large sample"


def test_result_label():
    assert ev.result_label(0.5) == "WIN"
    assert ev.result_label(0.0001) == "WIN"
    assert ev.result_label(-0.5) == "LOSS"
    assert ev.result_label(-0.0001) == "LOSS"
    assert ev.result_label(0.0) == "FLAT"
    assert ev.result_label(None) is None


def test_win_loss_flat_rates():
    rows = [_row(1.0, True), _row(-1.0, False), _row(0.0, None), _row(2.0, True)]
    s = ev.aggregate_stats(rows)
    assert s["n"] == 4 and s["wins"] == 2 and s["losses"] == 1 and s["flats"] == 1
    assert s["wins"] + s["losses"] + s["flats"] == s["n"]
    assert s["win_rate"] == pytest.approx(0.5)
    # loss_rate = losses / n, pas 1 - win_rate : avec un FLAT, 1 - 0.5 != 0.25.
    assert s["loss_rate"] == pytest.approx(0.25)
    assert s["loss_rate"] != pytest.approx(1 - s["win_rate"])
    assert s["flat_rate"] == pytest.approx(0.25)
    assert s["total_return"] == pytest.approx(2.0)


def test_aggregate_stats_derives_from_return_pct_not_direction_correct():
    # direction_correct incohérent : le label vient de return_pct seul.
    rows = [_row(1.0, False), _row(-1.0, True), _row(0.0, True)]
    s = ev.aggregate_stats(rows)
    assert s["wins"] == 1 and s["losses"] == 1 and s["flats"] == 1
    assert "neutrals" not in s and "neutral_rate" not in s


def test_horizons_never_mixed():
    # Deux groupes séparés gardent leurs propres n : l'agrégat ne sait pas
    # mélanger, c'est l'appelant qui choisit le groupe (garde-fou §2).
    g5 = ev.aggregate_stats([_row(1.0)] * 10)
    g60 = ev.aggregate_stats([_row(-1.0)] * 3)
    assert g5["n"] == 10 and g60["n"] == 3
    assert g5["average_return"] == pytest.approx(1.0)
    assert g60["average_return"] == pytest.approx(-1.0)


def test_nan_inf_never_leak():
    rows = [_row(float("inf"), True), _row(float("-inf"), False)]
    s = ev.aggregate_stats(rows)
    for k, v in s.items():
        if isinstance(v, float):
            assert v == v and abs(v) != float("inf"), k


# ---------------------------------------------------------------- coûts / paper
def test_net_return_subtracts_cost():
    assert ev.net_return_pct(0.50, 20.0) == pytest.approx(0.30)
    assert ev.net_return_pct(-0.10, 20.0) == pytest.approx(-0.30)
    assert ev.net_return_pct(None, 20.0) is None


def test_paper_pnl():
    assert ev.paper_pnl_usdt(0.50, 100.0) == pytest.approx(0.50)
    assert ev.paper_pnl_usdt(-2.0, 100.0) == pytest.approx(-2.0)
    assert ev.paper_pnl_usdt(None, 100.0) is None


def test_costs_never_stored_in_evaluation():
    # compute_evaluation ne connaît pas les coûts : le brut seul est stocké.
    import inspect
    sig = inspect.signature(ev.compute_evaluation)
    assert "cost_bps" not in sig.parameters
    assert "cost" not in str(sig)


# ------------------------------------------------------- signal_evaluability
def _sig(direction="BUY", entry_ts=T0, lag_s=5.0, created=None):
    created_at = (created if created is not None
                  else (entry_ts + timedelta(seconds=lag_s)
                        if entry_ts is not None else None))
    return {"direction": direction, "entry_timestamp": entry_ts,
            "created_at": created_at}


def test_evaluability_ok_within_lag():
    assert ev.signal_evaluability(_sig(lag_s=5.0), 60.0) == (True, None)


def test_evaluability_boundary_lag_equals_threshold():
    # lag == seuil : évaluable (le garde-fou déclenche strictement au-delà).
    assert ev.signal_evaluability(_sig(lag_s=60.0), 60.0) == (True, None)


def test_evaluability_lag_exceeded():
    ok, reason = ev.signal_evaluability(_sig(lag_s=61.0), 60.0)
    assert (ok, reason) == (False, "entry_lag")


def test_evaluability_no_entry_timestamp():
    ok, reason = ev.signal_evaluability(_sig(entry_ts=None), 60.0)
    assert (ok, reason) == (False, "entry_timestamp_not_derivable")


def test_evaluability_neutral_direction():
    ok, reason = ev.signal_evaluability(_sig(direction="NEUTRAL"), 60.0)
    assert (ok, reason) == (False, "neutral_direction")


def test_evaluability_naive_datetimes_read_as_utc():
    naive = {"direction": "BUY",
             "entry_timestamp": T0.replace(tzinfo=None),
             "created_at": (T0 + timedelta(seconds=5)).replace(tzinfo=None)}
    assert ev.signal_evaluability(naive, 60.0) == (True, None)


def test_evaluability_custom_threshold():
    # Même signal, verdict différent selon le seuil : la fonction est
    # l'unique source de vérité, pas le seuil codé en dur.
    assert ev.signal_evaluability(_sig(lag_s=45.0), 60.0) == (True, None)
    assert ev.signal_evaluability(_sig(lag_s=45.0), 30.0) == (False, "entry_lag")
