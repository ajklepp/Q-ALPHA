# Q-ALPHA LEARNING AUTOPSY — week ending 2026-09-11

**MachineId:** `9b425164-99a8-4ab1-bd2e-136c69aa547e`  
**Project:** `C:\Users\ajkle\Documents\Q-ALPHA` (Documents, not OneDrive)  
**As-of sources:** php_weekly / scorecard generated **2026-09-11T16:04** ET; Fri scheduler + TWS logs; live book snapshot ~16:36 ET.  
**Scope:** Analysis only — no trades, no process kills.

---

## 1. Executive one-liner

Peak Hour ran a full week of scans (**44** runs → **684** launches → **16** book entries) but **Friday finished 11/11 hours at `entered=0`**: morning slot burned on **NUAI `no_fill_timeout`** + **STX `already_confirmed`**, later hours mostly **CASE_REJECT / already_confirmed / cap 2/hour**, while exits were dominated by **`base_break_down`** (**8/15** closed legs in the weekly outcomes table; closed PnL **−$139.74**), with **ATRC/NX** still open after **T1 banks** and one **manual SMR** flat (−$10.36, not in the weekly entered-16 list).

---

## 2. What the system RAN this week

### Peak Hour weekly funnel (`php_weekly_20260911`)
| Metric | Value |
|--------|------:|
| Window | last 7 days (as of 2026-09-11T16:04:43-04:00) |
| Scans run | **44** |
| HTF-pass evals (sum) | **9581** |
| Symbols scanned (sum) | **9581** |
| 1H launches | **684** (7.14% of scanned) |
| Entered | **16** (2.34% of launches) |

**Reject-reason histogram (weekly):**
| Reason | Count |
|--------|------:|
| `polygon_1h_aggs_start_labeled` | 6719 |
| `stale_1h_bars` | 1262 |
| `extension_hard` | 538 |
| `hour_not_allowed` | 378 |

**Source scan coverage:** Mon 09-08 through Fri 09-11, hours **05–15** each day (`php_scan_20260908_*` … `php_scan_20260911_1515.json`) — **11 hours × 4 sessions = 44**, matching the funnel.

**Structure mode (live):** every Fri launch row and trail banner showed **`KILL ONLY until +1R`**.

### Scorecard note (`scorecard_20260911`)
The separate **TSD Pipeline Scorecard** (window 2026-09-04→today) reports **Total scans: 0 / Signals: 0 / Live entries attempted: 0** — that card is **not** counting Peak Hour PHP scans. Useful fields from it:
- Trail monitor passes: **136**; trail actions: **7**; exits (trail/kill/time_cap): **0**
- Open positions: **2** `['ATRC', 'NX']`
- Closed leg exits (book): **35**
- Pool: **$2,638.56** deployed **$234.47**
- Last scan: 2026-09-11T15:15:01; last trail monitor (scorecard): 15:57:54

### Options study (`options_study_20260911`)
Offline study empty: **0** scans/signals/options rows in window; no counterfactual ranking. Does not change live scoring.

### Friday 2026-09-11 hourly behavior (scheduler telegram summaries)
All hours used HTF universe size **168**. **`entered=0` every launch hour** after the morning attempt path failed to fill.

| Hour ET | launches | take | entered | Notable |
|--------:|---------:|-----:|--------:|---------|
| 5 | 40 | 2 | **0** | Cap **2/hour**; CASE attention=12 **ENTER=6**; took **NUAI**+**STX**; STX `already_confirmed`; NUAI micro→**`no_fill_timeout`** |
| 6 | 20 | 2 | **0** | Structure KILL ONLY |
| 7 | 7 | 0 | **0** | |
| 8 | 12 | 0 | **0** | |
| 9 | 15 | 1 | **0** | Near RTH open; SMR appears in book ~09:28 (see §3) |
| 10 | 12 | 1 | **0** | Sync warns: `tsd_mark_mismatch:ATRC` |
| 11 | 2 | 0 | **0** | Sync warns: ATRC mark mismatch |
| 12 | 3 | 0 | **0** | |
| 13 | 9 | 0 | **0** | Sync warns: ATRC mark mismatch |
| 14 | 11 | 0 | **0** | Sync warns: `tsd_mark_mismatch:NX`; php_scan take_n=0 |
| 15 | 14 | 1 | **0** | **MMED** `already_confirmed` / UNCHANGED; **SMR** top rank **CASE_REJECT**; take_count=1 was MMED CASE ENTER path that did not new-enter |

