"""Risk management : module indépendant, aucune logique de trading ici.

Garde-fous :
- kill switch manuel (trading_enabled = false -> aucune nouvelle position)
- activation explicite du paper trading (paper_trading_enabled)
- nombre max de positions ouvertes
- perte journalière max (sur trades clôturés du jour)
- drawdown max -> circuit breaker (désactivation automatique + événement)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app import db
from app.config import settings

log = logging.getLogger("tradinglab")


# ---------------------------------------------------------- état système
def get_state(key: str, default=None):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM system_state WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else default


def set_state(key: str, value) -> None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_state (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                   updated_at = now()""",
                (key, json.dumps(value)),
            )
        conn.commit()


def log_event(level: str, event: str, message: str, details: dict | None = None) -> None:
    """Journalise dans system_events + logs structurés (jamais de secrets)."""
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO system_events (level, event, message, details)"
                    " VALUES (%s, %s, %s, %s)",
                    (level, event, message, json.dumps(details or {})),
                )
            conn.commit()
    except Exception as exc:
        log.error("log_event impossible : %s", type(exc).__name__)
    getattr(log, level.lower(), log.info)(message, extra={"event": event})


# ---------------------------------------------------------- décisions
def kill_switch_on() -> bool:
    """False = aucune nouvelle position (manuel ou circuit breaker)."""
    val = get_state("trading_enabled", None)
    return bool(settings.trading_enabled if val is None else val)


def paper_trading_on() -> bool:
    """Le paper trading doit être activé explicitement (défaut : off)."""
    val = get_state("paper_trading_enabled", None)
    return bool(settings.paper_trading_enabled if val is None else val)


def can_trade() -> tuple[bool, str]:
    """Les deux conditions pour ouvrir une position."""
    if not kill_switch_on():
        return False, "kill_switch"
    if not paper_trading_on():
        return False, "paper_trading_disabled"
    return True, "ok"


def check_open_allowed(conn, account: dict, equity: float) -> tuple[bool, str]:
    """Garde-fous avant d'ouvrir une position. Retourne (autorisé, raison)."""
    ok, reason = can_trade()
    if not ok:
        return False, reason
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM paper_positions WHERE account_id = %s",
                    (account["id"],))
        n_open = cur.fetchone()[0]
    if n_open >= settings.max_open_positions:
        return False, "max_open_positions"
    # perte journalière max (trades clôturés aujourd'hui, en UTC)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT coalesce(sum(pnl), 0) FROM paper_trades
               WHERE account_id = %s AND exit_ts >= date_trunc('day', now())""",
            (account["id"],),
        )
        day_pnl = float(cur.fetchone()[0])
    if day_pnl < -settings.max_daily_loss * account["capital_initial"]:
        log_event("WARNING", "RISK_LIMIT",
                  f"Perte journalière max atteinte ({day_pnl:.2f}) — nouvelles positions bloquées",
                  {"day_pnl": day_pnl})
        return False, "max_daily_loss"
    # circuit breaker : drawdown max sur l'equity (réalisé + latent)
    peak = get_state("equity_peak", None)
    if peak is None or equity > float(peak):
        set_state("equity_peak", equity)
        peak = equity
    drawdown = (float(peak) - equity) / float(peak) if float(peak) > 0 else 0.0
    if drawdown >= settings.max_drawdown:
        set_state("trading_enabled", False)
        log_event("CRITICAL", "RISK_LIMIT",
                  f"CIRCUIT BREAKER : drawdown {drawdown:.1%} >= {settings.max_drawdown:.0%} — "
                  "paper trading désactivé automatiquement",
                  {"drawdown": drawdown, "equity": equity})
        return False, "circuit_breaker"
    return True, "ok"


def update_peak(equity: float) -> None:
    peak = get_state("equity_peak", None)
    if peak is None or equity > float(peak):
        set_state("equity_peak", equity)


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
