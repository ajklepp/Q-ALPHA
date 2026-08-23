-- Q-ALPHA | strategy_lab_state (run once in the Supabase SQL editor)
--
-- Stores live_forward.py forward-test snapshots for the Streamlit
-- "Strategy Lab" tab on Cloud (no git commits of forward_state.json).
-- Keyed by flag_date; full state lives in jsonb.
--
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS strategy_lab_state (
    flag_date   DATE PRIMARY KEY,
    state       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS strategy_lab_state_updated_at_idx
    ON strategy_lab_state (updated_at DESC);

COMMENT ON TABLE strategy_lab_state IS
    'Strategy Lab dual-pool forward paper state (SIM / Polygon-paper).';
COMMENT ON COLUMN strategy_lab_state.state IS
    'Full forward_state.json blob from live_forward.save_state()';

-- Service role (SUPABASE_SECRET_KEY) bypasses RLS. Enable RLS so anon keys
-- cannot read/write unless you add explicit policies later.
ALTER TABLE strategy_lab_state ENABLE ROW LEVEL SECURITY;