Scheduler state shows launch ticks for Fri hours 05–15 all completed (last `launch:2026-09-11:15` at 15:27:11 ET). Early Fri also ran trail **backup one-shots** when dedicated loop looked idle (hours 5–6), then **“Trail loop heartbeat OK — tick skips trail”** from hour 7 onward.

---

## 3. What trades it ACTUALLY took vs skipped/failed

### Weekly entered symbols (php_weekly — 16)
**ARQQ, ATRC, BETA, CAI, CBLL, CLYM, CNH, FGI, FWDI, METC, NX, OCUL, PURR, QMCO, RDW, SLS**

Still **OPEN** at weekly report time: **ATRC**, **NX**.  
All other listed names show **CLOSED** in the outcomes table.

### Friday entry attempts / skips (from `tsd_scheduler_2026-09-11.log` + `last_1h_launch.json`)
| Symbol | What happened | Evidence |
|--------|---------------|----------|
| **NUAI** | Queued NEW → micro PENDING → **ENTRY FAIL `no_fill_timeout`** | Hour-5 log; only Fri `no_fill_timeout` |
| **STX** | CASE ENTER but **LIVE SKIP `already_confirmed`** (QUEUE KEEP CONFIRMED) | Hour-5; burned 1 of 2 cap slots without a new fill |
| **MMED** | Hour-15 CASE ENTER / status **UNCHANGED** `queue_reason=already_confirmed` | `last_1h_launch.json` rank 2 |
| **SMR** | Hour-15 **CASE_REJECT** (conf 0.85); earlier Fri **was open 28sh** then **manual** flat | Book + TWS sync + weekly not listing SMR in entered-16 |
| Cap | **`taking 2 (cap 2/hour)`** with 6 CASE ENTER at hour 5 | Explicit log line |
| CASE_REJECT | Hour-15: **5× CASE_REJECT** (SMR, VSTM, ACHV, WDAY, VIR) + 1 CASE_WAIT (ZYME) among 14 ranked | `last_1h_launch.json` |

**Hour-5 CASE rollup:** ENTER=6 (NUAI, STX, HPE, IREN, CRCL, SNOW), WAIT=2 (FIGR, SNDK), REJECT=4 (BMNR, MRVL, LITE, ASST) — then hard cap took only top 2.

### Friday extra: SMR (not in php_weekly entered-16)
- Book leg: opened **2026-09-11T09:28:14** @ **9.68**, **28** shares, `bar_hour=9`, closed **09:38:39**, reason **`manual`**, exit **9.31**, PnL **−$10.36**
- TWS sync 09:34 showed positions including `SMR: 28.0` alongside ATRC/NX/ARQQ
- php_weekly outcomes table does **not** include SMR; treat as Fri ops overlay, not part of the −$139.74 weekly closed sum below

### Funnel reality check
**684 launches → 16 enters (2.34%)** already thin; **Friday added 0 net new enters** after morning fill failure + already_confirmed consumption of the 2/hour budget.

---

## 4. Exit autopsy

### Closed PnL from php_weekly outcomes table (facts only)
Sum of **15 CLOSED** rows (ATRC/NX OPEN excluded):

| Symbol | PnL | Exit |
|--------|----:|------|
| CBLL | −12.96 | base_break_down |
| CBLL | −2.64 | base_break_down |
| PURR | **+2.40** | structure_stop |
| FWDI | **+1.46** | base_break_down |
| QMCO | −16.56 | base_break_down |
| BETA | −13.80 | broker_flat_sell_fill |
| METC | −10.20 | kill |
| RDW | −14.16 | broker_kill_fill |
| CLYM | −8.48 | base_break_down |
| CAI | −15.84 | broker_kill_fill |
| OCUL | −9.84 | broker_flat_sell_fill |
| FGI | −13.60 | kill |
| SLS | −7.60 | base_break_down |
| CNH | −5.80 | base_break_down |
| ARQQ | −12.12 | base_break_down |

- **Closed PnL total: −$139.74**
- **W/L:** 2 winners / 13 losers
- **Exit-reason mix:** `base_break_down` **8** (53%), `kill` **2**, `broker_kill_fill` **2**, `broker_flat_sell_fill` **2**, `structure_stop` **1**

**Dominance:** losers are mostly **base_break_down** full exits; only **PURR** (+2.40 structure_stop) and **FWDI** (+1.46, ironically also tagged base_break_down) printed green in the weekly table.

