# TSD 3HR Swing Scanner — Design (locked)

**Status:** Phase 5 complete (scheduler + scorecard + Windows tasks).  
**Track:** LONG-ONLY 3-hour swing, parallel to gap/momentum morning agent.  
**Workspace:** Q-ALPHA only.

---

## Three-pass pipeline

```
3H bar closes (:00 ET) → TWS LIVE (:03) → Polygon pre-filter (:20)

PASS 1  polygon_hunt_list.py @ H:20  (after each 3H bar close H)
PASS 2  tsd_scan_ibkr.py       @ (H+3):03  (IBKR live — signal SoT)
PASS 3  tsd_trail_monitor.py    (60s loop — software trail exits)
```

Orchestrated by `scheduler.py --tick` (Windows Task Scheduler every 5 min).

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
| `register_tsd_tasks.ps1` | Windows tasks: Scheduler (5m) + Trail (daily 04:00) |
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

## Not mixed with

- Strategy Lab SIM book
- Morning gap agent
- EXP-0012 / BracketPosition experiments
