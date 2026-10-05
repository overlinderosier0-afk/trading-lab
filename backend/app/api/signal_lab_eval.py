"""Endpoints d'évaluation observationnelle du Signal Lab.

Paper-trading de MESURE uniquement : aucun ordre, aucune position.
Les coûts et le P&L virtuel sont calculés à la lecture (jamais stockés),
toujours étiquetés SIMULATED. Unité d'analyse = (signal, horizon) :
aucune statistique ne mélange des horizons.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app import db
from app.config import settings
from app.signal_lab import evaluation as ev

router = APIRouter(tags=["signal-lab-evaluation"])

DISCLAIMER = ("Evaluation is observational paper-trading analysis "
              "(SIMULATED: no real positions, costs are simulated). "
              "It does not guarantee future performance.")

_VALID_DIRECTIONS = ("BUY", "SELL")
_VALID_ORIGINS = ("manual", "auto")
_VALID_GROUP_BY = ("score_range", "symbol", "timeframe", "direction",
                   "horizon", "origin")
_VALID_STATUS = ("PENDING", "COMPLETED", "ERROR", "UNAVAILABLE")


def _configured_horizons() -> list[int]:
    return [int(h) for h in settings.signal_lab_eval_horizons.split(",")
            if h.strip()]


def _parse_dt(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400,
                            detail=f"paramètre {name} invalide (ISO 8601 attendu)")
    return dt


def _filters(symbol=None, timeframe=None, direction=None, origin=None,
             min_score=None, max_score=None, date_from=None, date_to=None,
             status=None, horizon=None):
    """Construit (clauses SQL, args) pour les filtres communs. 400 si invalide."""
    clauses, args = [], []
    if symbol:
        clauses.append("s.symbol = %s")
        args.append(symbol.upper())
    if timeframe:
        clauses.append("s.timeframe = %s")
        args.append(timeframe)
    if direction:
        if direction.upper() not in _VALID_DIRECTIONS:
            raise HTTPException(status_code=400,
                                detail="direction invalide (BUY ou SELL)")
        clauses.append("s.direction = %s")
        args.append(direction.upper())
    if origin:
        if origin not in _VALID_ORIGINS:
            raise HTTPException(status_code=400,
                                detail="origin invalide (manual ou auto)")
        clauses.append("s.origin = %s")
        args.append(origin)
    if min_score is not None:
        if not 0 <= min_score <= 100:
            raise HTTPException(status_code=400,
                                detail="min_score invalide (0-100)")
        clauses.append("s.score >= %s")
        args.append(min_score)
    if max_score is not None:
        if not 0 <= max_score <= 100:
            raise HTTPException(status_code=400,
                                detail="max_score invalide (0-100)")
        clauses.append("s.score <= %s")
        args.append(max_score)
    df = _parse_dt(date_from, "date_from")
    dt = _parse_dt(date_to, "date_to")
    if df:
        clauses.append("s.created_at >= %s")
        args.append(df)
    if dt:
        clauses.append("s.created_at <= %s")
        args.append(dt)
    if status:
        if status.upper() not in _VALID_STATUS:
            raise HTTPException(status_code=400,
                                detail="status invalide")
        clauses.append("e.status = %s")
        args.append(status.upper())
    if horizon is not None:
        if horizon <= 0:
            raise HTTPException(status_code=400,
                                detail="horizon invalide (> 0)")
        clauses.append("e.horizon_minutes = %s")
        args.append(horizon)
    return clauses, args


def _max_lag_seconds() -> float:
    return settings.signal_lab_eval_max_entry_lag_seconds


def _completed_rows(conn, horizon, clauses, args) -> list[dict]:
    """Évaluations COMPLETED d'un horizon, signaux évaluables uniquement.

    Le garde-fou est appliqué À LA LECTURE : les lignes COMPLETED existantes
    ne sont jamais modifiées, mais celles dont le signal est non évaluable
    (evaluation.signal_evaluability) sont exclues des stats.
    """
    max_lag = _max_lag_seconds()
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT e.return_pct, e.direction_correct, e.mfe_pct, e.mae_pct,
                       s.timeframe, s.score, s.symbol, s.direction, s.origin,
                       s.created_at, s.entry_timestamp
                FROM signal_lab_evaluations e
                JOIN signal_lab_signals s ON s.id = e.signal_id
                WHERE e.status = 'COMPLETED' AND e.horizon_minutes = %s
                {"AND " + " AND ".join(clauses) if clauses else ""}""",
            [horizon] + args,
        )
        rows = []
        for r in cur.fetchall():
            sig = {"direction": r[7], "created_at": r[9],
                   "entry_timestamp": r[10]}
            if not ev.signal_evaluability(sig, max_lag)[0]:
                continue
            rows.append({"return_pct": r[0], "direction_correct": r[1],
                         "mfe_pct": r[2], "mae_pct": r[3], "timeframe": r[4],
                         "score": r[5], "symbol": r[6], "direction": r[7],
                         "origin": r[8]})
        return rows