### Kills / broker flats (same table + Fri log echoes)
- Explicit **kill**: METC −10.20, FGI −13.60  
- **broker_kill_fill**: RDW −14.16, CAI −15.84  
- **broker_flat_sell_fill**: BETA −13.80, OCUL −9.84  
- Fri log also echoed older closes (HPE kill −9.50, JANX kill_escalate/broker_flat −4.70, etc.) outside the weekly outcomes table — not added to −$139.74

### Manual SMR
- **SMR** closed **manual** −$10.36 (book + repeated TWS/scheduler closed lines). Not in weekly entered-16 / outcomes sum.

### T1 banks on opens ATRC / NX (live book `trail.tranches`)
| Symbol | Entry | T1 | Notes |
|--------|------:|----|-------|
| **ATRC** | 53.29 × 2sh (2026-09-09 12:18) | T1 **closed** `t1_bank` @ trigger **54.523210535** (2026-09-11 **09:52:14**); T2 still open; peak_high **55.16**; last_close **54.595**; kill **51.95775**; `php_keep_profit=true`; structure_stop **53.13** (`be_lock_1r`) | Scorecard open; residual after bank reflected in later sync qty (2sh) |
| **NX** | 21.11 × 7sh book / had been 12sh pre-bank (2026-09-10 15:19) | T1 **closed** `t1_bank` @ **21.5322** (2026-09-11 **09:37:52**); **T2 trailing** (run_high 21.97); T3/T4 open; peak_high **21.97**; last_close **21.68**; kill **20.58225**; structure_stop **21.05** | Keep-profit path working on the survivor |

Approx T1 bank marks (entry→t1_exit×t1_shares, not a separate ledger line): ATRC ≈ **+$2.47**, NX ≈ **+$2.11** — **not** included in the −$139.74 closed table (positions still OPEN).

### Trail monitor scorecard vs exits
136 passes / 7 trail actions / **0** attributed trail/kill/time_cap exits in the scorecard — consistent with **base_break / kill / broker / manual** doing the damage, not software trail profit-taking beyond T1 banks.

---

## 5. What it could have done BETTER

1. **Fills / premarket entry quality** — NUAI was the hour-5 #1 take and died on **`no_fill_timeout`** (FILL_WAIT path). Premarket limit resting / longer wait / chase policy needs a measured change; a dead #1 under a hard cap wastes the hour.
2. **`already_confirmed` consuming cap slots** — STX (hour 5) and MMED (hour 15) counted toward take/ENTER without creating a new fill. Cap should prefer **new** risk, or already_confirmed should not occupy a take slot.
3. **CASE_REJECT vs continuation rank** — Hour 15 top name SMR was CASE_REJECT while lower MMED was ENTER-but-unchanged; case gate and continuation rank disagree too often → empty hours.
4. **Capacity 2/hour is brittle when ENTER≫2** — Hour 5 had 6 ENTER and only 2 takes; after one no_fill + one already_confirmed → **zero** new risk for the day.
5. **Exit mix** — `base_break_down` at **8/15** closed legs with only **2** weekly winners says structure/base exits are harvesting losers without enough T1/T2 banking on the failed cohort (survivors ATRC/NX show the intended path).
6. **Ops: trail process tree vs true duplicates** — Two `python.exe` lines (`venv\Scripts\python.exe` parent + `pythoncore` child) are **one** `--loop --adaptive` launch on this Windows install. `start_tsd_trail_monitor_scheduled.ps1` explicitly warns: do **not** kill the pythoncore child (that kills the real trail). True duplicate = two `start_tsd_trail_monitor_scheduled.ps1` starters. Client-id churn still noted: trail client **95** (RTH) vs **85** (EXTENDED fallback). Post-audit: single starter tree restarted; heartbeat refreshed 16:45 ET; ATRC/NX untouched.
7. **Mark sync mismatches** — Repeated `tsd_mark_mismatch` on ATRC/NX (and SMR while open) → cloud/local drift and “Peak Hour sync failed” telegrams; muddy ops signal, not necessarily bad exits.
8. **Scorecard vs PHP funnel** — Scorecard “0 scans” while PHP ran 44 confuses EOW reading; reporting should label PHP vs legacy TSD cards.
9. **Options overlay** — Study returned empty; no ranking help this week.
10. **SMR manual** — 10-minute hold, manual flat −$10.36, absent from weekly entered list → book/funnel accounting gap for human overlays.

