-- Run once in Supabase SQL editor (Peak Hour Weekly Review missed highlights)
-- Source of truth also in candidates/sql/tsd_cloud.sql

CREATE TABLE IF NOT EXISTS tsd_missed_moves (
    symbol          TEXT NOT NULL,
    signal_day      TEXT NOT NULL,
    signal_at       TIMESTAMPTZ,
    ref_price       NUMERIC,
    peak_price      NUMERIC,
    ran_up_pct      NUMERIC,
    outcome         TEXT DEFAULT 'MISSED',
    marked_at       TIMESTAMPTZ,
    thesis          JSONB,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, signal_day)
);

ALTER TABLE tsd_missed_moves ADD COLUMN IF NOT EXISTS thesis JSONB;

CREATE INDEX IF NOT EXISTS tsd_missed_moves_day_idx ON tsd_missed_moves (signal_day DESC);
CREATE INDEX IF NOT EXISTS tsd_missed_moves_ran_idx ON tsd_missed_moves (ran_up_pct DESC NULLS LAST);

ALTER TABLE tsd_missed_moves ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON TABLE public.tsd_missed_moves TO anon;
DROP POLICY IF EXISTS tsd_missed_moves_anon_select ON public.tsd_missed_moves;
CREATE POLICY tsd_missed_moves_anon_select ON public.tsd_missed_moves
    FOR SELECT TO anon USING (true);

NOTIFY pgrst, 'reload schema';
