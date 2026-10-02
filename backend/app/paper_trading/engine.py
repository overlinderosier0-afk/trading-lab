"""Moteur de paper trading : exécute VIRTUELLEMENT les signaux.

AUCUN ordre réel n'est jamais envoyé — il n'existe d'ailleurs aucun module
d'exécution d'ordres réels dans ce système.

Fonctionnement (par paire symbol/timeframe, sur la dernière bougie CLOSE) :
1. synchro incrémentale des données ;
2. calcul du signal + persistance (idempotent) ;
3. vérification stop-loss / take-profit des positions ouvertes ;
4. application du signal (flip long/short/flat) après garde-fous risk ;
5. exécution au CLOSE de la bougie de signal + slippage (proxy du prix live ;
   le backtest, plus conservateur, exécute à l'open suivant — différence assumée).
"""

from __future__ import annotations

import logging

import numpy as np

from app import db
from app.backtesting.engine import BacktestConfig, _exec_price, position_notional
from app.config import settings
from app.market_data import sync as market_sync
from app.risk import limits
from app.strategies.registry import STRATEGIES

log = logging.getLogger("tradinglab")
ACCOUNT_ID = "default"
STRATEGY = "ema_rsi_demo"
WINDOW = 300  # bougies chargées pour le calcul des indicateurs


def _bt_config() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=settings.initial_capital,
        fee_rate=settings.fee_rate,
        slippage=settings.slippage,
        risk_per_trade=settings.risk_per_trade,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        position_pct=settings.position_pct,
        allow_short=settings.allow_short,
    )


def _pairs() -> list[tuple[str, str]]:
    syms = [s.strip() for s in settings.market_data_symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in settings.market_data_timeframes.split(",") if t.strip()]
    return [(s, t) for s in syms for t in tfs]


def get_account(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id, capital_initial, cash FROM paper_accounts WHERE id = %s",
                    (ACCOUNT_ID,))
        row = cur.fetchone()
        if row:
            return {"id": row[0], "capital_initial": float(row[1]), "cash": float(row[2])}
        cur.execute(
            "INSERT INTO paper_accounts (id, capital_initial, cash) VALUES (%s, %s, %s)",
            (ACCOUNT_ID, settings.initial_capital, settings.initial_capital),
        )
    conn.commit()
    limits.log_event("INFO", "PAPER_START",
                     f"Compte paper créé : capital virtuel {settings.initial_capital:.2f} USD")
    return {"id": ACCOUNT_ID, "capital_initial": float(settings.initial_capital),
            "cash": float(settings.initial_capital)}


def _latest_price(conn, symbol: str, timeframe: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT close FROM market_data WHERE symbol = %s AND timeframe = %s"
            " ORDER BY ts DESC LIMIT 1",
            (symbol, timeframe),
        )
        row = cur.fetchone()
        return float(row[0]) if row else None


def account_equity(conn, account: dict) -> float:
    """Cash + PnL latent des positions ouvertes."""
    equity = account["cash"]
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, timeframe, direction, qty, entry_price"
                    " FROM paper_positions WHERE account_id = %s", (account["id"],))
        for symbol, timeframe, direction, qty, entry_price in cur.fetchall():
            price = _latest_price(conn, symbol, timeframe)
            if price is None:
                continue
            qty, entry_price = float(qty), float(entry_price)
            equity += qty * (price - entry_price) if direction == "long" \
                else qty * (entry_price - price)
    return equity