def _with_net(rows: list[dict], cost_bps: float) -> tuple[dict, dict]:
    """Stats brutes + nettes (coût soustrait à chaque return)."""
    gross = ev.aggregate_stats(rows)
    net_rows = [dict(r, return_pct=ev.net_return_pct(r["return_pct"], cost_bps))
                for r in rows]
    net = ev.aggregate_stats(net_rows)
    return gross, net


def _paper_pnl(rows: list[dict], stake: float) -> dict:
    pnls = [ev.paper_pnl_usdt(r["return_pct"], stake) for r in rows
            if r.get("return_pct") is not None]
    total = sum(pnls) if pnls else None
    return {
        "total_usdt": total,
        "average_usdt": (total / len(pnls)) if pnls else None,
        "stake_usdt": stake,
        "simulated": True,
    }


def _stats_block(rows: list[dict], timeframes: list[str]) -> dict:
    cost = settings.signal_lab_eval_cost_bps
    stake = settings.signal_lab_eval_paper_stake_usdt
    gross, net = _with_net(rows, cost)
    block = {
        "n": gross["n"],
        "sample_quality": gross["sample_quality"],
        "wins": gross["wins"], "losses": gross["losses"],
        "neutrals": gross["neutrals"],
        "win_rate": gross["win_rate"], "loss_rate": gross["loss_rate"],
        "neutral_rate": gross["neutral_rate"],
        "average_return": gross["average_return"],
        "average_return_net": net["average_return"],
        "median_return": gross["median_return"],
        "median_return_net": net["median_return"],
        "total_return": gross["total_return"],
        "total_return_net": net["total_return"],
        "profit_factor": gross["profit_factor"],
        "profit_factor_net": net["profit_factor"],
        "average_mfe": gross["average_mfe"],
        "average_mae": gross["average_mae"],
        "paper_pnl": _paper_pnl(rows, stake),
        "mixed_timeframes": len(set(timeframes)) > 1,
        "timeframes_included": sorted(set(timeframes)),
        "disclaimer": DISCLAIMER,
    }
    return block


