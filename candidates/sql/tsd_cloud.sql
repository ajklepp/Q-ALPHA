-- Q-ALPHA | TSD cloud tables for Streamlit Live Status (run once in Supabase SQL editor)
--
-- Populated by candidates/tsd_supabase_sync.py (via tws_intraday_sync + tsd_scan_ibkr).
-- Safe to re-run: CREATE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.

-- ---------------------------------------------------------------------------
-- tsd_positions — open legs mirror of tsd_book_state.json
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_positions (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    entry_date      TEXT NOT NULL,
    leg_opened_at   TEXT NOT NULL,
    entry_price     NUMERIC,
    shares          INTEGER,
    kill_price      NUMERIC,
    current_price   NUMERIC,
    pnl_dollars     NUMERIC,
    pnl_pct         NUMERIC,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    last_bar_time   TEXT,
    scan_score      NUMERIC,
    peak_high       NUMERIC,
    kill_pct        NUMERIC,
    trail_pct       NUMERIC,
    trading_day     INTEGER,
    t4_only         BOOLEAN DEFAULT FALSE,
    tranche_summary TEXT,
    last_updated    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS peak_high       NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS kill_pct        NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS trail_pct       NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS trading_day     INTEGER;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS t4_only         BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS tranche_summary TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS structure_stop NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS rth_armed         BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS structure_stop_reason TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS breakeven_locked    BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS tranche_json        JSONB;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS t1_trigger_price   NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS next_trail_stop     NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS launch_score        NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS phase               TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS pre_catalyst        BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS mfe_r               NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS kill_source         TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS bar_state           TEXT;

ALTER TABLE tsd_positions DROP CONSTRAINT IF EXISTS tsd_positions_symbol_leg_opened_at_key;
ALTER TABLE tsd_positions ADD CONSTRAINT tsd_positions_symbol_leg_opened_at_key
    UNIQUE (symbol, leg_opened_at);

CREATE INDEX IF NOT EXISTS tsd_positions_status_idx ON tsd_positions (status);
CREATE INDEX IF NOT EXISTS tsd_positions_symbol_idx ON tsd_positions (symbol);

-- ---------------------------------------------------------------------------
-- tsd_pool_snapshots — TSD cash / deployed (separate from gap pool_snapshots)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_pool_snapshots (
    snapshot_date   TEXT PRIMARY KEY,
    pool            NUMERIC NOT NULL,
    deployed        NUMERIC NOT NULL,
    open_positions  INTEGER NOT NULL DEFAULT 0,
    open_names      INTEGER NOT NULL DEFAULT 0,
    starting_pool   NUMERIC NOT NULL DEFAULT 3000,
    last_updated    TIMESTAMPTZ
);

ALTER TABLE tsd_pool_snapshots ADD COLUMN IF NOT EXISTS open_names INTEGER DEFAULT 0;
ALTER TABLE tsd_pool_snapshots ADD COLUMN IF NOT EXISTS spy_regime  TEXT;
ALTER TABLE tsd_pool_snapshots ADD COLUMN IF NOT EXISTS vix_regime  TEXT DEFAULT 'NORMAL';
ALTER TABLE tsd_pool_snapshots ADD COLUMN IF NOT EXISTS sizing_pct  TEXT DEFAULT '100%';

-- ---------------------------------------------------------------------------
-- tsd_watchlist — current TSD watch-10 (replace-all each scan)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_watchlist (
    symbol          TEXT PRIMARY KEY,
    rank            INTEGER,
    scan_score      NUMERIC,
    trend_strength  NUMERIC,
    mfi             NUMERIC,
    buy_signal      BOOLEAN,
    profiler_pass   BOOLEAN,
    in_book         BOOLEAN DEFAULT FALSE,
    trade_pick      BOOLEAN DEFAULT FALSE,
    status_label    TEXT,
    entry_price     NUMERIC,
    kill_price      NUMERIC,
    scan_at         TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS launch_score      NUMERIC;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS phase             TEXT;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS wt_gap            NUMERIC;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS early_bull        BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS analog_count      INTEGER;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS analog_win_rate   NUMERIC;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS pre_catalyst      BOOLEAN DEFAULT FALSE;
ALTER TABLE tsd_watchlist ADD COLUMN IF NOT EXISTS tags              JSONB;

CREATE INDEX IF NOT EXISTS tsd_watchlist_rank_idx ON tsd_watchlist (rank);

-- ---------------------------------------------------------------------------
-- tsd_watch_queue — UTS v2 entry pipeline (WATCHING / CONFIRMED / SKIPPED)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_watch_queue (
    symbol              TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'WATCHING',
    signal_lane         TEXT,
    launch_score        NUMERIC,
    launch_score_display NUMERIC,
    phase               TEXT,
    scan_score          NUMERIC,
    wt_gap              NUMERIC,
    cross_level         NUMERIC,
    early_bull          BOOLEAN DEFAULT FALSE,
    buy_signal          BOOLEAN DEFAULT FALSE,
    pre_catalyst        BOOLEAN DEFAULT FALSE,
    analog_count        INTEGER,
    analog_win_rate     NUMERIC,
    gates               JSONB,
    quality_gates       JSONB,
    tags                JSONB,
    size_mult           NUMERIC DEFAULT 1.0,
    news_summary        TEXT,
    catalyst_tier       INTEGER DEFAULT 0,
    sentiment_score     NUMERIC,
    regime              TEXT,
    skip_reason         TEXT,
    added_at            TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tsd_watch_queue_status_idx ON tsd_watch_queue (status);

-- ---------------------------------------------------------------------------
-- tsd_closed_legs — completed TSD legs (one row per leg_opened_at)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_closed_legs (
    symbol          TEXT NOT NULL,
    leg_opened_at   TEXT NOT NULL,
    entry_date      TEXT,
    entry_price     NUMERIC,
    shares          INTEGER,
    exit_price      NUMERIC,
    exit_reason     TEXT,
    pnl_dollars     NUMERIC,
    pnl_pct         NUMERIC,
    closed_at       TIMESTAMPTZ,
    scan_score      NUMERIC,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, leg_opened_at)
);

ALTER TABLE tsd_closed_legs ADD COLUMN IF NOT EXISTS launch_score   NUMERIC;
ALTER TABLE tsd_closed_legs ADD COLUMN IF NOT EXISTS phase          TEXT;
ALTER TABLE tsd_closed_legs ADD COLUMN IF NOT EXISTS exit_layer     TEXT;

CREATE INDEX IF NOT EXISTS tsd_closed_legs_closed_at_idx ON tsd_closed_legs (closed_at DESC);

-- ---------------------------------------------------------------------------
-- tsd_missed_moves — launches seen but not taken (Weekly Review highlights)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tsd_missed_moves (
    symbol          TEXT NOT NULL,
    signal_day      TEXT NOT NULL,
    signal_at       TIMESTAMPTZ,
    ref_price       NUMERIC,
    peak_price      NUMERIC,
    ran_up_pct      NUMERIC,
    outcome         TEXT DEFAULT 'MISSED',
    marked_at       TIMESTAMPTZ,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, signal_day)
);

CREATE INDEX IF NOT EXISTS tsd_missed_moves_day_idx ON tsd_missed_moves (signal_day DESC);
CREATE INDEX IF NOT EXISTS tsd_missed_moves_ran_idx ON tsd_missed_moves (ran_up_pct DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- RLS: anon SELECT for Streamlit Cloud (service role writes from local sync)
-- ---------------------------------------------------------------------------
ALTER TABLE tsd_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_pool_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_closed_legs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_watch_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_missed_moves ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON TABLE public.tsd_positions TO anon;
GRANT SELECT ON TABLE public.tsd_pool_snapshots TO anon;
GRANT SELECT ON TABLE public.tsd_watchlist TO anon;
GRANT SELECT ON TABLE public.tsd_closed_legs TO anon;
GRANT SELECT ON TABLE public.tsd_watch_queue TO anon;
GRANT SELECT ON TABLE public.tsd_missed_moves TO anon;

DROP POLICY IF EXISTS tsd_positions_anon_select ON public.tsd_positions;
CREATE POLICY tsd_positions_anon_select ON public.tsd_positions
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_pool_snapshots_anon_select ON public.tsd_pool_snapshots;
CREATE POLICY tsd_pool_snapshots_anon_select ON public.tsd_pool_snapshots
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_watchlist_anon_select ON public.tsd_watchlist;
CREATE POLICY tsd_watchlist_anon_select ON public.tsd_watchlist
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_closed_legs_anon_select ON public.tsd_closed_legs;
CREATE POLICY tsd_closed_legs_anon_select ON public.tsd_closed_legs
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_watch_queue_anon_select ON public.tsd_watch_queue;
CREATE POLICY tsd_watch_queue_anon_select ON public.tsd_watch_queue
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_missed_moves_anon_select ON public.tsd_missed_moves;
CREATE POLICY tsd_missed_moves_anon_select ON public.tsd_missed_moves
    FOR SELECT TO anon USING (true);

NOTIFY pgrst, 'reload schema';
