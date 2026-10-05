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
    """Ligne signal minimale, façon legacy (colonnes historiques).

    Par défaut created_at = entry_timestamp + 5 s (signal frais, évaluable) ;
    passer created_at explicitement pour simuler un retard d'entrée.
    """
    d = dict(symbol="BTCUSDT", timeframe="5m", direction="BUY", score=55,
             total=10.0, entry_price=100.0,
             candle_ts=T0, origin="auto")
    d.update(kw)
    if d.get("created_at") is None:
        ets = d.get("entry_timestamp")
        d["created_at"] = (ets + timedelta(seconds=5) if ets
                           else datetime.now(UTC))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_signals
               (symbol, timeframe, direction, score, total, factors,
                justification, indicators, entry_price, stop_loss,
                take_profit, atr, horizon_minutes, resolve_at,
                candle_ts, origin, entry_timestamp, entry_price_source,
                created_at)
               VALUES (%s,%s,%s,%s,%s,'{}','{}','{}',%s,99.0,101.0,1.0,
                       15, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (d["symbol"], d["timeframe"], d["direction"], d["score"],
             d["total"], d["entry_price"], T0 + timedelta(minutes=15),
             d["candle_ts"], d["origin"], d.get("entry_timestamp"),
             d.get("entry_price_source"), d["created_at"]),
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


# ------------------------------------------------------- garde-fou entry_lag
def _lagged_signal(conn, lag_s=120.0, **kw):
    """Signal BUY dont le retard d'entrée dépasse le seuil (défaut 60 s)."""
    now = datetime.now(UTC)
    d = dict(entry_timestamp=now - timedelta(seconds=lag_s),
             entry_price_source="candle_close", created_at=now)
    d.update(kw)
    return _insert_signal(conn, **d)


@needs_pg
def test_worker_creates_no_pending_for_lagged_signal(conn):
    _lagged_signal(conn, lag_s=120.0)
    fresh = _make_evaluable(conn, datetime.now(UTC) - timedelta(seconds=5))
    n = eval_store.ensure_pending_evaluations(conn, [5])
    assert n == 1
    with conn.cursor() as cur:
        cur.execute("SELECT signal_id FROM signal_lab_evaluations")
        assert cur.fetchone()[0] == fresh


@needs_pg
def test_worker_ignores_due_pending_of_lagged_signal(conn):
    # Une PENDING existante d'un signal lagué : ni complétée, ni modifiée.
    now = datetime.now(UTC)
    entry_ts = now - timedelta(seconds=120)
    sid = _lagged_signal(conn, lag_s=120.0)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_evaluations
               (signal_id, horizon_minutes, entry_price, due_at, status)
               VALUES (%s, 5, 100.0, %s, 'PENDING') RETURNING id""",
            (sid, entry_ts + timedelta(minutes=5)))
        eid = cur.fetchone()[0]
    conn.commit()
    klines = _candles_1m(entry_ts, 10, 100.0)
    res = eval_worker.run_cycle(
        conn, klines_fetcher=lambda s, a, b: klines,
        now=now, horizons=(5,), batch=10)
    assert res["completed"] == 0 and res["transient"] == 0
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempt_count, last_error "
                    "FROM signal_lab_evaluations WHERE id = %s", (eid,))
        st, att, err = cur.fetchone()
    assert (st, att, err) == ("PENDING", 0, None)


@needs_pg
def test_count_by_status_counts_lagged_as_unavailable(conn):
    now = datetime.now(UTC)
    _lagged_signal(conn, lag_s=120.0)                       # entry_lag
    _insert_signal(conn, entry_timestamp=now - timedelta(seconds=5),
                   entry_price_source="candle_close",
                   created_at=now)                          # évaluable
    _insert_signal(conn, entry_timestamp=None, candle_ts=None)  # non dérivable
    counts = eval_store.count_by_status(conn)
    assert counts["UNAVAILABLE"] == 2


@needs_pg
def test_read_path_excludes_completed_of_lagged_signal(conn):
    # Une COMPLETED existante sur signal lagué : exclue des stats à la
    # lecture (ligne non modifiée), signal compté en unavailable.
    from app.signal_lab import evaluation as ev
    now = datetime.now(UTC)
    sid = _lagged_signal(conn, lag_s=120.0)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO signal_lab_evaluations
               (signal_id, horizon_minutes, entry_price, exit_price,
                exit_timestamp, return_pct, direction_correct,
                mfe_pct, mae_pct, status, evaluated_at, due_at)
               VALUES (%s, 5, 100.0, 101.0, %s, 1.0, true, 1.0, -0.5,
                       'COMPLETED', %s, %s)""",
            (sid, now, now, now - timedelta(minutes=115)))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT direction, created_at, entry_timestamp "
                    "FROM signal_lab_signals WHERE id = %s", (sid,))
        cols = [d[0] for d in cur.description]
        sig = dict(zip(cols, cur.fetchone()))
    # La fonction partagée dit non évaluable…
    assert ev.signal_evaluability(sig, 60.0) == (False, "entry_lag")
    # …donc count_by_status le compte en unavailable…
    assert eval_store.count_by_status(conn)["UNAVAILABLE"] == 1
    # …et la ligne COMPLETED reste intacte en base (garde-fou à la lecture).
    with conn.cursor() as cur:
        cur.execute("SELECT status, return_pct FROM signal_lab_evaluations")
        assert cur.fetchone() == ("COMPLETED", 1.0)


@needs_pg
def test_worker_and_api_agree_on_evaluability(conn):
    # Même signal → même verdict côté worker et côté fonction partagée
    # (l'API utilise exactement cette fonction).
    from app.signal_lab import evaluation as ev
    now = datetime.now(UTC)
    lagged = _lagged_signal(conn, lag_s=120.0)
    fresh = _insert_signal(conn, entry_timestamp=now - timedelta(seconds=5),
                           entry_price_source="candle_close", created_at=now)
    n = eval_store.ensure_pending_evaluations(conn, [5])
    assert n == 1  # worker : seul le signal frais donne un PENDING
    with conn.cursor() as cur:
        cur.execute("SELECT id, direction, created_at, entry_timestamp "
                    "FROM signal_lab_signals")
        cols = [d[0] for d in cur.description]
        verdicts = {str(r[0]): ev.signal_evaluability(dict(zip(cols, r)), 60.0)[0]
                    for r in cur.fetchall()}
    assert verdicts == {str(lagged): False, str(fresh): True}
    with conn.cursor() as cur:
        cur.execute("SELECT signal_id FROM signal_lab_evaluations")
        pending_for = {str(r[0]) for r in cur.fetchall()}
    assert pending_for == {str(fresh)} == {s for s, ok in verdicts.items() if ok}