def _open_position(conn, account: dict, symbol: str, timeframe: str,
                   direction: str, price: float, ts: str, cfg: BacktestConfig) -> dict | None:
    equity = account_equity(conn, account)
    allowed, reason = limits.check_open_allowed(conn, account, equity)
    if not allowed:
        limits.log_event("WARNING", "RISK_LIMIT",
                         f"Ouverture {direction} {symbol} {timeframe} bloquée : {reason}",
                         {"symbol": symbol, "timeframe": timeframe, "reason": reason})
        return {"status": "blocked", "reason": reason}
    notional = position_notional(equity, cfg)
    if notional < 10.0:
        return {"status": "skipped", "reason": "notional_too_small"}
    buying = direction == "long"
    px = _exec_price(price, buying, cfg.slippage)
    qty = notional / px
    fee = qty * px * cfg.fee_rate
    cash = account["cash"] + (-qty * px - fee if buying else qty * px - fee)
    stop_px = (px * (1 - cfg.stop_loss_pct) if buying else px * (1 + cfg.stop_loss_pct)) \
        if cfg.stop_loss_pct > 0 else None
    take_px = (px * (1 + cfg.take_profit_pct) if buying else px * (1 - cfg.take_profit_pct)) \
        if cfg.take_profit_pct > 0 else None
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO paper_positions
               (account_id, symbol, timeframe, direction, qty, entry_price, entry_ts,
                stop_price, take_price, notional, entry_fee)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (account["id"], symbol, timeframe, direction, qty, px, ts,
             stop_px, take_px, notional, fee),
        )
        cur.execute("UPDATE paper_accounts SET cash = %s, updated_at = now() WHERE id = %s",
                    (cash, account["id"]))
    conn.commit()
    account["cash"] = cash
    limits.log_event("INFO", "PAPER_ORDER",
                     f"PAPER {direction.upper()} {symbol} {timeframe} : {qty:.6f} @ {px:.2f}",
                     {"symbol": symbol, "timeframe": timeframe, "direction": direction,
                      "qty": qty, "price": px, "notional": notional})
    return {"status": "opened", "direction": direction, "qty": qty, "price": px}


def _close_position(conn, account: dict, pos: dict, price: float, ts: str,
                    reason: str, cfg: BacktestConfig) -> dict:
    buying = pos["direction"] == "short"  # pour clôturer un short, on achète
    px = _exec_price(price, buying, cfg.slippage)
    qty = float(pos["qty"])
    entry_px = float(pos["entry_price"])
    if pos["direction"] == "long":
        fee = qty * px * cfg.fee_rate
        cash = account["cash"] + qty * px - fee
        pnl = qty * (px - entry_px) - (float(pos["entry_fee"]) + fee)
    else:
        fee = qty * px * cfg.fee_rate
        cash = account["cash"] - qty * px - fee
        pnl = qty * (entry_px - px) - (float(pos["entry_fee"]) + fee)
    pnl_pct = pnl / float(pos["notional"]) if float(pos["notional"]) > 0 else 0.0
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO paper_trades
               (account_id, symbol, timeframe, direction, qty,
                entry_price, entry_ts, exit_price, exit_ts, fees, pnl, pnl_pct, exit_reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (account["id"], pos["symbol"], pos["timeframe"], pos["direction"], qty,
             entry_px, pos["entry_ts"], px, ts,
             float(pos["entry_fee"]) + fee, pnl, pnl_pct, reason),
        )
        cur.execute("DELETE FROM paper_positions WHERE id = %s", (pos["id"],))
        cur.execute("UPDATE paper_accounts SET cash = %s, updated_at = now() WHERE id = %s",
                    (cash, account["id"]))
    conn.commit()
    account["cash"] = cash
    limits.log_event("INFO", "PAPER_CLOSE",
                     f"PAPER CLOSE {pos['direction'].upper()} {pos['symbol']} : "
                     f"PnL {pnl:+.2f} ({pnl_pct:+.2%}) [{reason}]",
                     {"symbol": pos["symbol"], "pnl": pnl, "reason": reason})
    return {"status": "closed", "pnl": pnl, "reason": reason}


def _get_position(conn, account_id: str, symbol: str, timeframe: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, symbol, timeframe, direction, qty, entry_price, entry_ts,
                      stop_price, take_price, notional, entry_fee
               FROM paper_positions
               WHERE account_id = %s AND symbol = %s AND timeframe = %s""",
            (account_id, symbol, timeframe),
        )
        row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "symbol", "timeframe", "direction", "qty", "entry_price",
            "entry_ts", "stop_price", "take_price", "notional", "entry_fee"]
    pos = dict(zip(keys, row))
    pos["entry_ts"] = pos["entry_ts"].isoformat()
    return pos


def _persist_signal(conn, symbol: str, timeframe: str, ts: str,
                    signal: str, price: float, indicators: dict) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signals (ts, symbol, timeframe, strategy, signal, price, indicators)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ts, symbol, timeframe, strategy) DO NOTHING""",
            (ts, symbol, timeframe, STRATEGY, signal, price, json.dumps(indicators)),
        )
    conn.commit()


