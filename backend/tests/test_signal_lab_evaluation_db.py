"""Tests DB — migration, triggers, worker d'évaluation.

Base locale dédiée (jamais la prod) : TEST_DATABASE_URL ou
postgresql://postgres:postgres@localhost:5432/tradinglab_test.
Skip si Postgres injoignable.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app.signal_lab import eval_store, eval_worker, store

UTC = timezone.utc
T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/tradinglab_test",
)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "schema.sql")


def _can_connect():
    try:
        with psycopg.connect(TEST_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(not _can_connect(),
                              reason="Postgres local injoignable")


@pytest.fixture()
def conn():
    with psycopg.connect(TEST_DSN) as c:
        c.execute(open(SCHEMA_PATH, encoding="utf-8").read())
        c.execute("TRUNCATE signal_lab_evaluations, signal_lab_signals")
        c.commit()
        yield c


def _insert_signal(conn, **kw):
    """Ligne signal minimale, façon legacy (colonnes historiques)."""
    d = dict(symbol="BTCUSDT", timeframe="5m", direction="BUY", score=55,
             total=10.0, entry_price=100.0,
             candle_ts=T0, origin="auto")
    d.update(kw)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_signals
               (symbol, timeframe, direction, score, total, factors,
                justification, indicators, entry_price, stop_loss,
                take_profit, atr, horizon_minutes, resolve_at,
                candle_ts, origin, entry_timestamp, entry_price_source)
               VALUES (%s,%s,%s,%s,%s,'{}','{}','{}',%s,99.0,101.0,1.0,
                       15, %s, %s, %s, %s, %s)
               RETURNING id""",
            (d["symbol"], d["timeframe"], d["direction"], d["score"],
             d["total"], d["entry_price"], T0 + timedelta(minutes=15),
             d["candle_ts"], d["origin"], d.get("entry_timestamp"),
             d.get("entry_price_source")),
        )
        sid = cur.fetchone()[0]
    conn.commit()
    return sid


def _candles_1m(start, n, price=100.0):
    out = []
    for i in range(n):
        o = int((start + timedelta(minutes=i)).timestamp() * 1000)
        out.append({"open_ms": o, "open": price, "high": price,
                    "low": price, "close": price, "volume": 1.0,
                    "close_ms": o + 60_000})
    return out


# ---------------------------------------------------------------- migration
@needs_pg
def test_migration_adds_columns_and_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'signal_lab_signals'
               AND column_name IN ('entry_timestamp','entry_price_source')""")
        assert {r[0] for r in cur.fetchall()} == {
            "entry_timestamp", "entry_price_source"}
        cur.execute("SELECT to_regclass('signal_lab_evaluations')")
        assert cur.fetchone()[0] == "signal_lab_evaluations"


@needs_pg
def test_backfill_entry_timestamp_from_candle_ts(conn):
    sid = _insert_signal(conn, entry_timestamp=None, entry_price_source=None)
    conn.execute(open(SCHEMA_PATH, encoding="utf-8").read())  # re-run = backfill
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT entry_timestamp, entry_price_source "
                    "FROM signal_lab_signals WHERE id = %s", (sid,))
        ts, src = cur.fetchone()
    assert ts == T0 + timedelta(minutes=5)  # close de la bougie 5m
    assert src == "candle_close"


@needs_pg
def test_backfill_1h_timeframe(conn):
    sid = _insert_signal(conn, timeframe="1h",
                         candle_ts=datetime(2026, 10, 4, 11, 0, tzinfo=UTC),
                         entry_timestamp=None, entry_price_source=None)
    conn.execute(open(SCHEMA_PATH, encoding="utf-8").read())
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT entry_timestamp FROM signal_lab_signals "
                    "WHERE id = %s", (sid,))
        assert cur.fetchone()[0] == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@needs_pg
def test_no_candle_ts_stays_unavailable(conn):
    sid = _insert_signal(conn, candle_ts=None, entry_timestamp=None,
                         entry_price_source=None)
    conn.execute(open(SCHEMA_PATH, encoding="utf-8").read())
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT entry_timestamp FROM signal_lab_signals "
                    "WHERE id = %s", (sid,))
        assert cur.fetchone()[0] is None


@needs_pg
def test_trigger_refuses_history_update(conn):
    sid = _insert_signal(conn)
    with pytest.raises(Exception, match="immutable"):
        with conn.cursor() as cur:
            cur.execute("UPDATE signal_lab_signals SET entry_price = 1 "
                        "WHERE id = %s", (sid,))
    conn.rollback()
    with pytest.raises(Exception, match="immutable"):
        with conn.cursor() as cur:
            cur.execute("UPDATE signal_lab_signals SET symbol = 'XXX' "
                        "WHERE id = %s", (sid,))
    conn.rollback()


@needs_pg
def test_entry_timestamp_immutable_once_set(conn):
    # NULL -> valeur autorisé (backfill), mais jamais modifié ensuite :
    # verrou anti look-ahead.
    sid = _insert_signal(conn, entry_timestamp=None, entry_price_source=None)
    with conn.cursor() as cur:
        cur.execute("UPDATE signal_lab_signals SET entry_timestamp = %s "
                    "WHERE id = %s", (T0, sid))
    conn.commit()
    with pytest.raises(Exception, match="immutable"):
        with conn.cursor() as cur:
            cur.execute("UPDATE signal_lab_signals SET entry_timestamp = %s "
                        "WHERE id = %s", (T0 + timedelta(hours=1), sid))
    conn.rollback()
    # entry_price_source : NULL -> 'candle_close' autorisé, changement refusé.
    with conn.cursor() as cur:
        cur.execute("UPDATE signal_lab_signals "
                    "SET entry_price_source = 'candle_close' WHERE id = %s",
                    (sid,))
    conn.commit()
    with pytest.raises(Exception, match="immutable"):
        with conn.cursor() as cur:
            cur.execute("UPDATE signal_lab_signals "
                        "SET entry_price_source = 'manual' WHERE id = %s",
                        (sid,))
    conn.rollback()


@needs_pg
def test_trigger_allows_resolver_update(conn):
    # Le resolveur existant (outcome/exit_price/...) doit continuer à marcher.
    sid = _insert_signal(conn)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE signal_lab_signals
               SET outcome='loss', exit_price=99.0, sl_hit=true,
                   tp_hit=false, resolved_at=now()
               WHERE id = %s""", (sid,))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT outcome FROM signal_lab_signals WHERE id = %s",
                    (sid,))
        assert cur.fetchone()[0] == "loss"


