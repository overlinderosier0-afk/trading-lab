-- Trading Lab — schéma minimal (étape C). Idempotent : exécuté à chaque démarrage.
CREATE TABLE IF NOT EXISTS market_data (
    symbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TIMESTAMPTZ NOT NULL,
    open      DOUBLE PRECISION NOT NULL,
    high      DOUBLE PRECISION NOT NULL,
    low       DOUBLE PRECISION NOT NULL,
    close     DOUBLE PRECISION NOT NULL,
    volume    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_market_data_sym_tf_ts
    ON market_data (symbol, timeframe, ts DESC);

-- Backtests persistés (étape F)
CREATE TABLE IF NOT EXISTS backtests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    config JSONB NOT NULL,
    results JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtests_created
    ON backtests (created_at DESC);

-- Paper trading (étape G) : AUCUN argent réel, positions virtuelles uniquement.
CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    capital_initial DOUBLE PRECISION NOT NULL,
    cash DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,               -- 'long' | 'short'
    qty DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_ts TIMESTAMPTZ NOT NULL,
    stop_price DOUBLE PRECISION,
    take_price DOUBLE PRECISION,
    notional DOUBLE PRECISION NOT NULL,
    entry_fee DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id TEXT NOT NULL REFERENCES paper_accounts(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    qty DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_ts TIMESTAMPTZ NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    exit_ts TIMESTAMPTZ NOT NULL,
    fees DOUBLE PRECISION NOT NULL,
    pnl DOUBLE PRECISION NOT NULL,
    pnl_pct DOUBLE PRECISION NOT NULL,
    exit_reason TEXT NOT NULL,              -- 'signal' | 'stop_loss' | 'take_profit'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_account
    ON paper_trades (account_id, exit_ts DESC);

-- Signaux générés par le scheduler (idempotent)
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signal TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    indicators JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ts, symbol, timeframe, strategy)
);
CREATE INDEX IF NOT EXISTS idx_signals_ts
    ON signals (ts DESC);

-- Journal des événements système
CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    details JSONB
);
CREATE INDEX IF NOT EXISTS idx_system_events_ts
    ON system_events (ts DESC);

-- État système persistant (kill switch, circuit breaker, pic d'equity)
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Validations persistées : IS/OOS + walk-forward (anti sur-optimisation)
CREATE TABLE IF NOT EXISTS validation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL,                    -- 'is_oos' | 'walk_forward'
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    config JSONB NOT NULL,
    results JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_runs_created
    ON validation_runs (created_at DESC);
