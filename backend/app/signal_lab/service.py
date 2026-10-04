"""Signal Lab — service partagé entre l'API (génération manuelle)
et le scheduler (auto-génération).

Règle centrale : UN signal par bougie clôturée et par paire.
Regénérer sur la même bougie ne crée aucun doublon (retourne skipped).
Les NEUTRAL ne sont jamais stockés.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from app.market_data.binance import normalize_symbol
from app.signal_lab import engine, features, store

log = logging.getLogger("tradinglab")

WARMUP = 400  # bougies lues (230 mini pour EMA200 + marge)


class InsufficientData(Exception):
    """Pas assez de bougies closes pour scorer (l'appelant convertit en 422/skip)."""


def fetch_closed_candles(conn, symbol: str, timeframe: str,
                         warmup: int = WARMUP) -> dict:
    """Bougies CLOSES uniquement — la bougie en formation est exclue.

    Anti look-ahead : une bougie d'ouverture `ts` n'est close que si
    `ts + durée_timeframe <= maintenant`.
    """
    if timeframe not in store.TIMEFRAME_MINUTES:
        raise ValueError(
            f"timeframe inconnu : {timeframe} "
            f"(choix : {sorted(store.TIMEFRAME_MINUTES)})"
        )
    # Format natif Binance ('btc/usdt' -> 'BTCUSDT') : les bougies sont
    # toujours stockées normalisées, quel que soit le format en .env.
    symbol = normalize_symbol(symbol)
    tf_ms = store.TIMEFRAME_MINUTES[timeframe]
    cutoff = datetime.now(timezone.utc) - timedelta(milliseconds=tf_ms)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ts, open, high, low, close, volume FROM market_data
               WHERE symbol = %s AND timeframe = %s AND ts <= %s
               ORDER BY ts DESC LIMIT %s""",
            (symbol, timeframe, cutoff, warmup),
        )
        rows = cur.fetchall()
    if len(rows) < features.MIN_CANDLES:
        raise InsufficientData(
            f"pas assez de bougies closes pour {symbol} {timeframe} : "
            f"{len(rows)} < {features.MIN_CANDLES}"
        )
    rows = list(reversed(rows))  # ordre chronologique
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_ts": rows[-1][0],  # datetime de la dernière bougie close
        "last_ts_iso": rows[-1][0].isoformat(),
        "n": len(rows),
        "candles": {
            "open": np.array([r[1] for r in rows], dtype=float),
            "high": np.array([r[2] for r in rows], dtype=float),
            "low": np.array([r[3] for r in rows], dtype=float),
            "close": np.array([r[4] for r in rows], dtype=float),
            "volume": np.array([r[5] for r in rows], dtype=float),
        },
    }


def needs_generation(last_signal_candle_ts, last_closed_ts) -> bool:
    """True ssi aucun signal n'existe encore pour cette bougie clôturée."""
    if last_signal_candle_ts is None:
        return True
    return last_closed_ts > last_signal_candle_ts


def generate_and_store(conn, symbol: str, timeframe: str, origin: str,
                       direction_threshold: float, sl_mult: float,
                       tp_mult: float) -> dict:
    """Scorre la dernière bougie close ; stocke si BUY/SELL nouveau.

    `origin` : 'manual' | 'auto'. Retourne un dict avec `signal`,
    `stored` (None si non stocké), `skipped` (None | 'neutral' | 'duplicate').
    """
    data = fetch_closed_candles(conn, symbol, timeframe)
    c = data["candles"]
    feats = features.compute_features(
        c["open"], c["high"], c["low"], c["close"], c["volume"])
    result = engine.generate_signal(feats, direction_threshold, sl_mult, tp_mult)

    if result["direction"] not in ("BUY", "SELL"):
        return {"signal": result, "stored": None, "skipped": "neutral",
                "candle_ts": data["last_ts_iso"], "candles_used": data["n"]}

    last_ts = store.latest_signal_candle_ts(conn, data["symbol"], data["timeframe"])
    if not needs_generation(last_ts, data["candle_ts"]):
        log.info("Signal Lab %s %s : doublon (bougie %s déjà scorée), ignoré",
                 data["symbol"], data["timeframe"], data["last_ts_iso"],
                 extra={"event": "SIGNAL_DUPLICATE"})
        return {"signal": result, "stored": None, "skipped": "duplicate",
                "candle_ts": data["last_ts_iso"], "candles_used": data["n"]}

    stored = store.insert_signal(conn, data["symbol"], data["timeframe"], result,
                                 candle_ts=data["candle_ts"], origin=origin)
    return {"signal": result, "stored": stored, "skipped": None,
            "candle_ts": data["last_ts_iso"], "candles_used": data["n"]}
