"""Endpoints validation (IS/OOS + walk-forward). Résultats persistés en base."""

from __future__ import annotations

import json
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db, validation
from app.backtesting.engine import BacktestConfig
from app.strategies.registry import STRATEGIES

router = APIRouter(tags=["validation"])


class ValidationBase(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    strategy: str = "ema_rsi_demo"
    strategy_params: dict = Field(default_factory=dict)
    param_grid: dict[str, list] | None = None
    initial_capital: float = Field(default=1000.0, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, le=0.05)
    slippage: float = Field(default=0.0005, ge=0, le=0.05)
    risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    stop_loss_pct: float = Field(default=0.02, ge=0, le=0.5)
    take_profit_pct: float = Field(default=0.0, ge=0, le=5)
    position_pct: float = Field(default=1.0, gt=0, le=1)
    allow_short: bool = True
    min_trades: int = Field(default=20, ge=1, le=500)


class IsOosRequest(ValidationBase):
    is_ratio: float = Field(default=0.7, ge=0.5, le=0.9)


class WalkForwardRequest(ValidationBase):
    train_days: int = Field(default=180, ge=30, le=730)
    test_days: int = Field(default=30, ge=7, le=180)
    step_days: int | None = Field(default=None, ge=7, le=365)


def _load_candles(symbol: str, timeframe: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts, open, high, low, close, volume FROM market_data
                   WHERE symbol = %s AND timeframe = %s ORDER BY ts""",
                (symbol, timeframe),
            )
            rows = cur.fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"aucune donnée pour {symbol} {timeframe} — lance POST /api/market/sync",
        )
    return rows


def _build_inputs(req: ValidationBase):
    cls = STRATEGIES.get(req.strategy)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"stratégie inconnue : {req.strategy}")
    symbol = req.symbol.upper()
    if req.timeframe not in ("1h", "4h", "1d"):
        raise HTTPException(status_code=422, detail="timeframe : 1h, 4h ou 1d")
    rows = _load_candles(symbol, req.timeframe)
    data = validation.SliceInput(
        tss=[r[0].isoformat() for r in rows],
        candles={
            "open": np.array([r[1] for r in rows]),
            "high": np.array([r[2] for r in rows]),
            "low": np.array([r[3] for r in rows]),
            "close": np.array([r[4] for r in rows]),
            "volume": np.array([r[5] for r in rows]),
        },
        timeframe=req.timeframe,
    )
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
    return cls, symbol, data, cfg


def _persist(kind: str, req: ValidationBase, symbol: str, config_extra: dict, results: dict) -> str:
    config = {
        "strategy": req.strategy,
        "strategy_params": req.strategy_params,
        "param_grid": req.param_grid,
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
        "min_trades": req.min_trades,
        **config_extra,
    }
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO validation_runs (kind, strategy, symbol, timeframe, config, results)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (kind, req.strategy, symbol, req.timeframe,
                 json.dumps(config), json.dumps(results)),
            )
            run_id = str(cur.fetchone()[0])
        conn.commit()
    return run_id


@router.post("/api/validation/is-oos")
def run_is_oos(req: IsOosRequest) -> dict:
    """IS/OOS : avec param_grid, optimise sur IS puis évalue sur OOS (honnête)."""
    cls, symbol, data, cfg = _build_inputs(req)
    try:
        results = validation.is_oos(
            cls, req.strategy_params or {}, req.param_grid, data, cfg,
            is_ratio=req.is_ratio, min_trades=req.min_trades,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    run_id = _persist("is_oos", req, symbol, {"is_ratio": req.is_ratio}, results)
    return {"id": run_id, "kind": "is_oos", "degradation": results["degradation"]}


@router.post("/api/validation/walk-forward")
def run_walk_forward(req: WalkForwardRequest) -> dict:
    """Walk-forward roulant : grille optimisée par pli TRAIN, évaluée sur TEST."""
    if not req.param_grid:
        raise HTTPException(
            status_code=422,
            detail="param_grid requis pour le walk-forward (ex. {\"ema_fast\": [10, 20]})",
        )
    cls, symbol, data, cfg = _build_inputs(req)
    try:
        results = validation.walk_forward(
            cls, req.param_grid, data, cfg,
            train_days=req.train_days, test_days=req.test_days,
            step_days=req.step_days, min_trades=req.min_trades,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    run_id = _persist("walk_forward", req, symbol, {
        "train_days": req.train_days, "test_days": req.test_days,
        "step_days": req.step_days or req.test_days,
    }, results)
    return {"id": run_id, "kind": "walk_forward", "aggregate": results["aggregate"]}


@router.get("/api/validation/runs")
def list_runs(limit: int = 50) -> dict:
    """Historique des validations (résumés)."""
    limit = max(1, min(limit, 200))
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, created_at, kind, strategy, symbol, timeframe, results
                   FROM validation_runs ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        res = r[6] or {}
        summary = res.get("degradation") if r[2] == "is_oos" else res.get("aggregate")
        out.append({
            "id": str(r[0]), "created_at": r[1].isoformat(), "kind": r[2],
            "strategy": r[3], "symbol": r[4], "timeframe": r[5], "summary": summary,
        })
    return {"runs": out}


@router.get("/api/validation/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Résultat complet d'une validation."""
    try:
        uid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="id invalide")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, created_at, kind, strategy, symbol, timeframe, config, results"
                " FROM validation_runs WHERE id = %s",
                (uid,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="validation introuvable")
    return {
        "id": str(row[0]), "created_at": row[1].isoformat(), "kind": row[2],
        "strategy": row[3], "symbol": row[4], "timeframe": row[5],
        "config": row[6], "results": row[7],
    }
