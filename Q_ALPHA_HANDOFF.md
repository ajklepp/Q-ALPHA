# Q-ALPHA SYSTEM HANDOFF
## Operational state — hand this to a fresh assistant with zero prior context

**Version focus:** Peak Hour Performers (Live Paper) · dashboard v3.1  
**MOTHBALLED (2026-09-04):** Gap Strategy Lab SIM — tasks Disabled; dashboard **Weekly Research** tab instead  
**Companion architecture doc:** `Q_ALPHA_MASTER_CONTEXT.md`  
**Glossary:** `GLOSSARY.md` (also **📖 Glossary** dashboard tab)  
**Canonical product card:** `candidates/uts_v2/PEAK_HOUR_PERFORMERS.md`  
**Last updated:** 2026-09-04

---

## WHO / WHAT

Aaron Klepp — Ontario. Building Q-Alpha: **Peak Hour Performers** live IBKR paper (1H LAUNCH hours 05–15 ET @:15, continuation ranker, 2 slots/scan).  
Gap Strategy Lab SIM and gap Autonomous Agent are **not Live** (code archived / Disabled).

**Dashboard:** https://q-alpha-lshnrvza2radqpkjrkf52m.streamlit.app  
**Repo:** `ajklepp/Q-ALPHA` · branch `main` · entry `dashboard.py`  
**Canonical local path:** `C:\Users\ajkle\Documents\Q-ALPHA` (not OneDrive — Aug 2026)

---

## LIVE PAPER (PRIMARY) — Peak Hour / TSD

| | Peak Hour Performers |
|--|----------------------|
| Code | `candidates/tsd_scan_pipeline/` + `tsd_trail_monitor.py` + continuation ranker |
| Money | IBKR paper + `tsd_pool_state.json` / `tsd_book_state.json` |
| Schedule | **QAlpha TSD Scheduler** (`--tick --live`, 1H @:15 hours **05–15**) + **Trail Monitor** + **Live TWS Sync** |
| Supabase | `tsd_positions`, `tsd_pool_snapshots`, `tsd_watchlist` |
| Dashboard | **Live Status** + **Weekly Research** (funnel / EXP-0021 hitch) |

**Keep Disabled:**
```powershell
schtasks /Change /TN "QAlpha Autonomous Agent" /DISABLE
schtasks /Change /TN "QAlpha Approval Runner" /DISABLE
schtasks /Change /TN "QAlpha Strategy Lab" /DISABLE
schtasks /Change /TN "QAlpha Strategy Lab Mark" /DISABLE
schtasks /Change /TN "QAlpha Strategy Lab Settle" /DISABLE
```
Re-register Lab (stays Disabled): `.\strategy_lab\register_lab_tasks.ps1`

**TSD cloud schema:** `candidates/sql/tsd_cloud.sql`  
**Force TSD push (TWS open):** `.\venv\Scripts\python.exe candidates\tws_intraday_sync.py --repair`

---

## ARCHIVE — THREE TRACKS (historical; Lab mothballed)

| | TSD live (PRIMARY) | Gap agent (RUNOFF) | Strategy Lab (SIM, Disabled) |
|--|--------------------|--------------------|------------------------------|
| Code | `candidates/tsd_scan_pipeline/` | `candidates/autonomous_agent.py` | `strategy_lab/live_forward.py` |
| Money | IBKR paper + tsd_* state | residual `pool_state.json` | SIM dual pools — no IBKR |
| Schedule | TSD Scheduler + Trail | DISABLED | DISABLED (was 9:35 ET) |
| Dashboard | Live Status PRIMARY | Gap runoff if opens | **Weekly Research** (not Lab tabs) |

---

## LEGACY TWO-SYSTEM TABLE (gap agent was live — now runoff only)

| | Live agent (runoff) | Strategy Lab (mothballed) |
|--|---------------------|---------------------------|
| Code | `candidates/autonomous_agent.py` | `strategy_lab/live_forward.py` |
| Money | IBKR paper + `candidates/pool_state.json` | SIM dual pools (archive only) |
| Schedule | **DISABLED** | **DISABLED** — do not re-enable for Live Paper |
| Telegram | Q-ALPHA agent messages | Prefixed Strategy Lab (idle) |
| Data | Needs TWS | Polygon 1-min (research / replay only) |

---

## CURRENT LAB STATE (MOTHBALLED 2026-09-04)

