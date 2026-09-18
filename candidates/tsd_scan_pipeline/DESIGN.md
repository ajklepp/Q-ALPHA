# Peak Hour Performers — Live Paper design

**Status:** Live Paper primary = **1H LAUNCH @ :15** (hours **05–15** ET).  
**Product:** Peak Hour Performers v3.2 · continuation_score nominates · **case review decides** · 2 slots/scan · LONG-ONLY.  
**Equal-signal (2026-09-15):** default **ON** (`PHP_EQUAL_SIGNAL=1`). Soft stage is not graded; hard `scan>=75` still excluded.  
**Momentum-rank (2026-09-16):** default **ON** (`PHP_MOMENTUM_RANK=1`). After equal admission, rank by same-day momentum / room / tape over slow popularity. **Revert both:** `candidates/tsd_scan_pipeline/REVERT.md`.  
**Workspace:** Q-ALPHA only.

### Live Paper stack (KEEP)

| Job | Role |
|-----|------|
| `scheduler.py --tick --live` | Sole entry authority → `tsd_1h_launch_scan` |
| HTF universe @ 04:30 | Daily pass set for hourly scan |
| `tsd_popularity` | Multi-day Polygon movers + $vol leaders + live gainers + TWS MOST_ACTIVE/TOP_PERC_GAIN |
| `tsd_attention` | Attention Pool: top continuation ∪ tradable_popular ∪ soft-extension (ST optional) |
| `tsd_case_review` | ENTER / WAIT / REJECT — score alone cannot buy |
| `tsd_watch_queue` + micro-confirm + `execute_live_entries` | Admit → 1m tape confirm (+RVOL/no-chase) → pullback Limit BUY |
| `tsd_social` (Polygon + TWS news + StockTwits; X off) | Rank soft terms + case dossier evidence |
| `tsd_deep_features` (20d room/bounce + 1H path prior) | Path prior when n≥3; else profile analog fallback |
| Missed ledger `evidence` + thesis | Frozen decision snapshot for Weekly Review |
| `catalyst_ai` via OpenRouter (`gpt-4o-mini` default) | Thin PRINT/OUTLOOK (attention set) |
| `tsd_catalyst_deep` (90d lookback) | Narrative + risk flags; contradictions can REJECT |
| OpenRouter web search | Live chatter / leaderboard context inside case fusion |
| `tsd_trail_monitor` | Keep-profit v1 + **micro-confirm** WATCHING→BUY |
| `tws_intraday_sync` (clientId 96) | Marks / closed / pool / Peak Hour launch board |
| Telegram + on-fill Supabase | Immediate Aaron + dashboard awareness |

### Authority (v3.2 + equal-signal)

1. **Every valid 1H signal candle is equal** when `PHP_EQUAL_SIGNAL` is ON (default). Do not grade/rank/demote by LAUNCH vs EXTENSION vs NEUTRAL / soft-EXTENSION. Hard-extension (`scan >= 75`) still excluded.
2. **Continuation score nominates** into Attention Pool (stage terms off when equal-signal ON).
3. **Momentum-rank after equal admission** — choose scarce slots with same-day RS / volume / room / tape, not raw multi-day popularity. Popularity remains a lane (and a first-day ripper with hot tape can take without the 10-day board). Slow popular names are score-demoted. Hard-extension + case stay vetoes; they are not the main filter.
4. **Case review decides** ENTER / WAIT / REJECT. Wreckage room, toxic flags, and thin↔deep contradictions hard-REJECT. LLM ENTER remains. Rules ENTER: constructive room + momentum + tradable popularity; scan band is not a veto when equal-signal is ON (`scan < 75`).
5. **BUY only case-ENTER**, still capped at 2/scan + capacity. Take sort is momentum-adjusted continuation (confidence is tiebreak only) when `PHP_MOMENTUM_RANK` is ON.
6. **Micro-confirm before BUY** — after case ENTER, watch 1-min tape from the 1H bar close; ABORT if dumping through −1.5%/structure; wait for pullback if extended >+1.5%; skip dead tape (micro RVOL / vol_ratio_20); CONFIRM only if holding. EH wait extends to ~20m; sparse bars may use TWS last to hold-confirm (dump abort never loosened). Confirmed buys use **pullback LimitOrder** near signal. Trail loop continues polling WATCHING names.

