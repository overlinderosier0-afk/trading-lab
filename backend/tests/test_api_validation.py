"""Tests de câblage de l'API validation (sans PostgreSQL : DB mockée)."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import validation as validation_api


def make_rows(n: int = 1200, seed: int = 7):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0002, 0.006, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        (base + timedelta(hours=i), float(open_[i]), float(close[i] * 1.001),
         float(close[i] * 0.999), float(close[i]), 50.0)
        for i in range(n)
    ]


@pytest.fixture()
def client(monkeypatch):
    rows = make_rows(n=3000)
    monkeypatch.setattr(validation_api, "_load_candles", lambda s, tf: rows)
    monkeypatch.setattr(validation_api, "_persist",
                        lambda kind, req, symbol, extra, results: "fake-id-123")
    tapp = FastAPI()
    tapp.include_router(validation_api.router)
    return TestClient(tapp)


def test_is_oos_endpoint(client):
    res = client.post("/api/validation/is-oos", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "strategy": "ema_rsi_demo",
        "is_ratio": 0.7, "min_trades": 5,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == "fake-id-123"
    assert body["kind"] == "is_oos"
    assert body["degradation"]["verdict"] in (
        "sans_edge", "robuste", "dégradé", "sur-optimisé")


def test_is_oos_with_grid(client):
    res = client.post("/api/validation/is-oos", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "strategy": "ema_rsi_demo",
        "param_grid": {"ema_fast": [10, 20], "rsi_buy": [25, 30]},
        "min_trades": 5,
    })
    assert res.status_code == 200, res.text
    assert res.json()["degradation"]["verdict"]


def test_walk_forward_endpoint(client):
    res = client.post("/api/validation/walk-forward", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "strategy": "ema_rsi_demo",
        "param_grid": {"ema_fast": [10, 20]},
        "train_days": 60, "test_days": 15, "min_trades": 5,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "walk_forward"
    assert body["aggregate"]["profitable_folds"] >= 0


def test_walk_forward_rejects_missing_grid(client):
    res = client.post("/api/validation/walk-forward", json={
        "symbol": "BTCUSDT", "timeframe": "1h", "strategy": "ema_rsi_demo",
        "train_days": 60, "test_days": 15,
    })
    assert res.status_code == 422


def test_unknown_strategy_404(client):
    res = client.post("/api/validation/is-oos", json={"strategy": "nope"})
    assert res.status_code == 404


def test_strategies_endpoint_expose_edge_status():
    from app.api import strategies as strategies_api
    tapp = FastAPI()
    tapp.include_router(strategies_api.router)
    client = TestClient(tapp)
    res = client.get("/api/strategies")
    assert res.status_code == 200, res.text
    strats = {s["name"]: s for s in res.json()["strategies"]}
    assert strats["ema_rsi_demo"]["edge_status"] == "NO_EDGE_DEMONSTRATED"
    assert strats["ema_rsi_demo"]["edge_note"]


def test_health_not_caught_by_spa_fallback():
    """Régression : /health et /ready doivent être DÉFINIS avant le catch-all
    SPA dans main.py (le routage FastAPI matche dans l'ordre d'enregistrement ;
    avant le fix, /health retournait index.html au lieu du JSON)."""
    import pathlib
    src = pathlib.Path(__file__).parent.parent.joinpath("app", "main.py").read_text()
    pos_health = src.index('@app.get("/health")')
    pos_ready = src.index('@app.get("/ready")')
    pos_spa = src.index('"/{full_path:path}"')
    assert pos_health < pos_spa
    assert pos_ready < pos_spa
