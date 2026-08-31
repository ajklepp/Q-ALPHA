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

CREATE INDEX IF NOT EXISTS tsd_watchlist_rank_idx ON tsd_watchlist (rank);

-- ---------------------------------------------------------------------------
-- RLS: anon SELECT for Streamlit Cloud (service role writes from local sync)
-- ---------------------------------------------------------------------------
ALTER TABLE tsd_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_pool_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE tsd_watchlist ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON TABLE public.tsd_positions TO anon;
GRANT SELECT ON TABLE public.tsd_pool_snapshots TO anon;
GRANT SELECT ON TABLE public.tsd_watchlist TO anon;

DROP POLICY IF EXISTS tsd_positions_anon_select ON public.tsd_positions;
CREATE POLICY tsd_positions_anon_select ON public.tsd_positions
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_pool_snapshots_anon_select ON public.tsd_pool_snapshots;
CREATE POLICY tsd_pool_snapshots_anon_select ON public.tsd_pool_snapshots
    FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS tsd_watchlist_anon_select ON public.tsd_watchlist;
CREATE POLICY tsd_watchlist_anon_select ON public.tsd_watchlist
    FOR SELECT TO anon USING (true);

NOTIFY pgrst, 'reload schema';
