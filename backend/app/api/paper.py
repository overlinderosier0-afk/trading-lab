"""Endpoints paper trading (AUCUN argent réel — virtuel uniquement)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db
from app.paper_trading import engine as paper_engine
from app.risk import limits

router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/account")
def paper_account() -> dict:
    """Compte virtuel : capital, cash, equity (mark-to-market), positions."""
    with db.get_conn() as conn:
        account = paper_engine.get_account(conn)
        equity = paper_engine.account_equity(conn, account)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM paper_positions WHERE account_id = %s",
                        (account["id"],))
            n_pos = cur.fetchone()[0]
    return {
        "paper_trading": True,
        "notice": "PAPER TRADING — AUCUN ARGENT RÉEL",
        "capital_initial": account["capital_initial"],
        "cash": round(account["cash"], 2),
        "equity": round(equity, 2),
        "unrealized_pnl": round(equity - account["cash"], 2),
        "open_positions": n_pos,
        "trading_enabled": limits.kill_switch_on(),
        "paper_trading_enabled": limits.paper_trading_on(),
    }


@router.get("/positions")
def paper_positions() -> dict:
    """Positions virtuelles ouvertes avec PnL latent."""
    with db.get_conn() as conn:
        account = paper_engine.get_account(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT symbol, timeframe, direction, qty, entry_price, entry_ts,
                          stop_price, take_price, notional
                   FROM paper_positions WHERE account_id = %s ORDER BY entry_ts""",
                (account["id"],),
            )
            rows = cur.fetchall()
        out = []
        for symbol, timeframe, direction, qty, entry_px, entry_ts, stop_px, take_px, notional in rows:
            price = paper_engine._latest_price(conn, symbol, timeframe)
            qty, entry_px = float(qty), float(entry_px)
            upnl = (qty * (price - entry_px) if direction == "long"
                    else qty * (entry_px - price)) if price else 0.0
            out.append({
                "symbol": symbol, "timeframe": timeframe, "direction": direction,
                "qty": qty, "entry_price": entry_px,
                "entry_ts": entry_ts.isoformat(),
                "current_price": price,
                "unrealized_pnl": round(upnl, 2),
                "unrealized_pct": round(upnl / float(notional), 4) if notional else 0.0,
                "stop_price": float(stop_px) if stop_px else None,
                "take_price": float(take_px) if take_px else None,
            })
    return {"positions": out}


@router.get("/trades")
def paper_trades(limit: int = 100) -> dict:
    """Historique des trades virtuels clôturés."""
    limit = max(1, min(limit, 500))
    with db.get_conn() as conn:
        account = paper_engine.get_account(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT symbol, timeframe, direction, qty, entry_price, entry_ts,
                          exit_price, exit_ts, fees, pnl, pnl_pct, exit_reason
                   FROM paper_trades WHERE account_id = %s
                   ORDER BY exit_ts DESC LIMIT %s""",
                (account["id"], limit),
            )
            rows = cur.fetchall()
    return {"trades": [
        {
            "symbol": r[0], "timeframe": r[1], "direction": r[2], "qty": float(r[3]),
            "entry_price": float(r[4]), "entry_ts": r[5].isoformat(),
            "exit_price": float(r[6]), "exit_ts": r[7].isoformat(),
            "fees": float(r[8]), "pnl": float(r[9]), "pnl_pct": float(r[10]),
            "exit_reason": r[11],
        } for r in rows
    ]}


@router.get("/performance")
def paper_performance() -> dict:
    """Performance réalisée du compte virtuel."""
    with db.get_conn() as conn:
        account = paper_engine.get_account(conn)
        equity = paper_engine.account_equity(conn, account)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pnl, fees FROM paper_trades WHERE account_id = %s",
                (account["id"],),
            )
            rows = cur.fetchall()
    pnls = [float(r[0]) for r in rows]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), -sum(losses)
    peak = limits.get_state("equity_peak", account["capital_initial"])
    peak = float(peak) if peak else account["capital_initial"]
    dd = (peak - equity) / peak if peak > 0 else 0.0
    return {
        "notice": "PAPER TRADING — AUCUN ARGENT RÉEL",
        "capital_initial": account["capital_initial"],
        "equity": round(equity, 2),
        "total_return": round(equity / account["capital_initial"] - 1, 6),
        "n_trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "profit_factor": round(gp / gl, 4) if gl > 0 else None,
        "expectancy": round(sum(pnls) / n, 4) if n else 0.0,
        "total_fees": round(sum(float(r[1]) for r in rows), 2),
        "drawdown_from_peak": round(dd, 6),
    }


@router.get("/signals")
def paper_signals(limit: int = 50) -> dict:
    """Derniers signaux générés par le scheduler."""
    limit = max(1, min(limit, 200))
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, symbol, timeframe, strategy, signal, price, indicators
                   FROM signals ORDER BY ts DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    return {"signals": [
        {"ts": r[0].isoformat(), "symbol": r[1], "timeframe": r[2],
         "strategy": r[3], "signal": r[4], "price": float(r[5]),
         "indicators": r[6]}
        for r in rows
    ]}


@router.post("/start")
def paper_start() -> dict:
    """Active le paper trading (explicite, jamais automatique au premier déploiement)."""
    limits.set_state("paper_trading_enabled", True)
    limits.set_state("trading_enabled", True)
    limits.log_event("INFO", "PAPER_START", "Paper trading ACTIVÉ manuellement")
    return {"paper_trading_enabled": True,
            "notice": "PAPER TRADING — AUCUN ARGENT RÉEL"}


@router.post("/stop")
def paper_stop() -> dict:
    """Kill switch : bloque immédiatement toute nouvelle position."""
    limits.set_state("trading_enabled", False)
    limits.log_event("WARNING", "PAPER_STOP",
                     "Kill switch activé : aucune nouvelle position (existantes en gestion)")
    return {"trading_enabled": False}
