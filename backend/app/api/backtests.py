"""Endpoints backtesting (étape F) : les résultats sont persistés en base."""

from __future__ import annotations

import json
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.backtesting.engine import BacktestConfig, run_backtest
from app.strategies.registry import STRATEGIES

router = APIRouter(tags=["backtests"])


class BacktestRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    strategy: str = "ema_rsi_demo"
    strategy_params: dict = Field(default_factory=dict)
    initial_capital: float = Field(default=1000.0, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, le=0.05)
    slippage: float = Field(default=0.0005, ge=0, le=0.05)
    risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    stop_loss_pct: float = Field(default=0.02, ge=0, le=0.5)
    take_profit_pct: float = Field(default=0.0, ge=0, le=5)
    position_pct: float = Field(default=1.0, gt=0, le=1)
    allow_short: bool = True
    start: str | None = None  # ISO date, ex. "2025-06-01"
    end: str | None = None


def _load_candles(conn, symbol: str, timeframe: str, start: str | None, end: str | None):
    query = """SELECT ts, open, high, low, close, volume FROM market_data
               WHERE symbol = %s AND timeframe = %s"""
    params: list = [symbol, timeframe]
    if start:
        query += " AND ts >= %s"
        params.append(start)
    if end:
        query += " AND ts <= %s"
        params.append(end)
    query += " ORDER BY ts"
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"aucune donnée pour {symbol} {timeframe} — lance POST /api/market/sync",
        )
    return rows


@router.post("/api/backtests")
def create_backtest(req: BacktestRequest) -> dict:
    """Exécute un backtest et le persiste. Synchrone (quelques dizaines de ms)."""
    cls = STRATEGIES.get(req.strategy)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"stratégie inconnue : {req.strategy}")
    symbol = req.symbol.upper()

    with db.get_conn() as conn:
        rows = _load_candles(conn, symbol, req.timeframe, req.start, req.end)

    tss = [r[0].isoformat() for r in rows]
    candles = {
        "open": np.array([r[1] for r in rows]),
        "high": np.array([r[2] for r in rows]),
        "low": np.array([r[3] for r in rows]),
        "close": np.array([r[4] for r in rows]),
        "volume": np.array([r[5] for r in rows]),
    }
    strategy = cls(req.strategy_params or None)
    signals = strategy.generate(candles)

    cfg = BacktestConfig(
        initial_capital=req.initial_capital,
        fee_rate=req.fee_rate,
        slippage=req.slippage,
        risk_per_trade=req.risk_per_trade,
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
        position_pct=req.position_pct,
        allow_short=req.allow_short,
    )
    result = run_backtest(
        tss,
        candles["open"], candles["high"], candles["low"], candles["close"],
        signals, cfg, timeframe=req.timeframe,
    )

    config = {
        "strategy": req.strategy,
        "strategy_params": strategy.params,
        "symbol": symbol,
        "timeframe": req.timeframe,
        "initial_capital": req.initial_capital,
        "fee_rate": req.fee_rate,
        "slippage": req.slippage,
        "risk_per_trade": req.risk_per_trade,
        "stop_loss_pct": req.stop_loss_pct,
        "take_profit_pct": req.take_profit_pct,
        "position_pct": req.position_pct,
        "allow_short": req.allow_short,
        "start": req.start,
        "end": req.end,
        "n_candles": len(rows),
    }
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO backtests (strategy, symbol, timeframe, config, results)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (req.strategy, symbol, req.timeframe,
                 json.dumps(config), json.dumps(result)),
            )
            bt_id = str(cur.fetchone()[0])
        conn.commit()
    return {"id": bt_id, "summary": result["metrics"]}


@router.get("/api/backtests")
def list_backtests(limit: int = 50) -> dict:
    """Historique des backtests (résumés)."""
    limit = max(1, min(limit, 200))
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, created_at, strategy, symbol, timeframe,
                          results->'metrics'->>'total_return' AS ret,
                          results->'metrics'->>'max_drawdown' AS dd,
                          results->'metrics'->>'n_trades' AS n,
                          results->'metrics'->>'profit_factor' AS pf,
                          results->'metrics'->>'sharpe_ratio' AS sharpe
                   FROM backtests ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    return {"backtests": [
        {
            "id": str(r[0]), "created_at": r[1].isoformat(), "strategy": r[2],
            "symbol": r[3], "timeframe": r[4], "total_return": r[5],
            "max_drawdown": r[6], "n_trades": r[7], "profit_factor": r[8],
            "sharpe_ratio": r[9],
        } for r in rows
    ]}


@router.get("/api/backtests/{bt_id}")
def get_backtest(bt_id: str) -> dict:
    """Résultat complet d'un backtest (config, métriques, trades, courbe)."""
    try:
        uid = uuid.UUID(bt_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="id invalide")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, created_at, strategy, symbol, timeframe, config, results"
                " FROM backtests WHERE id = %s",
                (uid,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="backtest introuvable")
    return {
        "id": str(row[0]), "created_at": row[1].isoformat(), "strategy": row[2],
        "symbol": row[3], "timeframe": row[4], "config": row[5], "results": row[6],
    }