def cycle_pair(symbol: str, timeframe: str) -> dict:
    """Un cycle complet pour une paire. Idempotent (rejouer = sans effet)."""
    import json
    import numpy as np
    from app.strategies.registry import STRATEGIES

    cfg = _bt_config()
    sym = symbol.replace("/", "").upper()
    with db.get_conn() as conn:
        market_sync.sync_symbol(conn, sym, timeframe, lookback_days=3)
        account = get_account(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume FROM market_data
                   WHERE symbol = %s AND timeframe = %s ORDER BY ts DESC LIMIT %s""",
                (sym, timeframe, WINDOW),
            )
            rows = cur.fetchall()
    if len(rows) < 60:
        return {"symbol": sym, "timeframe": timeframe, "status": "skipped",
                "reason": "not_enough_data"}
    rows = list(reversed(rows))
    tss = [r[0].isoformat() for r in rows]
    # colonnes : ts, open, high, low, close, volume
    candles = {
        "open": np.array([r[1] for r in rows], dtype=float),
        "high": np.array([r[2] for r in rows], dtype=float),
        "low": np.array([r[3] for r in rows], dtype=float),
        "close": np.array([r[4] for r in rows], dtype=float),
        "volume": np.array([r[5] for r in rows], dtype=float),
    }
    strat = STRATEGIES[STRATEGY]()
    signals = strat.generate(candles)
    last, sig = rows[-1], signals[-1]
    ts, o, h, lo, cl = last[0].isoformat(), float(last[1]), float(last[2]), \
        float(last[3]), float(last[4])
    ind = strat.indicator_values(candles) if hasattr(strat, "indicator_values") else {}
    indicators = {k: (None if np.isnan(v[-1]) else round(float(v[-1]), 4))
                  for k, v in ind.items()}

    with db.get_conn() as conn:
        account = get_account(conn)
        _persist_signal(conn, sym, timeframe, ts, sig, cl, indicators)
        pos = _get_position(conn, account["id"], sym, timeframe)
        actions = []

        # 1) stop-loss / take-profit sur la dernière bougie close
        if pos and cfg.stop_loss_pct > 0:
            stop_px = float(pos["stop_price"]) if pos["stop_price"] else None
            take_px = float(pos["take_price"]) if pos["take_price"] else None
            hit_stop = stop_px is not None and (
                lo <= stop_px if pos["direction"] == "long" else h >= stop_px)
            hit_take = take_px is not None and (
                h >= take_px if pos["direction"] == "long" else lo <= take_px)
            if hit_stop:
                actions.append(_close_position(conn, account, pos, stop_px, ts, "stop_loss", cfg))
                pos = None
            elif hit_take:
                actions.append(_close_position(conn, account, pos, take_px, ts, "take_profit", cfg))
                pos = None

        # 2) application du signal (BUY -> long, SELL -> short, HOLD -> garder)
        target = {"BUY": "long", "SELL": "short"}.get(sig)
        if target == "short" and not settings.allow_short:
            target = None
        current = pos["direction"] if pos else None
        if target and target != current:
            if pos:
                actions.append(_close_position(conn, account, pos, cl, ts, "signal", cfg))
            actions.append(_open_position(conn, account, sym, timeframe, target, cl, ts, cfg))
        elif not target and pos:
            # SELL avec short interdit alors qu'on est long -> on clôt sans rouvrir
            if sig == "SELL":
                actions.append(_close_position(conn, account, pos, cl, ts, "signal", cfg))

        equity = account_equity(conn, account)
        limits.update_peak(equity)
    return {"symbol": sym, "timeframe": timeframe, "status": "ok",
            "signal": sig, "close": cl, "actions": actions, "equity": round(equity, 2)}


def run_cycle() -> dict:
    """Cycle complet sur toutes les paires configurées."""
    if not limits.can_trade()[0]:
        return {"status": "disabled",
                "reason": limits.can_trade()[1]}
    results = []
    for symbol, timeframe in _pairs():
        try:
            results.append(cycle_pair(symbol, timeframe))
        except Exception as exc:
            limits.log_event("ERROR", "PAPER_ERROR",
                             f"Cycle {symbol} {timeframe} : {type(exc).__name__}",
                             {"symbol": symbol, "timeframe": timeframe})
            results.append({"symbol": symbol, "timeframe": timeframe,
                            "status": "error"})
    return {"status": "ok", "pairs": results}