@needs_pg
def test_evaluations_unique_and_completed_immutable(conn):
    sid = _insert_signal(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_evaluations
               (signal_id, horizon_minutes, entry_price, due_at)
               VALUES (%s, 15, 100.0, now())""", (sid,))
    conn.commit()
    with pytest.raises(Exception, match="unique|duplicate"):
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO signal_lab_evaluations
                   (signal_id, horizon_minutes, entry_price, due_at)
                   VALUES (%s, 15, 100.0, now())""", (sid,))
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signal_lab_evaluations SET status='COMPLETED' "
            "WHERE signal_id = %s", (sid,))
    conn.commit()
    with pytest.raises(Exception, match="immutable"):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE signal_lab_evaluations SET return_pct = 9 "
                "WHERE signal_id = %s", (sid,))
    conn.rollback()


@needs_pg
def test_insert_signal_writes_entry_timestamp(conn):
    result = {"direction": "BUY", "score": 60, "total": 5.0, "factors": {},
              "justification": [], "indicators": {}, "entry": 100.0,
              "stop_loss": 99.0, "take_profit": 102.0, "atr": 1.0}
    out = store.insert_signal(conn, "BTCUSDT", "5m", result,
                              candle_ts=T0, origin="auto")
    with conn.cursor() as cur:
        cur.execute("SELECT entry_timestamp, entry_price_source "
                    "FROM signal_lab_signals WHERE id = %s", (out["id"],))
        ts, src = cur.fetchone()
    assert ts == T0 + timedelta(minutes=5)
    assert src == "candle_close"


# ---------------------------------------------------------------- worker
def _make_evaluable(conn, entry_ts, direction="BUY", price=100.0, horizon=5):
    sid = _insert_signal(conn, direction=direction, entry_price=price,
                         entry_timestamp=entry_ts,
                         entry_price_source="candle_close")
    return sid


@needs_pg
def test_worker_creates_pending_idempotent(conn):
    sid = _make_evaluable(conn, T0)
    n1 = eval_store.ensure_pending_evaluations(conn, [5, 15])
    n2 = eval_store.ensure_pending_evaluations(conn, [5, 15])
    assert (n1, n2) == (2, 0)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM signal_lab_evaluations "
                    "WHERE signal_id = %s", (sid,))
        assert cur.fetchone()[0] == 2


