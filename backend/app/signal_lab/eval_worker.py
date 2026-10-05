"""Signal Lab — évaluation observationnelle : worker.

Cycle : créer les PENDING manquants → réclamer les dus → récupérer les
bougies 1m à la demande (lecture seule, jamais stockées) → calculer →
COMPLETED. Idempotent : un cycle relancé ne duplique ni ne modifie rien.

Le client market data et l'horloge sont injectés (tests sans réseau).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import psycopg

from app.signal_lab import eval_store, evaluation

log = logging.getLogger("tradinglab")

# Au-delà de ce délai après due_at, une évaluation jamais complétée
# devient une erreur permanente (données définitivement manquantes).
STALE_AFTER_HOURS = 24


def _window_ms(entry_timestamp: datetime, horizon_minutes: int) -> tuple[int, int]:
    start = evaluation.round_up_to_minute(entry_timestamp)
    end = entry_timestamp + timedelta(minutes=horizon_minutes)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def run_cycle(
    conn: psycopg.Connection,
    klines_fetcher,
    now: datetime | None = None,
    horizons: tuple[int, ...] = (5, 15, 30, 60),
    batch: int = 100,
    max_attempts: int = 12,
    backoff_max_minutes: int = 30,
) -> dict:
    """Exécute un cycle d'évaluation. Retourne les compteurs du cycle.

    klines_fetcher(symbol, start_ms, end_ms) -> list[dict] (bougies 1m,
    format fetch_klines) ; lève en cas d'indisponibilité.
    """
    now = now or datetime.now(timezone.utc)
    horizons = [int(h) for h in horizons]
    created = eval_store.ensure_pending_evaluations(conn, horizons)
    due = eval_store.claim_due_evaluations(conn, now, limit=batch)

    # Regroupe les appels par symbole : une seule requête 1m par symbole.
    by_symbol: dict[str, list[dict]] = {}
    for row in due:
        by_symbol.setdefault(row["symbol"], []).append(row)
    fetched: dict[str, list[dict] | Exception] = {}
    for symbol, rows in by_symbol.items():
        starts_ends = [_window_ms(r["entry_timestamp"], r["horizon_minutes"])
                       for r in rows]
        start_ms = min(s for s, _ in starts_ends)
        end_ms = max(e for _, e in starts_ends)
        try:
            fetched[symbol] = klines_fetcher(symbol, start_ms, end_ms)
        except Exception as exc:  # indisponibilité : tous ces dus patientent
            fetched[symbol] = exc

    completed = transient = permanent = 0
    for row in due:
        eid, horizon = row["id"], row["horizon_minutes"]
        data = fetched[row["symbol"]]
        if isinstance(data, Exception):
            transient += 1
            attempt = eval_store.fail_transient(
                conn, eid, f"market_data: {type(data).__name__}",
                now, backoff_max_minutes)
            _maybe_permanent(conn, row, now, attempt, max_attempts,
                             f"market_data: {type(data).__name__}")
            if _was_marked_error(conn, eid):
                permanent += 1
                transient -= 1
            continue
        start_ms, end_ms = _window_ms(row["entry_timestamp"], horizon)
        window = [c for c in data if start_ms <= c["open_ms"]
                  and c["close_ms"] <= end_ms]
        if not window:
            # Fenêtre vide alors qu'elle est due : données manquantes.
            transient += 1
            attempt = eval_store.fail_transient(
                conn, eid, "fenêtre 1m vide", now, backoff_max_minutes)
            _maybe_permanent(conn, row, now, attempt, max_attempts,
                             "fenêtre 1m vide")
            if _was_marked_error(conn, eid):
                permanent += 1
                transient -= 1
            continue
        try:
            result = evaluation.compute_evaluation(
                float(row["entry_price"]), row["direction"], window,
                horizon, row["entry_timestamp"])
        except Exception as exc:
            eval_store.fail_permanent(conn, eid, f"calcul: {exc}")
            permanent += 1
            _log_error(row, horizon, f"calcul: {exc}")
            continue
        if result is None:  # ne devrait pas arriver (fenêtre non vide)
            eval_store.fail_permanent(conn, eid, "calcul: résultat vide")
            permanent += 1
            continue
        eval_store.complete_evaluation(conn, eid, result, now)
        completed += 1
        log.info(
            "Évaluation %s %s %sm : return=%s%%",
            row["symbol"], row["direction"], horizon,
            round(result["return_pct"], 4),
            extra={"event": "SIGNAL_EVALUATION_COMPLETED",
                   "signal_id": str(row["signal_id"]), "symbol": row["symbol"],
                   "horizon": horizon,
                   "return_pct": round(result["return_pct"], 4)},
        )
    return {"created": created, "due": len(due), "completed": completed,
            "transient": transient, "permanent": permanent}


def _maybe_permanent(conn, row, now, attempt, max_attempts, error) -> None:
    """Bascule en ERROR si tentatives épuisées ou fenêtre trop ancienne."""
    due_at = row["entry_timestamp"] + timedelta(minutes=row["horizon_minutes"])
    if attempt >= max_attempts or (now - due_at) > timedelta(hours=STALE_AFTER_HOURS):
        eval_store.fail_permanent(conn, row["id"], error)
        _log_error(row, row["horizon_minutes"], error)


def _was_marked_error(conn, eval_id) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM signal_lab_evaluations WHERE id = %s",
                    (eval_id,))
        r = cur.fetchone()
        return bool(r and r[0] == "ERROR")


def _log_error(row, horizon, error) -> None:
    log.warning(
        "Évaluation %s %s %sm en erreur : %s",
        row["symbol"], row["direction"], horizon, error,
        extra={"event": "SIGNAL_EVALUATION_ERROR",
               "signal_id": str(row["signal_id"]), "symbol": row["symbol"],
               "horizon": horizon, "error": error[:200]},
    )
