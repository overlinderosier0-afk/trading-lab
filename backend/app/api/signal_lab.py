"""Endpoints Signal Lab : génération manuelle de signaux + suivi honnête.

AUCUNE exécution : générer un signal ne fait qu'analyser et enregistrer.
Le scheduler ne fait que résoudre les signaux échus (win/loss mesuré).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.config import settings
from app.signal_lab import engine, features, store

router = APIRouter(tags=["signal-lab"])

WARMUP = 400  # bougies lues (230 mini pour EMA200 + marge)


class GenerateRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"


def _closed_candles(symbol: str, timeframe: str) -> dict:
    """Bougies CLOSES uniquement (bougie en formation exclue — anti look-ahead)."""
    if timeframe not in store.TIMEFRAME_MINUTES:
        raise HTTPException(
            status_code=400,
            detail=f"timeframe inconnu : {timeframe} "
                   f"(choix : {sorted(store.TIMEFRAME_MINUTES)})",
        )
    symbol = symbol.upper()
    tf_ms = store.TIMEFRAME_MINUTES[timeframe]
    # Une bougie d'ouverture ts est close quand ts + durée <= maintenant.
    cutoff = datetime.now(timezone.utc) - timedelta(milliseconds=tf_ms)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume FROM market_data
                   WHERE symbol = %s AND timeframe = %s AND ts <= %s
                   ORDER BY ts DESC LIMIT %s""",
                (symbol, timeframe, cutoff, WARMUP),
            )
            rows = cur.fetchall()
    if len(rows) < features.MIN_CANDLES:
        raise HTTPException(
            status_code=422,
            detail=f"pas assez de bougies closes pour {symbol} {timeframe} : "
                   f"{len(rows)} < {features.MIN_CANDLES} — lance POST /api/market/sync",
        )
    rows = list(reversed(rows))
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "last_ts": rows[-1][0].isoformat(),
        "n": len(rows),
        "candles": {
            "open": np.array([r[1] for r in rows], dtype=float),
            "high": np.array([r[2] for r in rows], dtype=float),
            "low": np.array([r[3] for r in rows], dtype=float),
            "close": np.array([r[4] for r in rows], dtype=float),
            "volume": np.array([r[5] for r in rows], dtype=float),
        },
    }


@router.get("/api/signal-lab/universe")
def get_universe() -> dict:
    """Paires et timeframes réellement disponibles en base."""
    with db.get_conn() as conn:
        return store.universe(conn)


@router.post("/api/signal-lab/generate")
def generate(req: GenerateRequest) -> dict:
    """Analyse les données récentes et génère UN signal (BUY/SELL/NEUTRAL).

    BUY/SELL sont enregistrés et résolus automatiquement après l'horizon
    (3 bougies). NEUTRAL n'est pas enregistré : le modèle dit "je ne sais pas".
    """
    if not settings.signal_lab_enabled:
        raise HTTPException(status_code=403, detail="Signal Lab désactivé")
    data = _closed_candles(req.symbol, req.timeframe)
    c = data["candles"]
    try:
        feats = features.compute_features(c["open"], c["high"], c["low"], c["close"], c["volume"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = engine.generate_signal(
        feats,
        direction_threshold=settings.signal_lab_direction_threshold,
        sl_mult=settings.signal_lab_sl_atr_mult,
        tp_mult=settings.signal_lab_tp_atr_mult,
    )
    stored = None
    if result["direction"] in ("BUY", "SELL"):
        with db.get_conn() as conn:
            stored = store.insert_signal(conn, data["symbol"], data["timeframe"], result)
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "data_last_ts": data["last_ts"],
        "candles_used": data["n"],
        "signal": result,
        "stored": stored,
        "disclaimer": "Score /100 = force du modèle, pas une probabilité de gain. "
                      "Aucune exécution réelle — analyse et suivi uniquement.",
    }


@router.get("/api/signal-lab/signals")
def history(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Historique des signaux générés, avec leur résolution (win/loss/pending)."""
    with db.get_conn() as conn:
        signals = store.list_signals(
            conn,
            symbol.upper() if symbol else None,
            timeframe,
            limit,
            offset,
        )
    return {"signals": signals, "limit": limit, "offset": offset}


@router.get("/api/signal-lab/stats")
def stats(
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict:
    """Calibration : taux de réussite OBSERVÉ par tranche de score.

    C'est la seule mesure honnête de "ce que le système produit réellement".
    """
    # Résoudre d'abord ce qui est échu pour des stats fraîches.
    with db.get_conn() as conn:
        store.resolve_due_signals(conn)
    with db.get_conn() as conn:
        return store.calibration_stats(
            conn,
            symbol.upper() if symbol else None,
            timeframe,
        )


@router.post("/api/signal-lab/resolve")
def resolve_now() -> dict:
    """Déclenche manuellement la résolution des signaux échus."""
    with db.get_conn() as conn:
        return store.resolve_due_signals(conn)
