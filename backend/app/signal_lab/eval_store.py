"""Signal Lab — évaluation observationnelle : persistance.

Une ligne par (signal, horizon). Création idempotente, claim avec
FOR UPDATE SKIP LOCKED (pas de chevauchement entre cycles lents).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import psycopg

from app.signal_lab import evaluation

log = logging.getLogger("tradinglab")


def ensure_pending_evaluations(conn: psycopg.Connection, horizons: list[int],
                               max_lag_seconds: float = 60.0) -> int:
    """Crée les lignes PENDING manquantes (idempotent).

    Uniquement pour les signaux évaluables selon
    evaluation.signal_evaluability (BUY/SELL, entry_timestamp dérivé,
    retard d'entrée ≤ max_lag_seconds) ; les autres seront comptés
    UNAVAILABLE à la lecture (aucune ligne créée, décision actée).
    Log SIGNAL_EVALUATION_CREATED.
    """
    if not horizons:
        return 0
    created = 0
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, direction, created_at, entry_timestamp, entry_price
               FROM signal_lab_signals
               WHERE direction IN ('BUY', 'SELL')
                 AND entry_timestamp IS NOT NULL"""
        )
        cols = [d[0] for d in cur.description]
        candidates = [dict(zip(cols, r)) for r in cur.fetchall()]
        for sig in candidates:
            ok, _reason = evaluation.signal_evaluability(sig, max_lag_seconds)
            if not ok:
                continue
            for h in horizons:
                cur.execute(
                    """INSERT INTO signal_lab_evaluations
                           (signal_id, horizon_minutes, entry_price, due_at,
                            status)
                       VALUES (%s, %s, %s,
                               %s + (%s || ' minutes')::interval, 'PENDING')
                       ON CONFLICT (signal_id, horizon_minutes) DO NOTHING""",
                    (sig["id"], h, sig["entry_price"],
                     sig["entry_timestamp"], h),
                )
                created += cur.rowcount or 0
    conn.commit()
    if created:
        log.info("Évaluations PENDING créées : %s", created,
                 extra={"event": "SIGNAL_EVALUATION_CREATED"})
    return created


def claim_due_evaluations(
    conn: psycopg.Connection,
    now: datetime,
    limit: int = 100,
) -> list[dict]:
    """Verrouille un lot d'évaluations dues (FOR UPDATE SKIP LOCKED)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT e.id, e.signal_id, e.horizon_minutes, e.entry_price,
                      e.attempt_count, s.created_at,
                      s.symbol, s.direction, s.entry_timestamp
               FROM signal_lab_evaluations e
               JOIN signal_lab_signals s ON s.id = e.signal_id
               WHERE e.status = 'PENDING'
                 AND e.due_at <= %s
                 AND (e.next_retry_at IS NULL OR e.next_retry_at <= %s)
               ORDER BY e.due_at ASC
               LIMIT %s
               FOR UPDATE OF e SKIP LOCKED""",
            (now, now, limit),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    # Pas de commit ici : le verrou est tenu jusqu'à la fin du cycle.
    return rows


def complete_evaluation(conn: psycopg.Connection, eval_id: str, result: dict,
                        now: datetime) -> None:
    """PENDING → COMPLETED (immuable ensuite, cf. trigger)."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE signal_lab_evaluations
               SET status = 'COMPLETED', exit_price = %s,
                   exit_timestamp = %s, return_pct = %s,
                   direction_correct = %s, mfe_pct = %s, mae_pct = %s,
                   evaluated_at = %s
               WHERE id = %s AND status = 'PENDING'""",
            (result["exit_price"], result["exit_timestamp"],
             result["return_pct"], result["direction_correct"],
             result["mfe_pct"], result["mae_pct"], now, eval_id),
        )
    conn.commit()


def fail_transient(conn: psycopg.Connection, eval_id: str, error: str,
                   now: datetime, backoff_max_minutes: int = 30) -> int:
    """Échec récupérable : PENDING conservé, backoff exponentiel plafonné."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT attempt_count FROM signal_lab_evaluations WHERE id = %s",
            (eval_id,),
        )
        attempt = (cur.fetchone() or [0])[0] + 1
        backoff_min = min(2 ** (attempt - 1), backoff_max_minutes)
        cur.execute(
            """UPDATE signal_lab_evaluations
               SET attempt_count = %s,
                   next_retry_at = %s + (%s || ' minutes')::interval,
                   last_error = %s
               WHERE id = %s AND status = 'PENDING'""",
            (attempt, now, backoff_min, error[:500], eval_id),
        )
    conn.commit()
    return attempt


def fail_permanent(conn: psycopg.Connection, eval_id: str, error: str) -> None:
    """Échec définitif : ERROR + last_error. Le signal n'est jamais supprimé."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE signal_lab_evaluations
               SET status = 'ERROR', last_error = %s
               WHERE id = %s AND status = 'PENDING'""",
            (error[:500], eval_id),
        )
    conn.commit()


def count_by_status(conn: psycopg.Connection,
                    max_lag_seconds: float = 60.0) -> dict:
    """Compteurs pour /summary : par statut (+ UNAVAILABLE dérivé à la lecture).

    UNAVAILABLE = signaux BUY/SELL non évaluables selon
    evaluation.signal_evaluability (fonction partagée avec le worker et
    l'API) : aucune ligne en base, compté à part, jamais en double.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT status, COUNT(*) FROM signal_lab_evaluations
               GROUP BY status"""
        )
        counts = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            """SELECT direction, created_at, entry_timestamp
               FROM signal_lab_signals
               WHERE direction IN ('BUY', 'SELL')"""
        )
        cols = [d[0] for d in cur.description]
        unavailable = sum(
            1 for r in cur.fetchall()
            if not evaluation.signal_evaluability(dict(zip(cols, r)),
                                                  max_lag_seconds)[0]
        )
    counts["UNAVAILABLE"] = unavailable
    return counts
