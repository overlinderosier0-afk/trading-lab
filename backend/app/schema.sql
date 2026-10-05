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

-- Signal Lab : signaux BUY/SELL générés manuellement, suivis jusqu'à résolution.
-- Le score (0-100) mesure la force du modèle, PAS une probabilité de gain.
-- Seuls les signaux résolus (win/loss) alimentent la calibration honnête.
CREATE TABLE IF NOT EXISTS signal_lab_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,              -- 'BUY' | 'SELL' (jamais NEUTRAL)
    score INTEGER NOT NULL,               -- 0..100, force du modèle
    total DOUBLE PRECISION NOT NULL,      -- somme signée des facteurs
    factors JSONB NOT NULL,               -- {trend:{label,weight,points}, ...}
    justification JSONB NOT NULL,         -- lignes d'explication (FR)
    indicators JSONB NOT NULL,            -- valeurs brutes EMA/RSI/MACD/ATR
    entry_price DOUBLE PRECISION NOT NULL,
    stop_loss DOUBLE PRECISION NOT NULL,
    take_profit DOUBLE PRECISION NOT NULL,
    atr DOUBLE PRECISION NOT NULL,
    horizon_minutes INTEGER NOT NULL,     -- 3 bougies après génération
    resolve_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'win' | 'loss' | 'expired'
    exit_price DOUBLE PRECISION,
    sl_hit BOOLEAN,
    tp_hit BOOLEAN,
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_slab_resolve
    ON signal_lab_signals (outcome, resolve_at);
CREATE INDEX IF NOT EXISTS idx_slab_sym_tf
    ON signal_lab_signals (symbol, timeframe, created_at DESC);

-- Signal Lab : auto-génération (04/10). Colonnes ajoutées de façon idempotente.
ALTER TABLE signal_lab_signals
    ADD COLUMN IF NOT EXISTS candle_ts TIMESTAMPTZ;
ALTER TABLE signal_lab_signals
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'manual';

-- Signal Lab : évaluation observationnelle (paper-trading de mesure).
-- entry_price existe déjà (DOUBLE PRECISION NOT NULL) : c'est le close de la
-- bougie déclencheuse, figé à l'INSERT. On ajoute seulement son horodatage
-- et sa source. NEUTRAL n'est jamais stocké : pas d'évaluation pour NEUTRAL.
ALTER TABLE signal_lab_signals
    ADD COLUMN IF NOT EXISTS entry_timestamp TIMESTAMPTZ;
ALTER TABLE signal_lab_signals
    ADD COLUMN IF NOT EXISTS entry_price_source TEXT;

-- Backfill idempotent : entry = close de la bougie déclencheuse, donc
-- entry_timestamp = close de candle_ts (= candle_ts + durée du timeframe).
UPDATE signal_lab_signals
SET entry_timestamp = candle_ts + (
    CASE timeframe
        WHEN '1m' THEN INTERVAL '1 minute'
        WHEN '3m' THEN INTERVAL '3 minutes'
        WHEN '5m' THEN INTERVAL '5 minutes'
        WHEN '15m' THEN INTERVAL '15 minutes'
        WHEN '30m' THEN INTERVAL '30 minutes'
        WHEN '1h' THEN INTERVAL '1 hour'
        WHEN '2h' THEN INTERVAL '2 hours'
        WHEN '4h' THEN INTERVAL '4 hours'
        WHEN '6h' THEN INTERVAL '6 hours'
        WHEN '8h' THEN INTERVAL '8 hours'
        WHEN '12h' THEN INTERVAL '12 hours'
        WHEN '1d' THEN INTERVAL '1 day'
        WHEN '3d' THEN INTERVAL '3 days'
        WHEN '1w' THEN INTERVAL '1 week'
        ELSE INTERVAL '5 minutes'
    END)
WHERE entry_timestamp IS NULL AND candle_ts IS NOT NULL;

UPDATE signal_lab_signals
SET entry_price_source = 'candle_close'
WHERE entry_price_source IS NULL AND entry_timestamp IS NOT NULL;