@router.get("/api/signal-lab/eval/summary")
def summary(
    horizon: int | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    origin: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Stats par horizon (un bloc par horizon si non précisé)."""
    clauses, args = _filters(symbol=symbol, timeframe=timeframe,
                             direction=direction, origin=origin,
                             min_score=min_score, max_score=max_score,
                             date_from=date_from, date_to=date_to)
    if horizon is not None and horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon invalide (> 0)")
    horizons = [horizon] if horizon else _configured_horizons()
    sig_clauses, sig_args = _filters(symbol=symbol, timeframe=timeframe,
                                     direction=direction, origin=origin,
                                     min_score=min_score, max_score=max_score,
                                     date_from=date_from, date_to=date_to)
    max_lag = _max_lag_seconds()
    out = {}
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # Signaux BUY/SELL (filtres) : total + part non évaluable.
            # Un signal non évaluable est compté en unavailable ET exclu des
            # stats / pending / completed : aucun double comptage.
            cur.execute(
                f"""SELECT s.direction, s.created_at, s.entry_timestamp
                    FROM signal_lab_signals s
                    WHERE s.direction IN ('BUY','SELL')
                    {"AND " + " AND ".join(sig_clauses) if sig_clauses else ""}""",
                sig_args,
            )
            scols = [d[0] for d in cur.description]
            sig_rows = [dict(zip(scols, r)) for r in cur.fetchall()]
        total_signals = len(sig_rows)
        unavailable = sum(
            1 for s in sig_rows
            if not ev.signal_evaluability(s, max_lag)[0])
        for h in horizons:
            rows = _completed_rows(conn, h, clauses, args)
            with conn.cursor() as cur:
                # PENDING dont le signal est évaluable (les PENDING de
                # signaux non évaluables sont exclus, comptés en unavailable).
                cur.execute(
                    f"""SELECT s.direction, s.created_at, s.entry_timestamp
                        FROM signal_lab_evaluations e
                        JOIN signal_lab_signals s ON s.id = e.signal_id
                        WHERE e.status = 'PENDING' AND e.horizon_minutes = %s
                        {"AND " + " AND ".join(clauses) if clauses else ""}""",
                    [h] + args,
                )
                pcols = [d[0] for d in cur.description]
                pending = sum(
                    1 for r in cur.fetchall()
                    if ev.signal_evaluability(dict(zip(pcols, r)),
                                             max_lag)[0])
            block = _stats_block(rows, [r["timeframe"] for r in rows])
            block.update({
                "total_signals": total_signals,
                "completed_signals": block.pop("n"),
                "pending_signals": pending,
                "unavailable_signals": unavailable,
                "max_entry_lag_seconds": max_lag,
            })
            out[str(h)] = block
    return {"horizons": out, "disclaimer": DISCLAIMER}


@router.get("/api/signal-lab/eval/signals")
def signals(
    horizon: int | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    origin: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = Query(default=50, le=200),
):
    """Signaux paginés avec leurs évaluations indexées par horizon."""
    if page < 1:
        raise HTTPException(status_code=400, detail="page invalide (>= 1)")
    if page_size < 1:
        raise HTTPException(status_code=400, detail="page_size invalide (>= 1)")
    clauses, args = _filters(symbol=symbol, timeframe=timeframe,
                             direction=direction, origin=origin,
                             min_score=min_score, max_score=max_score,
                             date_from=date_from, date_to=date_to,
                             status=status)
    where = " AND ".join(clauses)
    # Le filtre horizon ne garde que les signaux ayant une évaluation à cet horizon.
    h_join = ""
    if horizon is not None:
        if horizon <= 0:
            raise HTTPException(status_code=400, detail="horizon invalide (> 0)")
        h_join = ("JOIN signal_lab_evaluations eh ON eh.signal_id = s.id "
                  "AND eh.horizon_minutes = %s")
        args = [horizon] + args
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(DISTINCT s.id) FROM signal_lab_signals s
                    {h_join}
                    WHERE s.direction IN ('BUY','SELL')
                    {"AND " + where if where else ""}""",
                args,
            )
            total = cur.fetchone()[0]
            cur.execute(
                f"""SELECT DISTINCT s.id, s.created_at, s.symbol, s.timeframe,
                           s.direction, s.score, s.entry_price, s.entry_timestamp,
                           s.entry_price_source, s.origin, s.candle_ts
                    FROM signal_lab_signals s
                    {h_join}
                    WHERE s.direction IN ('BUY','SELL')
                    {"AND " + where if where else ""}
                    ORDER BY s.created_at DESC, s.id DESC
                    LIMIT %s OFFSET %s""",
                args + [page_size, (page - 1) * page_size],
            )
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, r)) for r in cur.fetchall()]
            max_lag = _max_lag_seconds()
            for it in items:
                ok, reason = ev.signal_evaluability(
                    {"direction": it["direction"],
                     "created_at": it["created_at"],
                     "entry_timestamp": it["entry_timestamp"]}, max_lag)
                it["evaluation_state"] = "available" if ok else "unavailable"
                it["unavailable_reason"] = reason
                it["id"] = str(it["id"])
                it["created_at"] = it["created_at"].isoformat()
                it["entry_timestamp"] = (it["entry_timestamp"].isoformat()
                                         if it["entry_timestamp"] else None)
                it["candle_ts"] = (it["candle_ts"].isoformat()
                                   if it["candle_ts"] else None)
            if items:
                cur.execute(
                    """SELECT e.signal_id, e.horizon_minutes, e.status,
                              e.entry_price, e.exit_price, e.exit_timestamp,
                              e.return_pct, e.direction_correct,
                              e.mfe_pct, e.mae_pct, e.evaluated_at, e.created_at
                       FROM signal_lab_evaluations e
                       WHERE e.signal_id = ANY(%s)""",
                    ([i["id"] for i in items],),
                )
                ecols = [d[0] for d in cur.description]
                by_signal: dict[str, dict] = {}
                for r in cur.fetchall():
                    d = dict(zip(ecols, r))
                    d["signal_id"] = str(d["signal_id"])
                    for k in ("exit_timestamp", "evaluated_at", "created_at"):
                        d[k] = d[k].isoformat() if d[k] else None
                    by_signal.setdefault(d.pop("signal_id"), {})[
                        str(d["horizon_minutes"])] = d
                for it in items:
                    it["evaluations"] = by_signal.get(it["id"], {})
            else:
                for it in items:
                    it["evaluations"] = {}
    return {"items": items, "page": page, "page_size": page_size, "total": total,
            "disclaimer": DISCLAIMER}


