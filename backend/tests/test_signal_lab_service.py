
"""Tests service Signal Lab : déduplication 1 signal / bougie (FakeConn)."""


def test_needs_generation_pure():
    from datetime import datetime, timezone

    from app.signal_lab.service import needs_generation

    t0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 4, 12, 5, tzinfo=timezone.utc)
    assert needs_generation(None, t1) is True      # premier signal
    assert needs_generation(t0, t0) is False       # même bougie -> doublon
    assert needs_generation(t0, t1) is True       # nouvelle bougie -> go
    assert needs_generation(t1, t0) is False      # passé -> non


def _rows(n, drift, vol, seed, start):
    """Lignes (ts, o, h, l, c, v) en ordre chronologique (ASC)."""
    import numpy as np
    from datetime import timedelta

    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, vol * 100, n)) + 1e-9
    high = np.maximum(open_, close) + spread * 0.5
    low = np.minimum(open_, close) - spread * 0.5
    volume = rng.uniform(50, 150, n)
    return [
        (start + timedelta(minutes=5 * i), float(open_[i]), float(high[i]),
         float(low[i]), float(close[i]), float(volume[i]))
        for i in range(n)
    ]


def _fake_conn(rows, state):
    """Fausse connexion : ORDER BY ts DESC + suivi de candle_ts stocké."""
    import uuid
    from datetime import datetime, timezone

    class FakeCursor:
        def __init__(self):
            self._one = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, q, p=None):
            if "MAX(candle_ts)" in q:
                self._one = (state.get("candle_ts"),)
            elif "INSERT INTO signal_lab_signals" in q:
                assert q.count("%s") == len(p), "placeholders != params"
                state["candle_ts"] = p[14]  # position de candle_ts
                state["origin"] = p[15]
                self._one = (uuid.uuid4(), datetime.now(timezone.utc))
            return self

        def fetchall(self):
            return list(reversed(rows))  # comme ORDER BY ts DESC

        def fetchone(self):
            return self._one

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    return FakeConn()


def test_generate_and_store_dedup():
    """2e appel sur la même bougie -> skipped=duplicate, rien stocké."""
    from datetime import datetime, timedelta, timezone

    from app.signal_lab import service

    start = datetime.now(timezone.utc) - timedelta(minutes=5 * 402)
    rows = _rows(400, 0.004, 0.004, 7, start)
    state = {}
    conn = _fake_conn(rows, state)

    out1 = service.generate_and_store(conn, "BTCUSDT", "5m", "auto", 15.0, 1.5, 2.0)
    assert out1["skipped"] is None
    assert out1["stored"] is not None
    assert state["origin"] == "auto"

    out2 = service.generate_and_store(conn, "BTCUSDT", "5m", "auto", 15.0, 1.5, 2.0)
    assert out2["skipped"] == "duplicate"
    assert out2["stored"] is None
    # le même signal est retourné (pas d'erreur), juste non stocké
    assert out2["signal"]["direction"] == out1["signal"]["direction"]


def test_generate_and_store_new_candle():
    """Nouvelle bougie clôturée -> nouveau signal stocké."""
    from datetime import datetime, timedelta, timezone

    from app.signal_lab import service

    start = datetime.now(timezone.utc) - timedelta(minutes=5 * 402)
    rows = _rows(400, 0.004, 0.004, 7, start)
    state = {}
    conn = _fake_conn(rows, state)

    out1 = service.generate_and_store(conn, "BTCUSDT", "5m", "auto", 15.0, 1.5, 2.0)
    assert out1["stored"] is not None

    # une bougie de plus se clôt (mêmes valeurs, ts + 5 min)
    last = rows[-1]
    rows.append((last[0] + timedelta(minutes=5),) + last[1:])
    out2 = service.generate_and_store(conn, "BTCUSDT", "5m", "auto", 15.0, 1.5, 2.0)
    assert out2["skipped"] is None
    assert out2["stored"] is not None
    assert out2["stored"]["id"] != out1["stored"]["id"]


def test_generate_and_store_neutral_not_stored():
    """NEUTRAL -> skipped=neutral, jamais stocké (seed 0 = NEUTRAL)."""
    from datetime import datetime, timedelta, timezone

    from app.signal_lab import service

    start = datetime.now(timezone.utc) - timedelta(minutes=5 * 402)
    rows = _rows(400, 0.0, 0.001, 0, start)
    out = service.generate_and_store(_fake_conn(rows, {}), "BTCUSDT", "5m",
                                     "auto", 15.0, 1.5, 2.0)
    assert out["signal"]["direction"] == "NEUTRAL"
    assert out["skipped"] == "neutral"
    assert out["stored"] is None
