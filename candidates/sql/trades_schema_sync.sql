-- Q-ALPHA | trades table schema sync (run once in the Supabase SQL editor)
--
-- Fixes: PGRST204 "Could not find the 'current_price' column of 'trades'
--        in the schema cache" raised by supabase_sync.upsert_trade().
--
-- Cause: the live trades table was created before the intraday P&L columns
--        were added to SCHEMA_SQL. CREATE TABLE IF NOT EXISTS in
--        setup_supabase.py is a no-op against an existing table, so re-running
--        that script can never add them.
--
-- Every column in supabase_sync.TRADE_FIELDS is listed below so the whole set
-- is reconciled in one pass rather than one column per outage. Columns that
-- already exist are skipped and keep their current type.

ALTER TABLE trades ADD COLUMN IF NOT EXISTS ticker         TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_date     TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price    NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_price     NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS target_1r      NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS target_2r      NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS shares_total   INTEGER;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS status         TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl_dollars    NUMERIC DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl_pct        NUMERIC DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason    TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS days_held      INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS execution_mode TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS ibkr_order_id  BIGINT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_price  NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS r_multiple     NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS dist_to_stop   NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS dist_to_target NUMERIC;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS last_updated   TIMESTAMPTZ;

-- upsert_trade() uses on_conflict="ticker,entry_date"; PostgREST needs a real
-- unique constraint on that pair or the upsert fails with a 42P10.
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_ticker_entry_date_key;
ALTER TABLE trades ADD CONSTRAINT trades_ticker_entry_date_key
    UNIQUE (ticker, entry_date);

-- PGRST204 is a PostgREST schema-cache miss, so the cache must be refreshed
-- after the DDL above or the API keeps rejecting the new columns.
NOTIFY pgrst, 'reload schema';

-- Verification: expect all 19 TRADE_FIELDS columns plus id and created_at.
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'trades'
ORDER BY ordinal_position;