@router.get("/api/signal-lab/eval/signals/{signal_id}")
def signal_detail(signal_id: str):
    """Détail d'un signal : entrée + toutes ses évaluations."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, created_at, symbol, timeframe, direction, score,
                          entry_price, entry_timestamp, entry_price_source,
                          origin, candle_ts
                   FROM signal_lab_signals WHERE id = %s""",
                (signal_id,),
            )
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="signal introuvable")
            cols = [d[0] for d in cur.description]
            sig = dict(zip(cols, r))
            ok, reason = ev.signal_evaluability(
                {"direction": sig["direction"], "created_at": sig["created_at"],
                 "entry_timestamp": sig["entry_timestamp"]},
                _max_lag_seconds())
            sig["evaluation_state"] = "available" if ok else "unavailable"
            sig["unavailable_reason"] = reason
            sig["id"] = str(sig["id"])
            for k in ("created_at", "entry_timestamp", "candle_ts"):
                sig[k] = sig[k].isoformat() if sig[k] else None
            cur.execute(
                """SELECT horizon_minutes, status, entry_price, exit_price,
                          exit_timestamp, return_pct, direction_correct,
                          mfe_pct, mae_pct, evaluated_at, created_at,
                          attempt_count, last_error
                   FROM signal_lab_evaluations
                   WHERE signal_id = %s ORDER BY horizon_minutes""",
                (signal_id,),
            )
            ecols = [d[0] for d in cur.description]
            evals = []
            for er in cur.fetchall():
                d = dict(zip(ecols, er))
                for k in ("exit_timestamp", "evaluated_at", "created_at"):
                    d[k] = d[k].isoformat() if d[k] else None
                evals.append(d)
            sig["evaluations"] = evals
    sig["disclaimer"] = DISCLAIMER
    return sig


@router.get("/api/signal-lab/eval/breakdown")
def breakdown(
    group_by: str = Query(...),
    horizon: int | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    origin: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Ventilation des stats par groupe (un seul horizon, sauf group_by=horizon)."""
    if group_by not in _VALID_GROUP_BY:
        raise HTTPException(status_code=400,
                            detail=f"group_by invalide ({'/'.join(_VALID_GROUP_BY)})")
    if group_by != "horizon" and horizon is None:
        raise HTTPException(status_code=400,
                            detail="horizon requis sauf pour group_by=horizon")
    if horizon is not None and horizon <= 0:
        raise HTTPException(status_code=400, detail="horizon invalide (> 0)")
    clauses, args = _filters(symbol=symbol, timeframe=timeframe,
                             direction=direction, origin=origin,
                             min_score=min_score, max_score=max_score,
                             date_from=date_from, date_to=date_to)
    with db.get_conn() as conn:
        if group_by == "horizon":
            groups = {}
            for h in _configured_horizons():
                rows = _completed_rows(conn, h, clauses, args)
                groups[str(h)] = _stats_block(rows,
                                             [r["timeframe"] for r in rows])
            return {"group_by": group_by, "groups": groups,
                    "max_entry_lag_seconds": _max_lag_seconds(),
                    "disclaimer": DISCLAIMER}
        rows = _completed_rows(conn, horizon, clauses, args)
        if group_by == "score_range":
            groups = {}
            for label in ev.SCORE_RANGES:
                lo, hi = label.split("-")
                sub = [r for r in rows
                       if r["score"] is not None
                       and int(lo) <= r["score"] <= int(hi)]
                groups[label] = _stats_block(sub, [x["timeframe"] for x in sub])
            return {"group_by": group_by, "horizon": horizon,
                    "groups": groups,
                    "max_entry_lag_seconds": _max_lag_seconds(),
                    "disclaimer": DISCLAIMER}
        key = {"symbol": "symbol", "timeframe": "timeframe",
               "direction": "direction", "origin": "origin"}[group_by]
        groups: dict[str, dict] = {}
        values = sorted({r[key] for r in rows})
        for v in values:
            sub = [r for r in rows if r[key] == v]
            groups[str(v)] = _stats_block(sub, [x["timeframe"] for x in sub])
        return {"group_by": group_by, "horizon": horizon,
                "groups": groups,
                "max_entry_lag_seconds": _max_lag_seconds(),
                "disclaimer": DISCLAIMER}
