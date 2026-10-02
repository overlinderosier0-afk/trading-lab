"""Endpoints stratégies & signaux (étape E)."""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException

from app import db
from app.strategies.registry import STRATEGIES

router = APIRouter(tags=["strategies"])

WARMUP_MARGIN = 200  # bougies supplémentaires pour la chauffe des indicateurs


@router.get("/api/strategies")
def list_strategies() -> dict:
    """Stratégies disponibles avec leurs paramètres par défaut."""
    return {
        "strategies": [
            {
                "name": cls.name,
                "description": cls.description,
                "default_params": cls.default_params(),
                "edge_status": cls.edge_status,
                "edge_note": cls.edge_note,
            }
            for cls in STRATEGIES.values()
        ]
    }


@router.get("/api/signals")
def recent_signals(
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    strategy: str = "ema_rsi_demo",
    limit: int = 50,
) -> dict:
    """Derniers signaux d'une stratégie (avec valeurs des indicateurs)."""
    limit = max(1, min(limit, 200))
    cls = STRATEGIES.get(strategy)
    if cls is None:
        raise HTTPException(
            status_code=404,
            detail=f"stratégie inconnue : {strategy} (choix : {sorted(STRATEGIES)})",
        )
    symbol = symbol.upper()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume FROM market_data
                   WHERE symbol = %s AND timeframe = %s
                   ORDER BY ts DESC LIMIT %s""",
                (symbol, timeframe, limit + WARMUP_MARGIN),
            )
            rows = cur.fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"aucune donnée pour {symbol} {timeframe} — lance POST /api/market/sync",
        )
    rows = list(reversed(rows))  # ordre chronologique
    tss = [r[0] for r in rows]
    candles = {
        "open": np.array([r[1] for r in rows]),
        "high": np.array([r[2] for r in rows]),
        "low": np.array([r[3] for r in rows]),
        "close": np.array([r[4] for r in rows]),
        "volume": np.array([r[5] for r in rows]),
    }
    strat = cls()
    signals = strat.generate(candles)
    ind = strat.indicator_values(candles) if hasattr(strat, "indicator_values") else {}

    def _clean(v) -> float | None:
        f = float(v)
        return None if np.isnan(f) else round(f, 4)

    out = []
    for i in range(len(rows) - limit, len(rows)):
        out.append({
            "ts": tss[i].isoformat(),
            "signal": signals[i],
            "close": round(float(candles["close"][i]), 4),
            "indicators": {k: _clean(v[i]) for k, v in ind.items()},
        })
    return {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "signals": out}