```
status:              mothballed — tasks Disabled
policy:              Peak Hour covers movers; no gap Live Paper
dashboard:           Weekly Research tab (php_weekly funnel + EXP-0021)
code:                strategy_lab/ kept for exit A/B replay only
```

Do **not** call `reset_forward.py` or re-enable Lab tasks unless Aaron explicitly starts gap research again.

---

## HOW TO RUN STRATEGY LAB (archive — Disabled)

### A) Automatic (kept Disabled)
Windows Task Scheduler tasks **`QAlpha Strategy Lab` / Mark / Settle**:
- Register script creates then **DISABLES**: `.\strategy_lab\register_lab_tasks.ps1`
- Do not `/ENABLE` unless researching gaps off Live Paper path.

Verify task:
```powershell
schtasks /Query /TN "QAlpha Strategy Lab" /V /FO LIST
```

### B) Manual live (research only — not Live Paper)
```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
.\venv\Scripts\python.exe strategy_lab\live_forward.py
```

### C) Replay / dry-run (safe — isolated)
```powershell
.\venv\Scripts\python.exe strategy_lab\live_forward.py --replay 2026-08-21
```
- Writes `strategy_lab/results/forward_state_replay.json`
- Does **not** overwrite live `forward_state.json` or the live prediction log
- Telegram messages prefixed **`[DRY-RUN]`**

### Reset (dangerous after live starts)
```powershell
.\venv\Scripts\python.exe strategy_lab\reset_forward.py
```
**Only** this script sets pools back to **$3000**. Wipes forward predictions / closed-trade history. **Never** run after real forward trades if you care about the equity curve.

---

## TELEGRAM — STRATEGY LAB

