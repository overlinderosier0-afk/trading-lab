"""Synchronisation : backfill historique + mise à jour incrémentale. Idempotent."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx
import psycopg

from app.market_data import binance
from app.market_data import store

log = logging.getLogger("tradinglab")

TIMEFRAME_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
REQUEST_PAUSE_S = 0.2  # politesse envers l'API (limites très généreuses par ailleurs)


def sync_symbol(
    conn: psycopg.Connection,
    symbol: str,
    timeframe: str,
    lookback_days: int = 365,
    client: httpx.Client | None = None,
) -> dict:
    """Télécharge les bougies manquantes puis détecte les trous."""
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"timeframe inconnu : {timeframe} (choix : {sorted(TIMEFRAME_MS)})")
    sym = binance.normalize_symbol(symbol)
    step_ms = TIMEFRAME_MS[timeframe]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    last_ts = store.get_last_ts(conn, sym, timeframe)
    if last_ts is not None:
        start_ms = int(last_ts.timestamp() * 1000) + 1  # reprend juste après la dernière
        log.info("Reprise %s %s depuis %s", sym, timeframe, last_ts.isoformat(),
                 extra={"event": "DATA_FETCH"})
    else:
        start_ms = now_ms - lookback_days * 86_400_000
        log.info("Backfill %s %s sur %s jours", sym, timeframe, lookback_days,
                 extra={"event": "DATA_FETCH"})

    own_client = client is None
    client = client or httpx.Client(base_url=binance.base_url(), timeout=15.0)
    inserted_total, rejected_total = 0, 0
    try:
        cursor_ms = start_ms
        while True:
            candles = binance.fetch_klines(sym, timeframe, start_ms=cursor_ms,
                                           limit=1000, client=client)
            if not candles:
                break
            ins, rej = store.upsert_candles(conn, sym, timeframe, candles)
            inserted_total += ins
            rejected_total += rej
            cursor_ms = candles[-1]["open_ms"] + 1  # avance (les open_ms sont croissants)
            if len(candles) < 1000 or cursor_ms >= now_ms:
                break
            time.sleep(REQUEST_PAUSE_S)
    finally:
        if own_client:
            client.close()

    gaps = store.detect_gaps(conn, sym, timeframe, step_ms, start_ms)
    if gaps:
        log.warning("%s trous détectés (%s %s)", len(gaps), sym, timeframe,
                    extra={"event": "DATA_GAPS"})

    new_last = store.get_last_ts(conn, sym, timeframe)
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "inserted": inserted_total,
        "rejected": rejected_total,
        "gaps": len(gaps),
        "last_ts": new_last.isoformat() if new_last else None,
    }
