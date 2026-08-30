# TSD 3HR Swing Pipeline — Results Log

**Track:** LONG-ONLY TSD swing (IBKR live signals, Polygon hunt-list only).  
**Status:** Phase 5 complete — scheduler + scorecard wired.

---

## Phase completion summary

| Phase | Status | Key deliverable |
|-------|--------|-----------------|
| 1 | PASS | IBKR probe, `tsd_signals.py`, `build_3h_bars.py` |
| 2 | PASS | `universe_tsd.py`, `polygon_hunt_list.py`, `pipeline.py` |
| 3 | PASS | Profiler gate, capacity, session-aware entries |
| 3.1 | PASS | IBKR bar alignment, watch-10 profiler, pool accounting |
| 4 | PASS | `tsd_trail_monitor.py` — strategy_a 4-tranche software trail |
| 5 | PASS | `scheduler.py`, `tsd_scorecard.py`, Windows task registration |

---

## IBKR probe (Phase 1) — PASS

See `results/ibkr_probe.md`:
- 3H bars work (360 bars/60D, extended hours)
- Bar timestamps: 04, 05, 08, 11, 14, 17, 19, 22, 01 ET
- Pacing: ~2.5s/symbol
- ClientId 94 (probe), no collision with scan=93 / trail=95

---

## Bar parity (Phase 3.1) — PASS

IBKR 3H vs Polygon 30m IBKR-bucketed alignment confirmed (SPY/TSLA/PACB).  
Legacy hourly midnight resample **misaligned** — not used for profiler.

---

## Profiler panel (Phase 3.1) — PASS

| Symbol | Analogs | Status | Source |
|--------|---------|--------|--------|
| SPY | 70 | OK | ibkr_3h |
| TSLA | 67 | OK | ibkr_3h |
| PACB | 66 | OK | ibkr_3h |
| NVDA | 90 | OK | polygon_30m buckets |
| CVX | 84 | OK | polygon_30m buckets |

MIN 30 gate enforced — no trade fallback.

---

## Live scan dry-runs — EXPECTED (0 signals)

Weekend / stale 3H bar (Fri 17:00 ET last bar): **0 fresh BUY crosses** on hunt list.  
This is correct behavior — not a failure.

---

## Phase 5 — Scheduler

**ET slots (per IBKR 3H bar close hour H):**
- Polygon PASS 1 @ **H:20**
- TWS PASS 2 @ **(H+3):03** with `--live` when trail monitor exists
- Trail monitor: dedicated loop (60s) or scheduler tick fallback

**Register tasks (Aaron, once):**
```powershell
.\candidates\register_tsd_tasks.ps1
```

**Manual:**
```powershell
.\venv\Scripts\python.exe candidates\tsd_scan_pipeline\scheduler.py --tick --dry-run
.\venv\Scripts\python.exe candidates\tsd_scan_pipeline\tsd_scorecard.py --write
```

---

## Open items / Phase 6+

- First **live** entry + trail exit cycle on paper (requires fresh BUY + TWS open)
- `keepUpToDate` IBKR streaming test at next 3H bar close
- Telegram alerts for TSD entries/exits (optional)

---

*Last updated: 2026-08-30*
