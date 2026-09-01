# UTS v2 Phase 3 — Setup Watch Agent

**Depends on:** Phase 1 (`92861e5+`)  
**Date:** 2026-09-01  
**Status:** SHIPPED (code + tests)

## Problem

Phase 1 stopped direct scan entries but had no executor to confirm setups and place orders. Queue rows sat WATCHING indefinitely.

## Solution

```
tsd_watch_queue (WATCHING)
    → setup_watch_agent.py (RTH 30s loop)
    → Lane A/B confirmation
    → execute_live_entries() (single symbol)
    → tsd_book_state.json
```

## Components

| File | Role |
|------|------|
| `candidates/setup_watch_agent.py` | RTH loop, timeout @ 11:00, IBKR quotes, entry |
| `candidates/setup_watch_confirmation.py` | Pure Lane A/B confirmation logic |
| `candidates/start_setup_watch_scheduled.ps1` | Task Scheduler wrapper |
| `candidates/register_tsd_tasks.ps1` | Registers `QAlpha TSD Setup Watch` @ 09:30 |

## Confirmation Rules

### Lane B (TSD swing — default)
- Price holds above `cross_level`
- ORB break (`price > orb_high`) **OR** VWAP reclaim
- `rvol >= 0.8` vs 20d avg pace

### Lane A (gap-style — future)
- Ports `watch_and_enter` gates: gap hold, above VWAP, vol confirming, not dumping, structure intact, 2min wait

## Gates (before confirmation)
- RTH entry window **09:35–14:00** (enforced live; relaxed in `--dry-run`)
- Phase 1 gates re-checked: score≥70, wt_gap≥3, regime BULL, dedup

## Timeout
- Rows still WATCHING at **11:00 ET** (same day) → `SKIPPED` + Telegram

## Manual Start

```powershell
py -3 candidates/setup_watch_agent.py --dry-run --once
py -3 candidates/setup_watch_agent.py --loop
.\candidates\register_tsd_tasks.ps1   # adds QAlpha TSD Setup Watch
```

## Tests

```
py -3 -m unittest discover -s tests -p "test_setup_watch*.py" -v
```

## Scan path unchanged

`tsd_scan_ibkr.py --live` → `add_to_watch_queue()` only. **No bulk `execute_live_entries`.**

## Reply for Chat A

**Phase 3 shipped:** Setup watch agent polls `tsd_watch_queue.json`, confirms Lane A/B, places TSD entries via `execute_live_entries` for confirmed symbols only.

**Register task:** `QAlpha TSD Setup Watch` daily 09:30 ET (30s loop until 14:00).

**Pipeline complete:** Scan → Queue → Watch → Entry → Trail (structure + 4-tranche).

**Action for Aaron:** Run `register_tsd_tasks.ps1`, ensure TWS open by 09:30, verify Telegram on SKIP/ENTER.