`PHP_EQUAL_SIGNAL=0` restores pre-2026-09-15 stage grading (including the phase→`extended` leak) without reverting trails.  
`PHP_MOMENTUM_RANK=0` restores popularity-first take sort / score-as-is after admission. See `REVERT.md`. Trails / keep-profit / 2 NEW per hour are untouched by either flag.

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
- After T1 bank, shared kill **tightens to 2.5%** (broker stop ratchet **UP only** via `sync_kill_quantity`).
- **T2–T4** trail with earlier triggers `(2 / 3.5 / 6 / 10)%` — lock-profit is the tighter trail after ~+3–4% MFE, not a hard BE dump.
- **LIVE structure_stop / be_lock_1r dumps are OFF** (`TSD_LIVE_STRUCTURE_STOP` default `0`; `PHP_STRUCTURE_STOP_EXITS` is the same gate). Emergency kill remains the single protective SELL. Restore dumps: see `REVERT.md`.
- **Broker-truth qty** (`TSD_BROKER_QTY_RECONCILE` default ON) and **single protective** (`TSD_ENFORCE_SINGLE_KILL` default ON): after fills / trail ticks / TWS sync, `legs[].shares` matches TWS; exactly one working STP/STP LMT (`enforce_single_protective_kill`, owner-cancel includes trail 85/95). Cap-scrub on flat → `CLOSED_SCRUB` in `tsd_scan_pipeline/tsd_watch_queue.py`. Disable: `TSD_BROKER_QTY_RECONCILE=0` and/or `TSD_ENFORCE_SINGLE_KILL=0` (see `REVERT.md`).
- After ~+3–4% MFE, `maybe_lock_profit_via_trail` tightens remaining tranche trail widths and may ratchet kill **UP** (capped under last). Not a second broker BE sell.
- Offline book helper: `migrate_structure_stop_to_trail.py` (dry-run default). ATRC already cleared on the laptop; helper remains for other opens.
- **Do not** place primary kill at structure area-low (Chat A + autopsy: net negative on runners).
- Entry soft-skip when structure risk **> 3.5%**; ENTER requires tradable popularity.
- **Shadow 3R paper** (`tsd_shadow_multi_target.py`): same **Peak Hour** fills, software banks at **0.35/0.50/0.90R** (50/25/25); Dashboard tab **3R Paper** (not Track 100). Mirror on `record_entry`; idempotent backfill from `tsd_book_state.json` (or Supabase if the local book is missing). No second broker exits. Unchanged by the LIVE all-trailing gate.

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
| `tsd_shadow_mt3_book.json` | Peak Hour 3R shadow paper (software exits only) |
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
   **QAlpha TSD Scheduler** Settings must be **Do not start a new instance** and **Stop if longer than 2 hours** (hour-8 abort: a 5-min overlap must not kill an in-flight 1H LAUNCH). Re-run `.\candidates\register_tsd_tasks.ps1` or set it in the GUI.
3. First `:15` log line shows `score=v1.6+equal_signal+momentum_rank` (or drops the overlay suffix when that flag is `0`) and `slots=2`. Telegram SCAN includes `equal_signal=ON|OFF` and `momentum_rank=ON|OFF`.
4. HTF refresh at **04:30** (or first launch rebuilds if cache miss).
5. Keep laptop awake / plugged if possible — tasks now allow battery, but sleep still kills ticks.
6. Do **not** re-enable Setup Watch / gap agent / Approval Runner.

Ship note: `experiments/EXP-0021/STUDY_SHIP_V11.md`.

---

## Not mixed with

- Strategy Lab SIM book
- Morning gap agent
- EXP-0012 / BracketPosition experiments

