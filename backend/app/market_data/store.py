"""Persistance des bougies : idempotente (ON CONFLICT DO NOTHING) et validée."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg

log = logging.getLogger("tradinglab")


def validate_candle(c: dict, now_ms: int) -> bool:
    """Garde-fous : prix strictement positifs, high/low cohérents, pas de futur."""
    try:
        if c["open_ms"] > now_ms + 60_000:
            return False
        o, h, lo, cl = c["open"], c["high"], c["low"], c["close"]
        if not (o > 0 and h > 0 and lo > 0 and cl > 0):
            return False
        if not (h >= lo and h >= o and h >= cl and lo <= o and lo <= cl):
            return False
        if c["volume"] < 0:
            return False
        return True
    except (KeyError, TypeError):
        return False


def upsert_candles(
    conn: psycopg.Connection, symbol: str, timeframe: str, candles: list[dict]
) -> tuple[int, int]:
    """Insère les bougies fermées, ignore les doublons. Retourne (insérées, rejetées)."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows: list[dict] = []
    rejected = 0
    for c in candles:
        # Bougie en cours de formation -> on ne la stocke pas (données incomplètes).
        if c["close_ms"] > now_ms:
            continue
        if not validate_candle(c, now_ms):
            rejected += 1
            continue
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": datetime.fromtimestamp(c["open_ms"] / 1000, tz=timezone.utc),
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
        })
    if rejected:
        log.warning(
            "%s bougies invalides ignorées (%s %s)",
            rejected, symbol, timeframe, extra={"event": "DATA_INVALID"},
        )
    if not rows:
        return 0, rejected
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO market_data (symbol, timeframe, ts, open, high, low, close, volume)
               VALUES (%(symbol)s, %(timeframe)s, %(ts)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s)
               ON CONFLICT (symbol, timeframe, ts) DO NOTHING""",
            rows,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted, rejected


def get_last_ts(conn: psycopg.Connection, symbol: str, timeframe: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(ts) FROM market_data WHERE symbol = %s AND timeframe = %s",
            (symbol, timeframe),
        )
        return cur.fetchone()[0]


def detect_gaps(
    conn: psycopg.Connection, symbol: str, timeframe: str, step_ms: int, since_ms: int
) -> list[dict]:
    """Trous = écarts de plus d'un pas entre bougies consécutives (tolérance 50 %)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT extract(epoch FROM ts)::bigint * 1000 AS ms FROM market_data
               WHERE symbol = %s AND timeframe = %s AND ts >= to_timestamp(%s / 1000.0)
               ORDER BY ts""",
            (symbol, timeframe, since_ms),
        )
        tss = [r[0] for r in cur.fetchall()]
    gaps = []
    for a, b in zip(tss, tss[1:]):
        if b - a > step_ms * 1.5:
            gaps.append({
                "after_ms": a,
                "before_ms": b,
                "missing": round((b - a) / step_ms) - 1,
            })
    return gaps
