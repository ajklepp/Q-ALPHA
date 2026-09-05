-- Trade thesis JSON on open/closed/missed (Weekly Review + Live Status expanders)
-- Run once in Supabase SQL editor. Also folded into tsd_cloud.sql.

ALTER TABLE tsd_positions ADD COLUMN IF NOT EXISTS thesis JSONB;
ALTER TABLE tsd_closed_legs ADD COLUMN IF NOT EXISTS thesis JSONB;
ALTER TABLE tsd_missed_moves ADD COLUMN IF NOT EXISTS thesis JSONB;

NOTIFY pgrst, 'reload schema';