@needs_pg
def test_worker_skips_signal_without_entry(conn):
    _insert_signal(conn, candle_ts=None, entry_timestamp=None)
    n = eval_store.ensure_pending_evaluations(conn, [5, 15, 30, 60])
    assert n == 0
    assert eval_store.count_by_status(conn)["UNAVAILABLE"] == 1


@needs_pg
def test_worker_completes_due_evaluation(conn):
    entry_ts = T0  # minute pile
    _make_evaluable(conn, entry_ts, price=100.0, horizon=5)
    klines = _candles_1m(T0, 5, 100.0)
    klines[4] = dict(klines[4], close=101.0, high=101.0)
    now = T0 + timedelta(minutes=6)
    res = eval_worker.run_cycle(
        conn, klines_fetcher=lambda s, a, b: klines,
        now=now, horizons=(5,), batch=10)
    assert res == {"created": 1, "due": 1, "completed": 1,
                   "transient": 0, "permanent": 0}
    with conn.cursor() as cur:
        cur.execute("SELECT status, return_pct, direction_correct, exit_price"
                    " FROM signal_lab_evaluations")
        st, ret, ok, px = cur.fetchone()
    assert st == "COMPLETED" and ok is True and px == 101.0
    assert abs(ret - 1.0) < 1e-9


@needs_pg
def test_worker_pending_before_due(conn):
    _make_evaluable(conn, T0, horizon=60)
    now = T0 + timedelta(minutes=10)  # due_at = T0+60
    res = eval_worker.run_cycle(
        conn, klines_fetcher=lambda s, a, b: _candles_1m(T0, 60),
        now=now, horizons=(60,), batch=10)
    assert res["completed"] == 0 and res["due"] == 0
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM signal_lab_evaluations")
        assert cur.fetchone()[0] == "PENDING"


@needs_pg
def test_worker_late_uses_t_end_not_current_price(conn):
    # Worker très en retard : le prix de sortie reste celui de T_end.
    entry_ts = T0
    _make_evaluable(conn, entry_ts, price=100.0, horizon=5)
    klines = _candles_1m(T0, 300, 100.0)
    klines[4] = dict(klines[4], close=101.0, high=101.0)   # T_end = T0+5
    klines[200] = dict(klines[200], close=500.0, high=500.0)  # bien après
    now = T0 + timedelta(hours=5)
    res = eval_worker.run_cycle(
        conn, klines_fetcher=lambda s, a, b: klines,
        now=now, horizons=(5,), batch=10)
    assert res["completed"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT exit_price, return_pct FROM signal_lab_evaluations")
        px, ret = cur.fetchone()
    assert px == 101.0 and abs(ret - 1.0) < 1e-9


@needs_pg
def test_worker_transient_backoff_on_market_data_failure(conn):
    _make_evaluable(conn, T0, horizon=5)
    now = T0 + timedelta(minutes=6)

    def boom(s, a, b):
        raise RuntimeError("binance down")

    res = eval_worker.run_cycle(conn, klines_fetcher=boom, now=now,
                                horizons=(5,), batch=10)
    assert res["transient"] == 1 and res["completed"] == 0
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempt_count, next_retry_at "
                    "FROM signal_lab_evaluations")
        st, att, nxt = cur.fetchone()
    assert st == "PENDING" and att == 1 and nxt > now


@needs_pg
def test_worker_permanent_after_max_attempts(conn):
    _make_evaluable(conn, T0, horizon=5)
    now = T0 + timedelta(minutes=6)

    def boom(s, a, b):
        raise RuntimeError("binance down")

    res = eval_worker.run_cycle(conn, klines_fetcher=boom, now=now,
                                horizons=(5,), batch=10, max_attempts=1)
    assert res["permanent"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT status, last_error FROM signal_lab_evaluations")
        st, err = cur.fetchone()
    assert st == "ERROR" and err


@needs_pg
def test_worker_two_cycles_no_duplication(conn):
    _make_evaluable(conn, T0, horizon=5)
    klines = _candles_1m(T0, 5, 100.0)
    now = T0 + timedelta(minutes=6)
    fetch = lambda s, a, b: klines
    r1 = eval_worker.run_cycle(conn, klines_fetcher=fetch, now=now,
                               horizons=(5,), batch=10)
    r2 = eval_worker.run_cycle(conn, klines_fetcher=fetch, now=now,
                               horizons=(5,), batch=10)
    assert r1["completed"] == 1 and r2["completed"] == 0 and r2["due"] == 0
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM signal_lab_evaluations")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT return_pct FROM signal_lab_evaluations")
        first = cur.fetchone()[0]
    # Pas de recalcul silencieux : le résultat est inchangé.
    assert first is not None
