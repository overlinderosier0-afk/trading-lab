"""Signal Lab — persistance : signaux générés, résolution, calibration.

Table `signal_lab_signals` (créée par schema.sql, idempotent).
Un signal NEUTRAL n'est jamais stocké : seuls BUY/SELL sont suivis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import psycopg

log = logging.getLogger("tradinglab")

# Horizon : 3 bougies après génération (le signal "joue" 3 closes).
HORIZON_CANDLES = 3

TIMEFRAME_MINUTES = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

# Tranches de score pour la calibration honnête.
CALIBRATION_BUCKETS = [(0, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 100)]

# Sans données de résolution après ce délai : signal expiré (pas de verdict).
EXPIRE_AFTER_DAYS = 7


def horizon_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"timeframe non supporté par Signal Lab : {timeframe}")
    return TIMEFRAME_MINUTES[timeframe] * HORIZON_CANDLES


def insert_signal(
    conn: psycopg.Connection,
    symbol: str,
    timeframe: str,
    result: dict,
) -> dict:
    """Stocke un signal BUY/SELL. Retourne id + resolve_at."""
    hm = horizon_minutes(timeframe)
    resolve_at = datetime.now(timezone.utc) + timedelta(minutes=hm)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_signals
               (symbol, timeframe, direction, score, total, factors, justification,
                indicators, entry_price, stop_loss, take_profit, atr,
                horizon_minutes, resolve_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id, created_at""",
            (
                symbol, timeframe, result["direction"], result["score"], result["total"],
                psycopg.types.json.Json(result["factors"]),
                psycopg.types.json.Json(result["justification"]),
                psycopg.types.json.Json(result["indicators"]),
                result["entry"], result["stop_loss"], result["take_profit"],
                result["atr"], hm, resolve_at,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    log.info("Signal Lab %s %s %s score=%s", symbol, timeframe,
             result["direction"], result["score"], extra={"event": "SIGNAL_GENERATED"})
    return {"id": str(row[0]), "created_at": row[1].isoformat(),
            "resolve_at": resolve_at.isoformat()}


def list_signals(
    conn: psycopg.Connection,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = [], []
    if symbol:
        where.append("symbol = %s")
        params.append(symbol)
    if timeframe:
        where.append("timeframe = %s")
        params.append(timeframe)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, created_at, symbol, timeframe, direction, score, total,
                       factors, justification, indicators, entry_price, stop_loss,
                       take_profit, atr, horizon_minutes, resolve_at, outcome,
                       exit_price, sl_hit, tp_hit, resolved_at
                FROM signal_lab_signals {clause}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def universe(conn: psycopg.Connection) -> dict:
    """Paires/timeframes réellement disponibles en base."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT symbol, timeframe, COUNT(*), MAX(ts)
               FROM market_data GROUP BY symbol, timeframe ORDER BY symbol, timeframe"""
        )
        rows = cur.fetchall()
    symbols = sorted({r[0] for r in rows})
    timeframes = sorted({r[1] for r in rows},
                        key=lambda t: TIMEFRAME_MINUTES.get(t, 9999))
    return {
        "symbols": symbols,
        "timeframes": [t for t in timeframes if t in TIMEFRAME_MINUTES],
        "detail": [
            {"symbol": r[0], "timeframe": r[1], "candles": r[2],
             "last_ts": r[3].isoformat() if r[3] else None}
            for r in rows
        ],
    }


def _resolve_one(conn: psycopg.Connection, sig: dict) -> str:
    """Résout UN signal échu. Retourne le verdict."""
    sid = sig["id"]
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # Prix à l'horizon : première bougie close dont ts >= resolve_at.
        cur.execute(
            """SELECT ts, open, high, low, close FROM market_data
               WHERE symbol = %s AND timeframe = %s AND ts >= %s
               ORDER BY ts ASC LIMIT 1""",
            (sig["symbol"], sig["timeframe"], sig["resolve_at"]),
        )
        exit_row = cur.fetchone()
        if exit_row is None:
            if now - sig["resolve_at"] > timedelta(days=EXPIRE_AFTER_DAYS):
                cur.execute(
                    """UPDATE signal_lab_signals SET outcome='expired',
                       resolved_at=now() WHERE id=%s""", (sid,))
                conn.commit()
                return "expired"
            return "pending"  # données pas encore sync — on réessaiera

        exit_price = float(exit_row[4])
        # SL/TP touchés entre génération et horizon (informatif).
        cur.execute(
            """SELECT MIN(low), MAX(high) FROM market_data
               WHERE symbol = %s AND timeframe = %s AND ts > %s AND ts <= %s""",
            (sig["symbol"], sig["timeframe"], sig["created_at"], sig["resolve_at"]),
        )
        wrow = cur.fetchone()
        lo, hi = (float(wrow[0]), float(wrow[1])) if wrow[0] is not None else (None, None)
        if sig["direction"] == "BUY":
            sl_hit = lo is not None and lo <= sig["stop_loss"]
            tp_hit = hi is not None and hi >= sig["take_profit"]
            outcome = "win" if exit_price >= sig["entry_price"] else "loss"
        else:
            sl_hit = hi is not None and hi >= sig["stop_loss"]
            tp_hit = lo is not None and lo <= sig["take_profit"]
            outcome = "win" if exit_price <= sig["entry_price"] else "loss"

        cur.execute(
            """UPDATE signal_lab_signals
               SET outcome=%s, exit_price=%s, sl_hit=%s, tp_hit=%s, resolved_at=now()
               WHERE id=%s""",
            (outcome, exit_price, sl_hit, tp_hit, sid),
        )
    conn.commit()
    log.info("Signal Lab %s résolu : %s (exit=%s)", sid, outcome, exit_price,
             extra={"event": "SIGNAL_RESOLVED"})
    return outcome


def resolve_due_signals(conn: psycopg.Connection, limit: int = 200) -> dict:
    """Résout tous les signaux échus non résolus. Retourne les compteurs."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, created_at, resolve_at, symbol, timeframe, direction,
                      entry_price, stop_loss, take_profit
               FROM signal_lab_signals
               WHERE outcome = 'pending' AND resolve_at <= %s
               ORDER BY resolve_at ASC LIMIT %s""",
            (now, limit),
        )
        cols = [d.name for d in cur.description]
        due = [dict(zip(cols, r)) for r in cur.fetchall()]
    counts = {"win": 0, "loss": 0, "expired": 0, "pending": 0}
    for sig in due:
        counts[_resolve_one(conn, sig)] += 1
    return {"resolved": len(due), **counts}


