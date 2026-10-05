"""Tests API /api/signal-lab/eval — sur Postgres local dédié (jamais la prod)."""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

UTC = timezone.utc
T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)

os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/tradinglab_test",
)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import signal_lab_eval  # noqa: E402
from app.config import settings  # noqa: E402
from app.signal_lab import eval_store, eval_worker  # noqa: E402

TEST_DSN = os.environ["TEST_DATABASE_URL"]
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")


@pytest.fixture(scope="module", autouse=True)
def _point_settings_at_test_db():
    """Le singleton settings peut déjà exister (autre module importé avant) :
    on le re-pointe vers la base de test et on restaure après."""
    old = settings.database_url
    settings.database_url = TEST_DSN
    yield
    settings.database_url = old


def _can_connect():
    try:
        with psycopg.connect(TEST_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(not _can_connect(),
                              reason="Postgres local injoignable")


@pytest.fixture()
def client():
    with psycopg.connect(TEST_DSN) as c:
        c.execute(open(SCHEMA_PATH, encoding="utf-8").read())
        c.execute("TRUNCATE signal_lab_evaluations, signal_lab_signals")
        # 2 signaux évaluables, horizons 5/15/30/60.
        for i, direction in enumerate(("BUY", "SELL")):
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO signal_lab_signals
                       (symbol, timeframe, direction, score, total, factors,
                        justification, indicators, entry_price, stop_loss,
                        take_profit, atr, horizon_minutes, resolve_at,
                        candle_ts, origin, entry_timestamp, entry_price_source)
                       VALUES ('BTCUSDT','5m',%s,60,5.0,'{}','{}','{}',
                               100.0, 99.0, 102.0, 1.0, 15,
                               %s, %s, 'auto', %s, 'candle_close')
                       RETURNING id""",
                    (direction, T0 + timedelta(minutes=15),
                     T0 - timedelta(minutes=90 - i),
                     T0 - timedelta(minutes=90 - i) + timedelta(minutes=5)),
                )
                sid = cur.fetchone()[0]
            eval_store.ensure_pending_evaluations(c, [5, 15, 30, 60])
        # Complète toutes les évaluations : prix monte 100 -> 101.
        klines = []
        start = T0 - timedelta(minutes=90)
        for i in range(200):
            o = int((start + timedelta(minutes=i)).timestamp() * 1000)
            px = 100.0 + i * 0.01
            klines.append({"open_ms": o, "open": px, "high": px + 0.005,
                           "low": px - 0.005, "close": px, "volume": 1.0,
                           "close_ms": o + 60_000})
        eval_worker.run_cycle(
            c, klines_fetcher=lambda s, a, b: klines,
            now=T0 + timedelta(hours=2),
            horizons=(5, 15, 30, 60), batch=100)
        c.commit()

    app = FastAPI()
    app.include_router(signal_lab_eval.router)
    return TestClient(app)


@needs_pg
def test_summary_shape_and_simulated_tag(client):
    r = client.get("/api/signal-lab/eval/summary")
    assert r.status_code == 200
    body = r.json()
    assert "SIMULATED" in body["disclaimer"]
    for h in ("5", "15", "30", "60"):
        row = body["horizons"][h]
        assert row["completed_signals"] == 2
        assert row["win_rate"] is not None
        assert row["paper_pnl"]["simulated"] is True


@needs_pg
def test_summary_filters_symbol_timeframe(client):
    r = client.get("/api/signal-lab/eval/summary",
                   params={"symbol": "ETHUSDT"})
    assert r.status_code == 200
    assert r.json()["horizons"]["5"]["completed_signals"] == 0
    r = client.get("/api/signal-lab/eval/summary",
                   params={"symbol": "BTCUSDT", "timeframe": "1h"})
    assert r.json()["horizons"]["5"]["completed_signals"] == 0


@needs_pg
def test_signals_list_and_detail(client):
    r = client.get("/api/signal-lab/eval/signals")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    sid = items[0]["id"]
    r = client.get(f"/api/signal-lab/eval/signals/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["evaluations"]) == 4
    assert all(e["status"] == "COMPLETED" for e in body["evaluations"])
    assert body["direction"] in ("BUY", "SELL")
    assert "SIMULATED" in body["disclaimer"]


@needs_pg
def test_signals_404(client):
    r = client.get("/api/signal-lab/eval/signals/00000000-0000-0000-0000-"
                   "000000000000")
    assert r.status_code == 404


@needs_pg
def test_breakdown_tranches(client):
    r = client.get("/api/signal-lab/eval/breakdown",
                   params={"group_by": "score_range", "horizon": 5})
    assert r.status_code == 200
    groups = r.json()["groups"]
    # Les 2 signaux ont score 60 -> tranche 60-69.
    t = next(g for k, g in groups.items() if k == "60-69")
    assert t["n"] == 2
