-- Q-ALPHA | ticker_profiles (run once in the Supabase SQL editor)
--
-- Cloud dashboard Ticker Profiles tab reads these rows (anon SELECT).
-- Morning agent + on-demand Refresh upsert with SUPABASE_SECRET_KEY
-- (service role bypasses RLS). Do NOT commit profiles/*.json for Cloud.
--
-- Safe to re-run.

CREATE TABLE IF NOT EXISTS ticker_profiles (
    ticker      TEXT PRIMARY KEY,
    as_of_date  DATE,
    profile     JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ticker_profiles_updated_at_idx
    ON ticker_profiles (updated_at DESC);

CREATE INDEX IF NOT EXISTS ticker_profiles_as_of_date_idx
    ON ticker_profiles (as_of_date DESC);

COMMENT ON TABLE ticker_profiles IS
    'Precomputed MAE/MFE setup profiles for Streamlit Ticker Profiles (Cloud).';
COMMENT ON COLUMN ticker_profiles.profile IS
    'Full profiles/{TICKER}_profile.json blob from ticker_profiler.save_profile_json';

-- Service role bypasses RLS. Enable RLS so anon cannot mutate.
ALTER TABLE ticker_profiles ENABLE ROW LEVEL SECURITY;

-- Anon READ-ONLY (same pattern as strategy_lab_state).
GRANT SELECT ON TABLE public.ticker_profiles TO anon;

DROP POLICY IF EXISTS ticker_profiles_anon_select ON public.ticker_profiles;

CREATE POLICY ticker_profiles_anon_select
    ON public.ticker_profiles
    FOR SELECT
    TO anon
    USING (true);

-- Explicitly: no INSERT/UPDATE/DELETE policies for anon.

NOTIFY pgrst, 'reload schema';

-- Verification
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'ticker_profiles'
ORDER BY ordinal_position;