def calibration_stats(
    conn: psycopg.Connection,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict:
    """Taux de réussite OBSERVÉ par tranche de score (signaux résolus).

    C'est la seule "probabilité" honnête du système : elle vient des
    résultats mesurés, pas du score.
    """
    where, params = ["outcome IN ('win','loss')"], []
    if symbol:
        where.append("symbol = %s")
        params.append(symbol)
    if timeframe:
        where.append("timeframe = %s")
        params.append(timeframe)
    clause = "WHERE " + " AND ".join(where)
    buckets = []
    with conn.cursor() as cur:
        for lo, hi in CALIBRATION_BUCKETS:
            cur.execute(
                f"""SELECT COUNT(*),
                           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END)
                    FROM signal_lab_signals {clause} AND score BETWEEN %s AND %s""",
                (*params, lo, hi),
            )
            n, wins = cur.fetchone()
            n, wins = int(n or 0), int(wins or 0)
            buckets.append({
                "range": f"{lo}–{hi}",
                "n": n,
                "wins": wins,
                "win_rate": round(wins / n, 3) if n else None,
            })
        cur.execute(
            f"""SELECT COUNT(*), SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END)::float
                         / NULLIF(COUNT(*),0)
                FROM signal_lab_signals {clause}""", params)
        n, wins, rate = cur.fetchone()
        pending_clause = clause.replace(
            "outcome IN ('win','loss')", "outcome = 'pending'")
        cur.execute(
            f"SELECT COUNT(*) FROM signal_lab_signals {pending_clause}",
            params)
        pending = int(cur.fetchone()[0])
    return {
        "symbol": symbol, "timeframe": timeframe,
        "buckets": buckets,
        "total": {"n": int(n or 0), "wins": int(wins or 0),
                  "win_rate": round(float(rate), 3) if rate is not None else None},
        "pending": pending,
        "note": "Le score /100 mesure la force du modèle, pas une probabilité. "
                "Seul le taux observé ci-dessus (sur signaux résolus) indique "
                "ce que le système a réellement produit.",
    }
