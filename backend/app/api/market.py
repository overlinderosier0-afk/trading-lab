"""Endpoints market data (étape C)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.market_data import store, sync

router = APIRouter(prefix="/api/market", tags=["market"])


class SyncRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    lookback_days: int = Field(default=365, ge=1, le=1825)


@router.get("/status")
def market_status() -> dict:
    """État des données : paires suivies, couverture temporelle, trous."""
    pairs = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT symbol, timeframe, count(*), min(ts), max(ts)
                   FROM market_data GROUP BY symbol, timeframe ORDER BY symbol, timeframe"""
            )
            rows = cur.fetchall()
        for symbol, timeframe, count, first, last in rows:
            step = sync.TIMEFRAME_MS.get(timeframe)
            gaps = (store.detect_gaps(conn, symbol, timeframe, step, int(first.timestamp() * 1000))
                    if step and first else [])
            pairs.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": count,
                "first_ts": first.isoformat() if first else None,
                "last_ts": last.isoformat() if last else None,
                "gaps": len(gaps),
            })
    return {"pairs": pairs}


@router.post("/sync")
def market_sync(req: SyncRequest) -> dict:
    """Déclenche une synchro (backfill + incrémental). Synchrone : ~30 requêtes, rapide."""
    results = []
    try:
        with db.get_conn() as conn:
            for symbol in req.symbols:
                for timeframe in req.timeframes:
                    results.append(sync.sync_symbol(conn, symbol, timeframe, req.lookback_days))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"synchro impossible : {type(exc).__name__}")
    return {"results": results}


@router.get("/candles")
def market_candles(symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 100) -> dict:
    """Dernières bougies (servira au dashboard)."""
    limit = max(1, min(limit, 1000))
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume FROM market_data
                   WHERE symbol = %s AND timeframe = %s ORDER BY ts DESC LIMIT %s""",
                (symbol.upper(), timeframe, limit),
            )
            rows = cur.fetchall()
    candles = [
        {"ts": ts.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": v}
        for ts, o, h, l, c, v in reversed(rows)
    ]
    return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": candles}