Uses same bot/chat as the agent (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` via `autonomous_agent.send_telegram`). Best-effort (never crashes the run).

| Event | Message shape |
|-------|----------------|
| Start | `🧪 Strategy Lab STARTED — {date}, entry=immediate, dual $3000 pools.` |
| Market closed / holiday | `🧪 Strategy Lab — market closed today, not running.` then exit |
| After scan | `🧪 Scan complete — N candidates: {tickers}.` |
| Each entry | `🧪 ENTERED {ticker} @ {price} (A+B pools).` |
| EOD | `🧪 Lab day summary: …` after morning entry (NOT agent EOD). Real Lab settle ~16:20 ET; agent EOD = Modal `run_eod_monitor` 4:15 PM ET |
| Fatal | `🧪 Strategy Lab ERROR: {short reason}` |

Replay adds **`[DRY-RUN]`** prefix. Holidays/weekends: ET `is_trading_day` → market-closed Telegram (verified Sunday 2026-08-23).

---

## DAILY SCHEDULE (Peak Hour Performers — Sep 2026)

```
Open TWS paper (port 7497) — required for entries + marks
:15 after each 1H bar close (hours 05–15 ET)
                              Task "QAlpha TSD Scheduler" → --tick --live
~every 60s (market hours)     Task "QAlpha TSD Trail Monitor" → kill/tranche trail
~07:00 / every 30m            Live TWS Sync → Supabase
every 30m                     Readonly Mirror (non-critical)

DISABLED: Autonomous Agent · Approval Runner · Strategy Lab Entry/Mark/Settle
```

**Live marks/closes SoT:** local `candidates/tws_intraday_sync.py` (TWS clientId **96**).
Modal cannot reach `127.0.0.1:7497`. After `intraday_monitor.py` changes, always
`python -m modal deploy candidates/scheduler.py`. Stale marks on Streamlit Cloud:
check `candidates/logs/tws_sync_YYYY-MM-DD.log` **Supabase verify** block (not dashboard).
Agents **cannot** create Windows tasks — Aaron runs once:

```powershell
schtasks /Create /F /TN "QAlpha Live TWS Sync" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ajkle\Documents\Q-ALPHA\candidates\start_tws_intraday_scheduled.ps1`"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:40 /RI 30 /DU 06:30
```

Verify: `schtasks /Query /TN "QAlpha Live TWS Sync" /FO LIST`  
Manual repair (TWS open): `.\venv\Scripts\python.exe candidates\tws_intraday_sync.py --repair`  
`--repair` re-reads today's CLOSED IBKR_PAPER sells; TWS fill px/reason wins over stop_price; pool rebuilt (no double-close).

**TSD dashboard (PRIMARY Live Status):** run `candidates/sql/tsd_cloud.sql` once in Supabase SQL editor.
Local `tsd_book_state.json` → `tsd_supabase_sync.py` (via TWS sync) → `tsd_positions` + pool + watchlist.
Streamlit Live Status reads TSD tables first; gap runoff demoted to secondary section.
Dashboard **Weekly Research** = Peak Hour funnel + EXP-0021 hitch (Lab tabs removed).

### Agent morning list (Phase 2 — TWS pipeline) — RUNOFF / DISABLED for new entries

- Default: `QALPHA_USE_TWS_SCAN=1` → TWS scanners (**~50 rows each**, union ≈100) → mcap lanes → score full shortlist → **watch top 10** / **trade top 3**.
  - **TRADE** mcap ≥ $150M (orders; no $5 floor; pool affordability applies)
  - **LEARN** $50–150M → learn file + may appear on watch 10 (**never bracketed**)
  - **IGNORE** &lt; $50M
- Revert: `$env:QALPHA_USE_TWS_SCAN='0'` → Polygon `full_market_scan`
- Dry scan (no orders): `.\venv\Scripts\python.exe candidates/tws_scan_pipeline/scan_only.py`
- Design: `candidates/tws_scan_pipeline/DESIGN.md`
- Lab SIM Polygon path unchanged (parallel book).

---

## DASHBOARD TABS

```
📊 Live Status      — **TSD-primary**: pool KPIs, TSD opens, watch-10; gap runoff section if legacy opens
📋 Trade Log        — gap-agent history (legacy caption); TSD closed → weekly scorecard (v1)
📈 Performance      — gap-agent equity (legacy caption)
🔧 System Health
📓 Daily Reviews
🔬 Ticker Profiles  — Supabase `ticker_profiles` (anon); local JSON fallback; Refresh gated if no POLYGON on Cloud
🧪 Strategy Lab     — SIM A (trail) vs B (targets); marks / residual tranches; closed = flat only; **A card: T4 trailing** = T4-only runners (not Live T3)
🧪 Lab Trade Log    — chronological SIM closed + today's entries; **≠** IBKR agent Trade Log
📖 Glossary         — renders GLOSSARY.md
```

Local: `.\start_dashboard.ps1` (detached). Cloud auto-deploys on push to `main`; reboot if cache stale.

**Ticker Profiles on Cloud:** were empty when only local `profiles/*.json` existed (not in git). Now the 9:20 agent upserts to Supabase; Cloud reads via anon. Aaron must run `candidates/sql/ticker_profiles.sql` once in the SQL editor if the table is missing.

---

## INFRASTRUCTURE

| Piece | Role |
|-------|------|
| GitHub `ajklepp/Q-ALPHA` | Source of truth |
| Streamlit Cloud | Public dashboard |
| Supabase | Agent tables + `strategy_lab_state` + `ticker_profiles` |
| Modal `qalpha-scheduler` | Intraday + EOD monitors |
| Polygon $79 | Lab bars, scans, news (15-min delayed “live”) |
| Telegram `@MyQalphaBot` | Alerts |
| Windows Task Scheduler | TSD scan + trail + TWS sync + Lab 9:35 (gap agent DISABLED) |

### Env (never commit `.env`)
`POLYGON_API_KEY`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_ANON_KEY` / `SUPABASE_PUBLISHABLE_KEY`, `TELEGRAM_*`, `OPENROUTER_API_KEY`, …

**Cloud secrets today:** still include service key for most tabs — **known debt**; Lab tab uses anon+RLS. Fix before real-money account data.

### Supabase (lab)
- Table: `strategy_lab_state` (JSONB state keyed by `flag_date`)
- Writer: `lab_state_sync.upsert_forward_state` (service)
- Reader: `fetch_latest_forward_state_anon` (dashboard Strategy Lab)

### Supabase (Ticker Profiles)
- Table: `ticker_profiles` — SQL: `candidates/sql/ticker_profiles.sql` (run once if missing)
- Writer: agent `_generate_watchlist_profiles` + dashboard Refresh → `upsert_ticker_profile_safe` (service)
- Reader: `fetch_ticker_profile_anon` / `load_profile` (dashboard; local JSON fallback)
- Scope: full watchlist (TWS `WATCH_TOP_N=10`), not only trade-3

---

## FILE MAP (high-signal)

```
Q-ALPHA/
├── dashboard.py / dashboard_shared.py / start_dashboard.ps1
├── GLOSSARY.md
├── Q_ALPHA_MASTER_CONTEXT.md · Q_ALPHA_HANDOFF.md
├── candidates/
│   ├── autonomous_agent.py      ← agent 9:20
│   ├── full_market_scan.py · ticker_profiler.py · supabase_sync.py
│   ├── scheduler.py (Modal) · ibkr_connector.py · …
│   └── state_paths.py           ← is_trading_day (US/Eastern)
└── strategy_lab/
    ├── start_lab_scheduled.ps1  ← Task Scheduler launcher
    ├── live_forward.py · reset_forward.py · lab_state_sync.py · oos_r2.py
    ├── strategy_a.py · strategy_b.py · entry_models.py · …
    ├── logs/lab_YYYY-MM-DD.log
    └── results/
        ├── forward_state.json · forward_predictions.json
        ├── forward_state_replay.json
        └── oos_r2_backtest.json   ← keep
```

---

## KEY LAB DECISIONS (ops)

- Entry = **`immediate`**. `sweep_reclaim` = quality tag only.  
- Exit comparison = A Trailing vs B Target, dual $3k pools, ~1% risk.  
- **Aaron capacity (each pool independently):**
  - `MAX_NEW_ENTRIES_PER_DAY = 3` — candidate list capped to top 3 in **existing scan order** (live scan already `rank_score` / `quality_score` desc; do not re-sort). Same cap in replay/dry-run.
  - `MAX_FULL_SLOTS = 10` — a “full” open still has residual **T1, T2, or T3** working. **T4-only runners do not count.** Settle writes `residual_tranche_ids` / `counts_as_full_slot` on still-open positions; opens without residuals yet count as full until the first settle (never treat all opens as full forever after residuals exist).
- LIVE **resumes/compounds**; only `reset_forward.py` zeros to $3000.  
- Forward R² UI gated at **N≥20**; forward bar is **R² ≥ 0** (beat the mean); backtest −0.24 is context only.  
- AT1 regression (**Option A**): tip scan-merge order is authoritative — see `strategy_lab/AT1_BASELINE.md`.  
- AI entry engine **deferred** (need Level 2).

---

## KNOWN DEBT (do not implement from this list without a dedicated spec)

- **IB reject / fill-truth (JEM-class):** DONE 2026-08-26 — book OPEN only on parent **FILLED**; rejects → `NEVER_FILLED` (not OPEN); `reconcile_unfilled_opens` frees ghost opens; dashboard shows **Never filled**. JEM 2026-08-25 ledger corrected (TWS had no position); SEDG 2026-08-26 kept (TWS 8 sh).
- **Live filled-flat→CLOSED mid-day:** DONE 2026-08-27 — local `tws_intraday_sync.py` (clientId 96, schtasks `QAlpha Live TWS Sync`) books CLOSED when TWS POS=0 after a confirmed fill; Modal Polygon marks never close / never re-OPEN.
- **ENTRY-CONVENTION LIMITATION:** SIM entries fill at the 09:30 1-min close, which on the 15-min delayed tier is not visible until ~09:45 — fills are therefore not executable at that price in real money. Real-money transition requires the real-time tier OR re-basing entry fills to the first executable visible bar. Deferred: latency-cost study quantifying the gap.
- **Latency-cost study:** for historical flagged gappers, recompute entries with fills at (i) 09:30 close vs (ii) first bar visible under 15-min delay; report per-trade and aggregate P&L difference → informs $199 Stocks Advanced upgrade decision.
- **Monitor marks:** with Snapshot, all open-position marks cost ONE call; marks will be ~15 min stale (acceptable — settle is source of truth).
- **Split hygiene:** use Corporate Actions endpoint to adjust cached bars when a flagged ticker splits mid-hold.
- **Second Aggregates:** available for future fill/slippage modeling at the open.
- Cloud secrets still include service key for most tabs (Lab tab uses anon+RLS).

---

## RECENT FIXES (dashboard / calendar — already on main)

- R:R display: show value; **n/a** for insufficient profiles (not a fake warning).  
- Live Status regime banner: dropped SPY price / SMA50; keep VIX + sizing.  
- Next-scan countdown: **9:20 ET**, skips Sat/Sun → next Monday.  
- `is_trading_day`: uses **US/Eastern** date (not machine local) — verified via Sunday market-closed Telegram.  
- Profiles: Cloud empty before was local-only JSON; now Supabase `ticker_profiles` (run SQL once). Refresh still on-demand when Polygon key present.

---

## IBKR / DATA CAVEATS

```
TWS paper port: 7497
Account (paper): DUR857496
Client IDs: ibkr_connector=1 · autonomous_agent=5 · Live TWS sync=96 · spike/scan=97 · MD probes=98–99
```

**Paper market data — verified 2026-08-24 ~19:34 EDT** (read-only probe, SPY, clientId 98):
- **PASS:** `reqMktData` streaming (valid bid/ask/last), `reqHistoricalData` 1-min, `reqRealTimeBars` 5s, `reqMktDepth` 5 levels.
- **Error 420:** not observed.
- Informational OK: **2104**, **2106**, **2158**; transient **2108** is normal.
- **Depth / L2:** smart depth returned 5 levels via the **IEX** path; IB **2152** notes additional permissions needed for depth on NASDAQ / BATS / ARCA / NYSE / BEX — L2 is **usable but may be partial** vs full TotalView on paper. Re-check if full NASDAQ L2 is required.
- **Likely cause:** live North America equity streaming + L2 / Snapshot entitlements **shared to paper** (“share market data with paper”).
- **Prior state (historical):** paper live + delayed + realtime bars failed with **Error 420**; historical requests always worked.

**Implications:** Agent **MAY** use IBKR live/streaming bars again when we choose. Strategy Lab stays on **Polygon** (15-min delayed OK). **Do not mix** Lab SIM books with the IBKR agent book.

Paper L2 gate 2026-08-24: AAPL/TSLA/SPY smart depth = PARTIAL_IEX_SMART (2152 missing NASDAQ/BATS/ARCA/NYSE depth); agent clientId 5 realtime bars OK — AI entry research unblocked at partial-L2 fidelity only.

---

## QUICK COMMANDS

```powershell
# Strategy Lab live
.\venv\Scripts\python.exe strategy_lab\live_forward.py

# Replay (safe)
.\venv\Scripts\python.exe strategy_lab\live_forward.py --replay 2026-08-21

# Reset live book to $3000 (ONLY before live history matters)
.\venv\Scripts\python.exe strategy_lab\reset_forward.py

# Agent (TWS open)
.\venv\Scripts\python.exe candidates\autonomous_agent.py

# Live TWS sync (marks + filled-flat→CLOSED; TWS open)
.\venv\Scripts\python.exe candidates\tws_intraday_sync.py --repair

# Dashboard detached
.\start_dashboard.ps1

# Task checks
schtasks /Query /TN "QAlpha Strategy Lab" /V /FO LIST
schtasks /Query /TN "QAlpha Autonomous Agent" /V /FO LIST
schtasks /Query /TN "QAlpha Live TWS Sync" /FO LIST
schtasks /Query /TN "QAlpha Strategy Lab Settle" /FO LIST
# Re-register Lab Entry + Mark + Settle (no 16:40 backup):
.\strategy_lab\register_lab_tasks.ps1
schtasks /Query /TN "QAlpha Readonly Mirror Sync" /FO LIST

# Read-only mirror for Cursor Chat A (reference); Chat B edits Q-ALPHA
.\tools\sync_readonly_mirror.ps1
# Open sibling folder: Documents\Q-ALPHA-READONLY
```

---

## NEW CHAT STARTER (paste)

```
I am Aaron Klepp building Q-ALPHA.

Read first (in order):
1) Q_ALPHA_HANDOFF.md — operational state, Monday Lab schedule, Telegram, reset rules
2) Q_ALPHA_MASTER_CONTEXT.md — durable architecture, Strategy Lab decisions, debt
3) GLOSSARY.md — if terms are unfamiliar

Today’s focus: [describe]

Do not mix Strategy Lab SIM state with the IBKR agent book.
Do not run reset_forward.py after live forward trades have started unless I explicitly ask.
```

---

## PHILOSOPHY (continuity)

1. Learn from **our** data — not retail Twitter lore.  
2. Autonomy: Aaron opens TWS; systems do the rest.  
3. Paper / SIM first; real money only after proof.  
4. Diagnose root causes before patching.  
5. Report fails as loudly as wins (esp. R² / WF / Sharpe).  

---

*Handoff updated: 2026-08-24 · IBKR paper MD probe verified; Lab/agent books remain separate*