-- Index pour les requêtes de listing / stats de l'évaluation.
CREATE INDEX IF NOT EXISTS idx_slab_eval_sym_created
    ON signal_lab_signals (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_slab_eval_origin_created
    ON signal_lab_signals (origin, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_slab_eval_score_created
    ON signal_lab_signals (score, created_at DESC);

-- Immuabilité des colonnes historiques : le resolveur existant (UPDATE de
-- outcome, exit_price, sl_hit, tp_hit, resolved_at) reste autorisé ; tout
-- UPDATE des colonnes ci-dessous est refusé par la base.
-- entry_timestamp / entry_price_source : NULL -> valeur autorisé UNE fois
-- (backfill idempotent de schema.sql) ; ensuite immuable.
CREATE OR REPLACE FUNCTION trg_slab_signals_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.entry_price IS DISTINCT FROM NEW.entry_price
        OR OLD.created_at IS DISTINCT FROM NEW.created_at
        OR OLD.symbol IS DISTINCT FROM NEW.symbol
        OR OLD.timeframe IS DISTINCT FROM NEW.timeframe
        OR OLD.direction IS DISTINCT FROM NEW.direction
        OR OLD.score IS DISTINCT FROM NEW.score
        OR OLD.origin IS DISTINCT FROM NEW.origin
        OR (OLD.entry_timestamp IS NOT NULL
            AND OLD.entry_timestamp IS DISTINCT FROM NEW.entry_timestamp)
        OR (OLD.entry_price_source IS NOT NULL
            AND OLD.entry_price_source IS DISTINCT FROM NEW.entry_price_source)
    THEN
        RAISE EXCEPTION
            'signal_lab_signals: historical columns are immutable (id=%)', OLD.id;
    END IF;
    RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_slab_signals_immutable ON signal_lab_signals;
CREATE TRIGGER trg_slab_signals_immutable
    BEFORE UPDATE ON signal_lab_signals
    FOR EACH ROW EXECUTE FUNCTION trg_slab_signals_immutable();

-- Évaluations observationnelles : une ligne par (signal, horizon).
-- Les bougies 1m servant au calcul sont récupérées à la demande via
-- fetch_klines (jamais stockées) : voir §2 du prompt d'ingénierie.
CREATE TABLE IF NOT EXISTS signal_lab_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signal_lab_signals(id) ON DELETE CASCADE,
    horizon_minutes INTEGER NOT NULL CHECK (horizon_minutes > 0),
    entry_price DOUBLE PRECISION NOT NULL,   -- copié du signal, jamais recalculé
    exit_price DOUBLE PRECISION,
    return_pct DOUBLE PRECISION,             -- % brut (coûts appliqués à la lecture)
    direction_correct BOOLEAN,               -- NULL = neutre (return = 0)
    mfe_pct DOUBLE PRECISION,
    mae_pct DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'COMPLETED', 'ERROR', 'UNAVAILABLE')),
    evaluated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    due_at TIMESTAMPTZ NOT NULL,
    exit_timestamp TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_error TEXT,
    calc_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (signal_id, horizon_minutes)
);
CREATE INDEX IF NOT EXISTS idx_slab_eval_signal
    ON signal_lab_evaluations (signal_id);
CREATE INDEX IF NOT EXISTS idx_slab_eval_status
    ON signal_lab_evaluations (status);
CREATE INDEX IF NOT EXISTS idx_slab_eval_horizon
    ON signal_lab_evaluations (horizon_minutes);
CREATE INDEX IF NOT EXISTS idx_slab_eval_evaluated
    ON signal_lab_evaluations (evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_slab_eval_due
    ON signal_lab_evaluations (status, due_at)
    WHERE status = 'PENDING';

-- Une évaluation COMPLETED est immuable : aucun recalcul silencieux.
CREATE OR REPLACE FUNCTION trg_slab_eval_completed_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'COMPLETED' THEN
        RAISE EXCEPTION
            'signal_lab_evaluations: COMPLETED evaluations are immutable (id=%)', OLD.id;
    END IF;
    RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS trg_slab_eval_completed_immutable ON signal_lab_evaluations;
CREATE TRIGGER trg_slab_eval_completed_immutable
    BEFORE UPDATE ON signal_lab_evaluations
    FOR EACH ROW EXECUTE FUNCTION trg_slab_eval_completed_immutable();
