# Peak Hour Performers — Live Paper design

**Status:** Live Paper primary = **1H LAUNCH @ :15** (hours **05–15** ET).  
**Product:** Peak Hour Performers v3.2 · continuation_score nominates · **case review decides** · 2 slots/scan · LONG-ONLY.  
**Workspace:** Q-ALPHA only.

### Live Paper stack (KEEP)

| Job | Role |
|-----|------|
| `scheduler.py --tick --live` | Sole entry authority → `tsd_1h_launch_scan` |
| HTF universe @ 04:30 | Daily pass set for hourly scan |
| `tsd_popularity` | Multi-day Polygon movers + $vol leaders + live gainers + TWS MOST_ACTIVE/TOP_PERC_GAIN |
| `tsd_attention` | Attention Pool: top continuation ∪ tradable_popular ∪ soft-extension (ST optional) |
| `tsd_case_review` | ENTER / WAIT / REJECT — score alone cannot buy |
| `tsd_watch_queue` + `execute_live_entries` | Admit + BUY **case-ENTER only** |
| `tsd_social` (Polygon + TWS news + StockTwits; X off) | Rank soft terms + case dossier evidence |
| `tsd_deep_features` (20d room/bounce + 1H path prior) | Path prior when n≥3; else profile analog fallback |
| Missed ledger `evidence` + thesis | Frozen decision snapshot for Weekly Review |
| `catalyst_ai` via OpenRouter (`gpt-4o-mini` default) | Thin PRINT/OUTLOOK (attention set) |
| `tsd_catalyst_deep` (90d lookback) | Narrative + risk flags; contradictions can REJECT |
| OpenRouter web search | Live chatter / leaderboard context inside case fusion |
| `tsd_trail_monitor` | Keep-profit v1: T1 bank @+2% → kill tighten 2.5% → T2–T4 trail |
| `tws_intraday_sync` (clientId 96) | Marks / closed / pool / Peak Hour launch board |
| Telegram + on-fill Supabase | Immediate Aaron + dashboard awareness |

### Authority (v3.2)

1. **1H deep-swing / early scan scores stay preferential** (do not equalize all bars).  
2. **Continuation score nominates** into Attention Pool.  
3. **Momentum / popularity** — ask “is this a popular stock to trade?” via recent multi-day leaderboard history (not first 1H print alone), TWS scanners, live gainers; StockTwits is optional only.  
4. **Case review decides** ENTER / WAIT / REJECT. Wreckage room, toxic flags, and thin↔deep contradictions hard-REJECT.  
5. **BUY only case-ENTER**, still capped at 2/scan + capacity.

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

`tsd_trail_monitor.py` (clientId **95**): Peak Hour **keep-profit v1** (autopsy 2026-09-10).

- **T1** hard-banks at **+2%** (not a 4% trail that only frees after ~+7%).
- After T1 bank, shared kill **tightens to 2.5%** (broker stop ratchet via `sync_kill_quantity`).
- **T2–T4** trail with earlier triggers `(2 / 3.5 / 6 / 10)%`.
- **Do not** place primary kill at structure area-low (Chat A + autopsy: net negative on runners).
- Entry soft-skip when structure risk **> 3.5%**; ENTER requires tradable popularity.

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

## Tuesday go-live checklist (ops)

First trading day after Labor Day weekend = **Tue 2026-09-08** (Mon 9/7 holiday — ticks skip).

1. TWS paper API on **7497** before **05:15 ET** (trail loop needs it from **04:00**).
2. Confirm Task Scheduler: **QAlpha TSD Scheduler**, **Trail Monitor**, **Live TWS Sync** Enabled; Setup Watch absent/disabled.
3. First `:15` log line shows `score=v1.1` and `slots=2`.
4. HTF refresh at **04:30** (or first launch rebuilds if cache miss).
5. Keep laptop awake / plugged if possible — tasks now allow battery, but sleep still kills ticks.
6. Do **not** re-enable Setup Watch / gap agent / Approval Runner.

Ship note: `experiments/EXP-0021/STUDY_SHIP_V11.md`.

---

## Not mixed with

- Strategy Lab SIM book
- Morning gap agent
- EXP-0012 / BracketPosition experiments

