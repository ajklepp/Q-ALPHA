# Peak Hour Performers — Live Paper design

**Status:** Live Paper primary = **1H LAUNCH @ :15** (hours **05–15** ET).  
**Product:** Peak Hour Performers v3.1 · continuation_score_v1 · 2 slots/scan · LONG-ONLY.  
**Workspace:** Q-ALPHA only.

### Live Paper stack (KEEP)

| Job | Role |
|-----|------|
| `scheduler.py --tick --live` | Sole entry authority → `tsd_1h_launch_scan` |
| HTF universe @ 04:30 | Daily pass set for hourly scan |
| `tsd_watch_queue` + `execute_live_entries` | Admit + BUY |
| `tsd_social` (Polygon + TWS news + StockTwits; X off) | Soft continuation terms + thesis; never hard-vetoes |
| `tsd_deep_features` (20d room/bounce + 1H path prior) | Path prior when n≥3; else profile analog fallback |
| `catalyst_ai` via OpenRouter (`gpt-4o-mini` default) | Headline → print/outlook; ~pennies/day; free optional via env |
| `tsd_trail_monitor` | Kill until +1R → BE → trail |
| `tws_intraday_sync` (clientId 96) | Marks / closed / pool / Peak Hour launch board |
| Telegram + on-fill Supabase | Immediate Aaron + dashboard awareness |

### Not Live Paper (research / disabled)

- `setup_watch_agent` / **QAlpha TSD Setup Watch** — DISABLED (second entry bot)
- Gap **Autonomous Agent** / **Approval Runner** — DISABLED
- `polygon_hunt_list`, `tsd_scan_ibkr` dry 3H, profiler — research/context only
- `--polygon` / `--tws` without redirect — research; `--tws --live` → 1H LAUNCH

Dashboard **Peak Hour launches** board SoT = `last_1h_launch.json` → `tsd_watchlist`  
(legacy 3H `last_watchlist.json` must not overwrite Supabase).

---

## Legacy 3HR research notes (not live trigger)

```
3H bar closes (:00 ET) → TWS context (:03) → Polygon hunt (:20)   # RESEARCH
```

Orchestrated historically by `scheduler.py --tick`; live tick now fires **1H @ :15** only.

## IBKR bar alignment (probed 2026-08-30)

IBKR 3H timestamps: **01, 04, 05, 08, 11, 14, 17, 19, 22 ET** (extended hours).  
Polygon profiler fallback: **30-min aggs bucketed to IBKR-style keys**.  
Pacing: **~2.5s/symbol** for historical pulls.

## Profiler v2 (watch-10 gate)

- Runs on **watch top 10 only** — `--enforce-profiler` or `--live`
- MIN **30 analogs** required — no trade fallback
- Profiles saved to `profiles/{SYMBOL}_tsd_profile.json`

## Phase 4 — Software trail monitor

`tsd_trail_monitor.py` (clientId **95**): strategy_a 4-tranche trail, session-aware SELL.

## Phase 5 — Scheduler + scorecard

| Module | Role |
|--------|------|
| `scheduler.py` | ET slot dispatcher (:20 Polygon, :03 TWS, trail fallback) |
| `tsd_scorecard.py` | Weekly 5-trading-day rollup |
| `register_tsd_tasks.ps1` | Scheduler (5m) + Trail (04:00) + Weekly Reports — **no Setup Watch** |
| `results/results.md` | Pipeline results log |

**Register (Aaron, once):**
```powershell
.\candidates\register_tsd_tasks.ps1
```

## TWS client IDs

| Process | clientId |
|---------|----------|
| Morning agent | 5 |
| TSD scan | **93** |
| IBKR probe | 94 |
| TSD trail monitor | **95** |
| TWS sync | 96 |
| Spike scanner | 97 |

## State files

| File | Purpose |
|------|---------|
| `tsd_book_state.json` | Positions, trail state, kill_order_id |
| `tsd_pool_state.json` | Deployable pool ($3000 default) |
| `results/tsd_scheduler_state.json` | Last-run slot keys |

## Phase 6 — Weekly reports (Friday 5 PM ET)

| Module | Role |
|--------|------|
| `tsd_scorecard.py` | 5-day scan/trail/book rollup → `results/scorecard_*.md` |
| `tsd_options_study.py` | Tier outcome study + options overlay counterfactual → `results/options_study_*.md` |
| `start_tsd_weekly_reports_scheduled.ps1` | Runs both with `--write` |
| `register_tsd_tasks.ps1` | Task **QAlpha TSD Weekly Reports** · Fri 17:00 |

**Manual:**
```powershell
.\candidates\start_tsd_weekly_reports_scheduled.ps1
py -3 candidates\tsd_scan_pipeline\tsd_options_study.py --days 5 --write
```

Study cohorts: top-100 by score, signals, watch-10, trade-3, filled. Options overlay
uses Polygon same-day call/put volume (best-effort; does not affect live scoring).

---

## Not mixed with

- Strategy Lab SIM book
- Morning gap agent
- EXP-0012 / BracketPosition experiments

