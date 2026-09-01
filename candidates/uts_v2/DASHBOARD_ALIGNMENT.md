# UTS v2 Dashboard Alignment

**Date:** 2026-09-01  
**Status:** IMPLEMENTED (Phases A–E)

## SQL migrations (`candidates/sql/tsd_cloud.sql`)

Run once in Supabase SQL editor after deploy.

### `tsd_positions` — new columns

| Column | Type | Purpose |
|--------|------|---------|
| `structure_stop_reason` | TEXT | orb_structure, breakeven_ratchet, day2_tighten |
| `breakeven_locked` | BOOLEAN | Structure ratchet state |
| `tranche_json` | JSONB | T1–T4 id, shares, trigger, armed, trail_stop |
| `t1_trigger_price` | NUMERIC | First tranche trigger |
| `next_trail_stop` | NUMERIC | Nearest armed trail stop |
| `launch_score` | NUMERIC | Entry launch score (when stored on leg) |
| `phase` | TEXT | LAUNCH / EXTENSION |
| `pre_catalyst` | BOOLEAN | No news at entry |
| `mfe_r` | NUMERIC | Peak MFE in R units |

### `tsd_watch_queue` — new table

Entry pipeline: symbol, status, launch_score, phase, cross_level, gates, tags, analog_count, pre_catalyst, news_summary, etc.

### `tsd_watchlist` — new columns

`launch_score`, `phase`, `wt_gap`, `early_bull`, `analog_count`, `analog_win_rate`, `pre_catalyst`, `tags`

### `tsd_closed_legs` — new columns

`launch_score`, `phase`, `exit_layer` (Kill / Structure / Trail)

### `tsd_pool_snapshots` — new columns

`spy_regime`, `vix_regime`, `sizing_pct`

## Sync (`tsd_supabase_sync.py`)

- `flatten_open_legs`: tranche_json, structure fields, mfe_r
- `flatten_closed_legs`: exit_layer, launch_score, phase
- `sync_tsd_watch_queue_from_file`: local queue → Supabase
- Watchlist sync: LAUNCH fields from scan rows
- Pool snapshot: SPY regime from `fetch_regime_bull()`

## Dashboard (`dashboard_live_status.py`)

| Section | Change |
|---------|--------|
| **Entry Pipeline** | Watch queue table above open positions |
| **Open Positions** | 3-layer card: kill / structure / trail + T1–T4 table |
| **Watchlist** | Launch primary; Ext = scan_score caption |
| **Trade Log** | Layer, Launch, Phase columns |
| **Performance** | P&L by exit layer chart |
| **Regime** | From pool snapshot or queue regime |

## Reply for Chat A — fields synced

**Open leg row (Supabase `tsd_positions`):**
```
symbol, entry_price, kill_price, structure_stop, structure_stop_reason,
breakeven_locked, next_trail_stop, t1_trigger_price, tranche_json[],
launch_score, phase, pre_catalyst, mfe_r, rth_armed, peak_high
```

**Watch queue row (`tsd_watch_queue`):**
```
symbol, status, launch_score, phase, cross_level, gates, tags,
pre_catalyst, analog_count, analog_win_rate, added_at
```

**Watchlist row (`tsd_watchlist`):**
```
symbol, rank, launch_score, phase, scan_score (Ext), wt_gap,
early_bull, analog_count, analog_win_rate, pre_catalyst
```

**Closed leg (`tsd_closed_legs`):**
```
symbol, exit_reason, exit_layer, launch_score, phase, pnl_dollars
```

**Action:** Run `tsd_cloud.sql` in Supabase, then `py -3 candidates/tsd_supabase_sync.py` to backfill.