---

## 6. Concrete next-week changes (prioritized)

### P0 — must fix before trusting next live week
1. **Cap accounting:** do **not** let `already_confirmed` / UNCHANGED consume a `take` slot; only NEW live entry attempts count against **2/hour**.
2. **Fill policy for premarket/micro:** on `no_fill_timeout`, either (a) extend limit rest + one requote, or (b) immediately promote next CASE ENTER under the same hour cap — so a dead #1 cannot zero the hour.
3. **Single trail-monitor owner:** enforce one **starter** (`start_tsd_trail_monitor_scheduled.ps1`) + lock; treat venv+pythoncore as one tree (never kill pythoncore child). Keep client **95** primary / **85** fallback only on connect fail — audit starter count at RTH open.

### P1 — high leverage process/rules
4. **CASE vs rank calibration:** log/alert when top continuation is CASE_REJECT and a lower rank is ENTER; tighten REJECT reasons that fire on leaders with CONSTRUCTIVE_ROOM + momentum.
5. **Base-break review:** sample the 8 `base_break_down` losers for false-break vs true failed launch; consider requiring +1R / T1 bank progress before allowing full base-break flatten on day-1 names (hypothesis — validate on replay before code change).
6. **Funnel hygiene:** include manual/broker-reconciled names (SMR) in weekly outcomes **or** explicitly tag “non-PHP entry” so EOW PnL isn’t split across silent extras.
7. **Sync marks:** fix ATRC/NX cloud vs local mark path that spammed `tsd_mark_mismatch` Fri midday.

### P2 — useful but not blocking
8. Relabel scorecard “scans=0” vs PHP weekly so EOW voice/reports don’t contradict.
9. Re-run options study **without** `--skip-options` when PHP signals exist; keep offline-only.
10. Track hour-level `take→entered` conversion on the dashboard (Fri was 7 takes across hours, **0** entered).

---

## 7. Evidence paths

### Laptop (canonical)
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\tsd_scan_pipeline\results\scorecard_20260911.md` (| `.json`)
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\tsd_scan_pipeline\results\peak_hour_scans\php_weekly_20260911.md` (| `.json`)
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\tsd_scan_pipeline\results\options_study_20260911.md`
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\logs\tsd_scheduler_2026-09-11.log`
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\logs\tws_sync_2026-09-11.log`
- `C:\Users\ajkle\Documents\Q-ALPHA\candidates\tsd_book_state.json` (live open ATRC/NX + SMR closed leg)
- PHP hour scans: `...\results\peak_hour_scans\php_scan_20260908_*.json` … `php_scan_20260911_*.json`
- Trail snapshots: `...\results\trail_monitor_20260911_*.json`
- Scheduler state: `...\results\tsd_scheduler_state.json`
- Dup-trail helper (ops, not run by this autopsy): box copy documents intended laptop lock path `...\candidates\logs\tsd_trail_monitor.lock`

### Box copies used for this report
- `/workspace/uploads/php_weekly_20260911.md`
- `/workspace/uploads/scorecard_20260911.md`
- `/workspace/uploads/options_study_20260911.md`
- `/workspace/uploads/tsd_scheduler_today.log` (Fri scheduler)
- `/workspace/uploads/tws_sync_today.log`
- `/workspace/uploads/live_tsd_book_state.json`
- `/workspace/uploads/live_tsd_scheduler_state.json`
- `/workspace/uploads/last_1h_launch.json` (Fri hour 15)
- `/workspace/uploads/php_scan_latest.json` / `php_scan_1415.json`
- `/workspace/uploads/trail_monitor_latest.json` (client 95) / `live_trail_monitor_1641.json` (client 85)
- `/workspace/uploads/tsd_weekly_reports_2026-09-11.log`
- `/workspace/uploads/fix_trail_dup.ps1` (documents duplicate-monitor problem)

### Prior root-cause facts (confirmed against logs)
Fri `entered=0` every hour; **NUAI** `no_fill_timeout`; **STX/MMED** `already_confirmed`; **cap 2/hour**; **CASE_REJECT**; reject mass from **polygon_** / **extension_hard**; structure **KILL ONLY until +1R**; opens **ATRC/NX**.

---

*End of learning autopsy. Numbers cited from files above; closed PnL −$139.74 is the arithmetic sum of the php_weekly CLOSED table only. SMR manual −$10.36 and unrealized/T1 marks on ATRC/NX are listed separately and not mixed into that sum.*
