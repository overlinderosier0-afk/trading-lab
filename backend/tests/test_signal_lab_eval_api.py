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
        # created_at = entry_timestamp + 10 s (retard d'entrée sous le seuil).
        for i, direction in enumerate(("BUY", "SELL")):
            entry_ts = T0 - timedelta(minutes=90 - i) + timedelta(minutes=5)
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO signal_lab_signals
                       (symbol, timeframe, direction, score, total, factors,
                        justification, indicators, entry_price, stop_loss,
                        take_profit, atr, horizon_minutes, resolve_at,
                        candle_ts, origin, entry_timestamp, entry_price_source,
                        created_at)
                       VALUES ('BTCUSDT','5m',%s,60,5.0,'{}','{}','{}',
                               100.0, 99.0, 102.0, 1.0, 15,
                               %s, %s, 'auto', %s, 'candle_close', %s)
                       RETURNING id""",
                    (direction, T0 + timedelta(minutes=15),
                     T0 - timedelta(minutes=90 - i),
                     entry_ts, entry_ts + timedelta(seconds=10)),
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
        # Clés renommées : flats/flat_rate, plus de neutrals/neutral_rate.
        assert "flats" in row and "flat_rate" in row
        assert "neutrals" not in row and "neutral_rate" not in row


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
    # Tri created_at DESC : items[0] = SELL (return < 0), items[1] = BUY (> 0).
    assert items[0]["evaluations"]["5"]["result"] == "LOSS"
    assert items[1]["evaluations"]["5"]["result"] == "WIN"
    sid = items[0]["id"]
    r = client.get(f"/api/signal-lab/eval/signals/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["evaluations"]) == 4
    assert all(e["status"] == "COMPLETED" for e in body["evaluations"])
    assert body["direction"] in ("BUY", "SELL")
    expected = "WIN" if body["direction"] == "BUY" else "LOSS"
    assert all(e["result"] == expected for e in body["evaluations"])
    assert "SIMULATED" in body["disclaimer"]


@needs_pg
def test_signals_404(client):
    r = client.get("/api/signal-lab/eval/signals/00000000-0000-0000-0000-"
                   "000000000000")
    assert r.status_code == 404


@needs_pg
def test_result_flat_in_list_and_detail(client):
    # Signal supplémentaire avec une évaluation COMPLETED à return = 0.
    entry_ts = T0 - timedelta(minutes=30)
    with psycopg.connect(TEST_DSN) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO signal_lab_signals
                   (symbol, timeframe, direction, score, total, factors,
                    justification, indicators, entry_price, stop_loss,
                    take_profit, atr, horizon_minutes, resolve_at,
                    candle_ts, origin, entry_timestamp, entry_price_source,
                    created_at)
                   VALUES ('ETHUSDT','5m','BUY',55,5.0,'{}','{}','{}',
                           200.0, 199.0, 202.0, 1.0, 15,
                           %s, %s, 'auto', %s, 'candle_close', %s)
                   RETURNING id""",
                (T0 + timedelta(minutes=15), T0 - timedelta(minutes=35),
                 entry_ts, entry_ts + timedelta(seconds=10)),
            )
            sid = str(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO signal_lab_evaluations
                   (signal_id, horizon_minutes, entry_price, exit_price,
                    return_pct, direction_correct, mfe_pct, mae_pct,
                    status, evaluated_at, due_at, exit_timestamp)
                   VALUES (%s, 15, 200.0, 200.0, 0.0, NULL, 0.1, -0.1,
                           'COMPLETED', %s, %s, %s)""",
                (sid, T0, entry_ts + timedelta(minutes=15),
                 entry_ts + timedelta(minutes=15)),
            )
        c.commit()
    r = client.get("/api/signal-lab/eval/signals",
                   params={"symbol": "ETHUSDT"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["evaluations"]["15"]["result"] == "FLAT"
    r = client.get(f"/api/signal-lab/eval/signals/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["evaluations"]) == 1
    assert body["evaluations"][0]["result"] == "FLAT"
    assert body["evaluations"][0]["return_pct"] == 0.0


@needs_pg
def test_breakdown_tranches(client):
    r = client.get("/api/signal-lab/eval/breakdown",
                   params={"group_by": "score_range", "horizon": 5})
    assert r.status_code == 200
    groups = r.json()["groups"]
    # Les 2 signaux ont score 60 -> tranche 60-69.
    t = next(g for k, g in groups.items() if k == "60-69")
    assert t["n"] == 2


# ------------------------------------------------------- garde-fou entry_lag
@pytest.fixture()
def client_with_lag():
    """1 signal frais + COMPLETED, 1 signal lagué + COMPLETED (manuelle)."""
    with psycopg.connect(TEST_DSN) as c:
        c.execute(open(SCHEMA_PATH, encoding="utf-8").read())
        c.execute("TRUNCATE signal_lab_evaluations, signal_lab_signals")
        now = datetime.now(timezone.utc)
        sids = {}
        for name, lag_s in (("fresh", 5.0), ("lagged", 120.0)):
            entry_ts = now - timedelta(seconds=lag_s)
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO signal_lab_signals
                       (symbol, timeframe, direction, score, total, factors,
                        justification, indicators, entry_price, stop_loss,
                        take_profit, atr, horizon_minutes, resolve_at,
                        candle_ts, origin, entry_timestamp, entry_price_source,
                        created_at)
                       VALUES ('BTCUSDT','5m','BUY',60,5.0,'{}','{}','{}',
                               100.0, 99.0, 102.0, 1.0, 15,
                               %s, %s, 'auto', %s, 'candle_close', %s)
                       RETURNING id""",
                    (now, entry_ts - timedelta(minutes=5), entry_ts, now),
                )
                sid = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO signal_lab_evaluations
                       (signal_id, horizon_minutes, entry_price, exit_price,
                        exit_timestamp, return_pct, direction_correct,
                        mfe_pct, mae_pct, status, evaluated_at, due_at)
                       VALUES (%s, 5, 100.0, 101.0, %s, 1.0, true, 1.0, -0.5,
                               'COMPLETED', %s, %s)""",
                    (sid, now, now, entry_ts + timedelta(minutes=5)),
                )
                sids[name] = str(sid)
        c.commit()
    app = FastAPI()
    app.include_router(signal_lab_eval.router)
    return TestClient(app), sids


@needs_pg
def test_summary_excludes_lagged_and_exposes_threshold(client_with_lag):
    client, _sids = client_with_lag
    r = client.get("/api/signal-lab/eval/summary", params={"horizon": 5})
    assert r.status_code == 200
    block = r.json()["horizons"]["5"]
    # La COMPLETED du signal lagué est exclue des stats à la lecture…
    assert block["completed_signals"] == 1
    assert block["wins"] == 1
    # …le signal lagué est compté en unavailable, sans double comptage…
    assert block["unavailable_signals"] == 1
    assert block["total_signals"] == 2
    # …et le seuil utilisé est exposé.
    assert block["max_entry_lag_seconds"] == \
        settings.signal_lab_eval_max_entry_lag_seconds == 60.0


@needs_pg
def test_signals_list_and_detail_expose_unavailable_reason(client_with_lag):
    client, sids = client_with_lag
    r = client.get("/api/signal-lab/eval/signals")
    assert r.status_code == 200
    by_id = {it["id"]: it for it in r.json()["items"]}
    assert by_id[sids["fresh"]]["evaluation_state"] == "available"
    assert by_id[sids["fresh"]]["unavailable_reason"] is None
    assert by_id[sids["lagged"]]["evaluation_state"] == "unavailable"
    assert by_id[sids["lagged"]]["unavailable_reason"] == "entry_lag"
    r = client.get(f"/api/signal-lab/eval/signals/{sids['lagged']}")
    assert r.status_code == 200
    body = r.json()
    assert body["evaluation_state"] == "unavailable"
    assert body["unavailable_reason"] == "entry_lag"
    # La ligne COMPLETED existe toujours en base (garde-fou à la lecture).
    assert body["evaluations"][0]["status"] == "COMPLETED"


@needs_pg
def test_breakdown_exposes_threshold(client_with_lag):
    client, _sids = client_with_lag
    r = client.get("/api/signal-lab/eval/breakdown",
                   params={"group_by": "horizon"})
    assert r.status_code == 200
    body = r.json()
    assert body["max_entry_lag_seconds"] == 60.0
    # Le signal lagué est exclu des stats groupées aussi.
    assert body["groups"]["5"]["n"] == 1


@needs_pg
def test_alias_prefix_evaluation(client):
    """Les deux préfixes /eval/* et /evaluation/* servent les mêmes
    handlers avec le même contenu (décision #6)."""
    r1 = client.get("/api/signal-lab/eval/summary")
    r2 = client.get("/api/signal-lab/evaluation/summary")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json() == r1.json()

    items = client.get("/api/signal-lab/eval/signals").json()["items"]
    sid = items[0]["id"]
    r1 = client.get(f"/api/signal-lab/eval/signals/{sid}")
    r2 = client.get(f"/api/signal-lab/evaluation/signals/{sid}")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json() == r1.json()

    r1 = client.get("/api/signal-lab/eval/breakdown",
                    params={"group_by": "horizon"})
    r2 = client.get("/api/signal-lab/evaluation/breakdown",
                    params={"group_by": "horizon"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json() == r1.json()
