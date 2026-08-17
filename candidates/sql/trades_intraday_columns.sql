-- Add intraday live P&L columns to trades table (run once in Supabase SQL editor)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS current_price FLOAT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS r_multiple FLOAT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS dist_to_stop FLOAT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS dist_to_target FLOAT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS last_updated TEXT;
