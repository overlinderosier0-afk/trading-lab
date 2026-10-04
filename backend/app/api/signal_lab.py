"""Endpoints Signal Lab : génération manuelle de signaux + suivi honnête.

AUCUNE exécution : générer un signal ne fait qu'analyser et enregistrer.
Le scheduler ne fait que résoudre les signaux échus (win/loss mesuré) et,
si activé, générer automatiquement un signal par bougie clôturée.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.config import settings
from app.signal_lab import service, store

router = APIRouter(tags=["signal-lab"])


class GenerateRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"


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
    Si la dernière bougie a déjà été scorée, aucun doublon n'est créé.
    """
    if not settings.signal_lab_enabled:
        raise HTTPException(status_code=403, detail="Signal Lab désactivé")
    try:
        with db.get_conn() as conn:
            out = service.generate_and_store(
                conn, req.symbol, req.timeframe, origin="manual",
                direction_threshold=settings.signal_lab_direction_threshold,
                sl_mult=settings.signal_lab_sl_atr_mult,
                tp_mult=settings.signal_lab_tp_atr_mult,
            )
    except service.InsufficientData as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe,
        "data_last_ts": out["candle_ts"],
        "candles_used": out["candles_used"],
        "signal": out["signal"],
        "stored": out["stored"],
        "duplicate": out["skipped"] == "duplicate",
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
