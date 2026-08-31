-- Q-ALPHA | tsd_positions table (run once in the Supabase SQL editor)
--
-- Cloud mirror of local tsd_book_state.json open legs for the Streamlit
-- dashboard TSD panel. Populated by candidates/tsd_supabase_sync.py via
-- local tws_intraday_sync.py (TWS marks, clientId 96).
--
-- Safe to re-run: CREATE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.

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
    last_updated    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS symbol        TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS entry_date     TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS leg_opened_at TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS entry_price    NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS shares         INTEGER;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS kill_price     NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS current_price  NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS pnl_dollars    NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS pnl_pct        NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS status         TEXT DEFAULT 'OPEN';
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS last_bar_time  TEXT;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS scan_score     NUMERIC;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS last_updated TIMESTAMPTZ;
ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS created_at   TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE tsd_positions DROP CONSTRAINT IF EXISTS tsd_positions_symbol_leg_opened_at_key;
ALTER TABLE tsd_positions ADD CONSTRAINT tsd_positions_symbol_leg_opened_at_key
    UNIQUE (symbol, leg_opened_at);

CREATE INDEX IF NOT EXISTS tsd_positions_status_idx ON tsd_positions (status);
CREATE INDEX IF NOT EXISTS tsd_positions_symbol_idx ON tsd_positions (symbol);

NOTIFY pgrst, 'reload schema';

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'tsd_positions'
ORDER BY ordinal_position;
