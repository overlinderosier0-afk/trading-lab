"""Scheduler léger in-process (APScheduler) : une seule instance, pas de chevauchement.

- sync_job (30 min) : données de marché incrémentales — toujours actif.
- paper_job (15 min) : cycle de paper trading — seulement si activé (défaut : off).
- signal_lab_resolve_job (5 min) : résolution des signaux Signal Lab échus
  (mesure win/loss uniquement — ne génère rien, n'ouvre aucune position).
- signal_lab_auto_job (5 min) : auto-génération d'1 signal par bougie clôturée
  sur les paires configurées — seulement si SIGNAL_LAB_AUTO_ENABLED=true
  (mesure uniquement, aucune position).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import db
from app.market_data import sync as market_sync
from app.paper_trading import engine as paper_engine
from app.risk import limits
from app.signal_lab import service as signal_lab_service
from app.signal_lab import store as signal_lab_store

log = logging.getLogger("tradinglab")

_scheduler: BackgroundScheduler | None = None


def _pairs():
    from app.config import settings
    syms = [s.strip() for s in settings.market_data_symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in settings.market_data_timeframes.split(",") if t.strip()]
    return [(s, t) for s in syms for t in tfs]


def sync_job() -> None:
    """Données de marché : reprise incrémentale sur toutes les paires."""
    for symbol, timeframe in _pairs():
        try:
            with db.get_conn() as conn:
                r = market_sync.sync_symbol(conn, symbol, timeframe, lookback_days=2)
            log.info("Sync %s %s : +%s bougies, %s trous",
                     r["symbol"], r["timeframe"], r["inserted"], r["gaps"],
                     extra={"event": "DATA_FETCH"})
        except Exception as exc:
            limits.log_event("ERROR", "SYSTEM_ERROR",
                             f"sync_job {symbol} {timeframe} : {type(exc).__name__}",
                             {"symbol": symbol, "timeframe": timeframe})


def paper_job() -> None:
    """Cycle de paper trading (no-op si désactivé)."""
    try:
        result = paper_engine.run_cycle()
        if result.get("status") == "ok":
            n_actions = sum(len(p.get("actions", [])) for p in result.get("pairs", []))
            if n_actions:
                log.info("paper_job : %s actions", n_actions,
                         extra={"event": "PAPER_CYCLE"})
    except Exception as exc:
        limits.log_event("ERROR", "SYSTEM_ERROR",
                         f"paper_job : {type(exc).__name__}", {})


def signal_lab_resolve_job() -> None:
    """Résolution des signaux Signal Lab échus (mesure win/loss uniquement).

    Ne génère rien, n'ouvre aucune position : pure observation.
    """
    try:
        from app.config import settings
        if not settings.signal_lab_enabled:
            return
        with db.get_conn() as conn:
            result = signal_lab_store.resolve_due_signals(conn)
        if result["resolved"]:
            log.info("signal_lab_resolve : %s résolus %s",
                     result["resolved"], result, extra={"event": "SIGNAL_RESOLVED"})
    except Exception as exc:
        limits.log_event("ERROR", "SYSTEM_ERROR",
                         f"signal_lab_resolve_job : {type(exc).__name__}", {})


def signal_lab_auto_job() -> None:
    """Auto-génération : 1 signal par bougie clôturée (paires configurées).

    Mesure uniquement : ne génère aucun ordre, n'ouvre aucune position.
    La déduplication (1 signal / bougie) est assurée par le service.
    """
    try:
        from app.config import settings
        if not (settings.signal_lab_enabled and settings.signal_lab_auto_enabled):
            return
        syms = [s.strip().upper() for s in settings.market_data_symbols.split(",")
                if s.strip()]
        tfs = [t.strip() for t in settings.signal_lab_auto_timeframes.split(",")
               if t.strip()]
        for symbol in syms:
            for timeframe in tfs:
                try:
                    with db.get_conn() as conn:
                        # Rattrapage incrémental : la dernière bougie doit être fraîche.
                        market_sync.sync_symbol(conn, symbol, timeframe,
                                                lookback_days=1)
                        r = signal_lab_service.generate_and_store(
                            conn, symbol, timeframe, origin="auto",
                            direction_threshold=settings.signal_lab_direction_threshold,
                            sl_mult=settings.signal_lab_sl_atr_mult,
                            tp_mult=settings.signal_lab_tp_atr_mult)
                    if r["stored"]:
                        log.info("Signal Lab AUTO %s %s %s score=%s", symbol,
                                 timeframe, r["signal"]["direction"],
                                 r["signal"]["score"],
                                 extra={"event": "SIGNAL_GENERATED"})
                except signal_lab_service.InsufficientData as exc:
                    log.warning("signal_lab auto %s %s : %s", symbol, timeframe,
                                exc, extra={"event": "SIGNAL_SKIP"})
                except Exception as exc:
                    limits.log_event("ERROR", "SYSTEM_ERROR",
                                     f"signal_lab_auto {symbol} {timeframe} : "
                                     f"{type(exc).__name__}", {})
    except Exception as exc:
        limits.log_event("ERROR", "SYSTEM_ERROR",
                         f"signal_lab_auto_job : {type(exc).__name__}", {})


def signal_lab_eval_job() -> None:
    """Évaluation observationnelle des signaux (paper-trading de mesure).

    Bougies 1m récupérées à la demande (lecture seule, jamais stockées).
    Ne génère rien, n'ouvre aucune position.
    """
    try:
        from app.config import settings
        if not (settings.signal_lab_enabled and settings.signal_lab_eval_enabled):
            return
        from app.market_data import binance
        from app.signal_lab import eval_worker

        def fetch_1m(symbol: str, start_ms: int, end_ms: int) -> list:
            return binance.fetch_klines(symbol, "1m", start_ms=start_ms,
                                        end_ms=end_ms)

        horizons = tuple(int(h) for h in
                         settings.signal_lab_eval_horizons.split(",") if h.strip())
        with db.get_conn() as conn:
            res = eval_worker.run_cycle(
                conn, klines_fetcher=fetch_1m, horizons=horizons,
                max_attempts=settings.signal_lab_eval_max_attempts,
                backoff_max_minutes=settings.signal_lab_eval_backoff_max_minutes,
                max_entry_lag_seconds=settings.signal_lab_eval_max_entry_lag_seconds,
            )
        if res["completed"] or res["permanent"] or res["created"]:
            log.info("signal_lab_eval : %s", res,
                     extra={"event": "SIGNAL_EVALUATION_CYCLE"})
    except Exception as exc:
        limits.log_event("ERROR", "SYSTEM_ERROR",
                         f"signal_lab_eval_job : {type(exc).__name__}", {})


def start() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(sync_job, "interval", minutes=30,
                       max_instances=1, coalesce=True, id="sync_job")
    _scheduler.add_job(paper_job, "interval", minutes=15,
                       max_instances=1, coalesce=True, id="paper_job")
    _scheduler.add_job(signal_lab_resolve_job, "interval", minutes=5,
                       max_instances=1, coalesce=True, id="signal_lab_resolve_job")
    _scheduler.add_job(signal_lab_auto_job,
                       CronTrigger(minute="*/5", second="40", timezone="UTC"),
                       max_instances=1, coalesce=True, id="signal_lab_auto_job")
    _scheduler.add_job(signal_lab_eval_job, "interval", seconds=60,
                       max_instances=1, coalesce=True, id="signal_lab_eval_job")
    _scheduler.start()
    log.info("Scheduler démarré (sync 30min, paper 15min, signal-lab 5min, eval 60s)",
             extra={"event": "SCHEDULER_START"})


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler arrêté", extra={"event": "SCHEDULER_STOP"})


def is_running() -> bool:
    return bool(_scheduler and _scheduler.running)
