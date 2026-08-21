-- Q-ALPHA | watchlist table (run once in the Supabase SQL editor)
--
-- Lets the dashboard show today's agent watchlist even when zero trades
-- are placed. autonomous_agent calls supabase_sync.upsert_watchlist() after
-- the Telegram summary; the Streamlit dashboard reads these rows by scan_date.
--
-- Safe to re-run: CREATE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS watchlist (
    id            BIGSERIAL PRIMARY KEY,
    scan_date     TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    rank          INTEGER,
    gap_pct       NUMERIC,
    pm_vol_ratio  NUMERIC,
    score         NUMERIC,
    regime        TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS scan_date    TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS ticker       TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS rank         INTEGER;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS gap_pct      NUMERIC;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS pm_vol_ratio NUMERIC;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS score        NUMERIC;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS regime       TEXT;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ DEFAULT NOW();

-- One row per ticker per day (upsert_watchlist deletes+inserts by scan_date,
-- but the unique pair still protects against concurrent writers).
ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_scan_date_ticker_key;
ALTER TABLE watchlist ADD CONSTRAINT watchlist_scan_date_ticker_key
    UNIQUE (scan_date, ticker);

CREATE INDEX IF NOT EXISTS watchlist_scan_date_idx ON watchlist (scan_date);

-- Refresh PostgREST schema cache so the API sees the new table immediately.
NOTIFY pgrst, 'reload schema';

-- Verification: expect the columns below.
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'watchlist'
ORDER BY ordinal_position;
