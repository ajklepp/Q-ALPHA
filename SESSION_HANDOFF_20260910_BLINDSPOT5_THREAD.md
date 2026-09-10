# Session handoff ΓÇö Q-ALPHA work from "Blind-spot #5 event distance" thread

**Date:** 2026-09-10  
**Source chat:** Agents ΓåÆ **Track 100** ΓåÆ "Blind-spot #5 event distance" (this thread was moved with the workspace; Q-ALPHA-relevant work is summarized here so the long-running **q-alpha** chat with the same title can continue).  
**Track 100 study:** now a **separate** repo ΓÇö not Q-ALPHA live. See `TRACK_100_MOVED.md` and https://github.com/ajklepp/track-100.

---

## Do not re-litigate

- Track 100 Wave Cross trough study is **out of Q-ALPHA**. Continue that only under `C:\Users\ajkle\Documents\Track 100`.
- This file is for **Peak Hour / TSD live paper** continuity only.

---

## Shipped on Q-ALPHA `main` (this thread)

| Commit | What |
|--------|------|
| `326ab17` | Keep-profit v1: T1 bank @ **+2%**, then kill tighten **2.5%**; structure skip **>3.5%**; ENTER needs popularity |
| `724dc4d` | **Micro-confirm**: after case ENTER, watch 1m tape from 1H bar close before BUY; ABORT dump ΓêÆ1.5%/structure; trail polls WATCHING |
| `9561ab9` | Harden entries: EH wait **20m** + **TWS last** hold; dead-tape RVOL / vol_ratio_20; **no chase** >+1.5%; pullback **LimitOrder** near signal |
| `4752bd7` | **False full exit fix**: wait for full fill; only close filled shares; **orphan long reopen** (book CLOSED vs broker long); pool `rebuild_deployed_from_book` |
| `221fa36` | Removed nested `Track 100/` from this repo; pointer `TRACK_100_MOVED.md` |

---

## Live paper entry path (current)

```
1H LAUNCH @ :15 ΓåÆ case ENTER ΓåÆ WATCHING queue
  ΓåÆ process_micro_confirm_queue (1m hold/abort / RVOL / no-chase)
  ΓåÆ execute_live_entries (prefer_limit pullback)
Trail monitor: micro-confirm even when flat + orphan reopen + kill reconcile
```

Key files:

- `candidates/tsd_scan_pipeline/tsd_micro_confirm.py`
- `candidates/tsd_scan_pipeline/tsd_watch_queue.py` (`process_micro_confirm_queue`)
- `candidates/tsd_scan_pipeline/tsd_1h_launch_scan.py`
- `candidates/tsd_scan_pipeline/tsd_trail_monitor.py`
- `candidates/tsd_scan_pipeline/tsd_keep_profit.py`
- `candidates/tsd_scan_pipeline/tsd_exit.py` (full-fill wait)
- `candidates/tws_intraday_sync.py` (`_reconcile_broker_orphan_longs`)
- `candidates/tsd_scan_pipeline/DESIGN.md` (authority + micro-confirm)

---

## Trade audit 2026-09-10 (TWS vs book)

**TWS opens:** ATRC 4, NX 12, JANX 1, ARQQ 12  

**Bug found:** JANX was **CLOSED** in book (structure exit recorded 11 sh) but **1 sh still long** at broker (partial fill + false full close).

**Repaired locally + synced to Supabase:**

- JANX reopened as OPEN **1 sh** (T4 residual); exit adjusted to 10 sh  
- Pool rebuilt: cash **~$2155** ┬╖ deployed **~$741** ┬╖ **4 opens**  
- Dashboard verify: JANX / ATRC / ARQQ / NX all OPEN  

**Note:** TWS *Daily P&L* Γëá book entryΓåÆexit P&L (mark-to-market vs fill accounting). Expect gaps (e.g. FWDI).

---

## Replay / research context (this thread)

- Range replay Aug 31ΓÇôSep 10 was weak; autopsy: many green-then-giveback; keep-profit + structureΓëñ3.5% helped A/B.  
- Micro-confirm shadow: avoided some dump entries; sparse EH bars can TIMEOUT winners ΓÇö mitigated by EH wait + TWS last (do **not** loosen dump abort).  
- Live rules context: SHARE_LOT=4, 4-tranche trail (collapses if &lt;8 shares), long-only.

---

## For the 20h q-alpha agent ΓÇö next actions

1. `git pull` on Q-ALPHA (`main` includes commits above).  
2. Confirm book still has **4 opens** including JANX; trail/kill armed on residual.  
3. Do **not** re-add Track 100 into this repo.  
4. Continue Peak Hour ops / any Blind-spot #5 EXP-0021 work from `experiments/EXP-0021/STUDY_BLINDSPOT_05_EVENT_DISTANCE.md` separately from Track 100.

---

## Agent instruction (paste into 20h q-alpha chat)

```
Read and follow C:\Users\ajkle\Documents\Q-ALPHA\SESSION_HANDOFF_20260910_BLINDSPOT5_THREAD.md
That file is the Q-ALPHA-relevant clone of work from the Track 100 "Blind-spot #5 event distance" thread (micro-confirm, keep-profit, JANX orphan audit, Track 100 repo split). Continue from there; do not re-run Track 100 inside Q-ALPHA.
```
