-- Q-ALPHA | strategy_lab_state — anon READ-ONLY policy
--
-- Run in the Supabase SQL editor BEFORE pointing the public Streamlit
-- dashboard at SUPABASE_ANON_KEY for the Strategy Lab tab.
--
-- RLS is already enabled on strategy_lab_state. This policy grants
-- SELECT to the anon role ONLY. No insert/update/delete for anon.
-- Does NOT open any other table.
--
-- Service role (SUPABASE_SECRET_KEY) bypasses RLS and keeps WRITE access
-- for live_forward.py / lab_state_sync.py.

-- Ensure anon can SELECT (RLS still filters via policy).
GRANT SELECT ON TABLE public.strategy_lab_state TO anon;

-- Drop+recreate so re-runs are idempotent.
DROP POLICY IF EXISTS strategy_lab_state_anon_select ON public.strategy_lab_state;

CREATE POLICY strategy_lab_state_anon_select
    ON public.strategy_lab_state
    FOR SELECT
    TO anon
    USING (true);

-- Explicitly: no write policies for anon on this table.
-- (Absence of INSERT/UPDATE/DELETE policies = anon cannot mutate.)
