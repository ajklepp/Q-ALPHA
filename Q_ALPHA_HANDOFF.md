# Q-ALPHA SYSTEM HANDOFF
## Operational state — hand this to a fresh assistant with zero prior context

**Version focus:** Strategy Lab live forward + agent paper path  
**Calendar note:** First Strategy Lab **live** session target = **Monday 2026-08-24**  
**Companion architecture doc:** `Q_ALPHA_MASTER_CONTEXT.md`  
**Glossary:** `GLOSSARY.md` (also **📖 Glossary** dashboard tab)  
**Last updated:** 2026-08-24

---

## WHO / WHAT

Aaron Klepp — Ontario. Building Q-Alpha: autonomous momentum system + **Strategy Lab** exit research.  
Starting SIM capital per lab pool: **$3,000** (compounds). Broker: IBKR Canada paper (agent); Polygon for lab.

**Dashboard:** https://q-alpha-lshnrvza2radqpkjrkf52m.streamlit.app  
**Repo:** `ajklepp/Q-ALPHA` · branch `main` · entry `dashboard.py`

---

## TWO SYSTEMS (do not mix state)

| | Live agent | Strategy Lab |
|--|------------|--------------|
| Code | `candidates/autonomous_agent.py` | `strategy_lab/live_forward.py` |
| Money | IBKR paper + `candidates/pool_state.json` | SIM dual pools in `forward_state.json` / Supabase |
| Schedule | Task **QAlpha Autonomous Agent** · **9:20 ET** | Task **QAlpha Strategy Lab** · **9:35 ET** weekdays |
| Telegram | Q-ALPHA agent messages | Prefixed **🧪 Strategy Lab** |
| Data | Needs TWS; **IBKR paper MD usable** (probe 2026-08-24 — see IBKR section) | Polygon 1-min (15-min delayed OK) |

---

## CURRENT LAB STATE (as of handoff prep, evening 2026-08-23)

```
status:              awaiting_first_live_run
pools:               A = $3000.00 · B = $3000.00
closed trades:       0 / 0
forward OOS R² N:    0  (dashboard shows "collecting data" until N≥20)
predictions file:    empty
backtest OOS R²:     preserved in results/oos_r2_backtest.json  (~ −0.2367, N=35)
next scheduled run:  Monday 2026-08-24 9:35 AM ET
```

After Monday’s first live trades, pools **compound** — do **not** call `reset_forward.py`.

---

## MONDAY — HOW TO RUN STRATEGY LAB

### A) Automatic (preferred)
Windows Task Scheduler task **`QAlpha Strategy Lab`**:
- Trigger: **Mon–Fri 09:35** local (= **9:35 ET** on this PC)
- Action: `powershell.exe … -File "…\strategy_lab\start_lab_scheduled.ps1"`
- Launcher: **`venv\Scripts\python.exe strategy_lab\live_forward.py`** (ENTRY only — opens positions, no phantom same-morning settle)

**Settle task** (register similarly): **`QAlpha Strategy Lab Settle`** · **Mon–Fri ~16:20 ET** ·
`start_lab_settle_scheduled.ps1` → `live_forward.py --settle`  
**No 16:40 backup** — duplicate settle Telegram (removed Aug 2026). Register all three tasks: **`.\strategy_lab\register_lab_tasks.ps1`**

**Intraday marks:** **`QAlpha Strategy Lab Mark`** · **Mon–Fri every 30m 10:00–16:00 ET** ·
`start_lab_mark_scheduled.ps1` → `live_forward.py --mark` (quiet; force-upserts Supabase).

Full cadence + schtasks examples: **`strategy_lab/DASHBOARD_FRESHNESS.md`**.  
Register all Lab tasks (no 16:40 backup): **`.\strategy_lab\register_lab_tasks.ps1`**

**Unattended needs:** PC on, awake, **logged in as ajkle**, network, `.env` present.

Verify task:
```powershell
schtasks /Query /TN "QAlpha Strategy Lab" /V /FO LIST
```

### B) Manual live
```powershell
cd C:\Users\ajkle\OneDrive\Documents\Q-ALPHA
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

## AGENT DAILY SCHEDULE (unchanged spine)

```
9:15     Open TWS paper (port 7497) — still required for agent orders
9:20     Task "QAlpha Autonomous Agent" → autonomous_agent.py
9:20–9:29 Scan / profiles
9:29     Premarket Telegram (agent)
9:30–11:00 Entries (agent)
9:35     Strategy Lab live_forward (parallel SIM — no IBKR)
10:00–16:00  Live TWS sync every 30m (LOCAL — marks + filled-flat→CLOSED)
10:00–16:00  Strategy Lab --mark every 30m (Polygon marks → Supabase SIM)
11:00    Agent session recap Telegram
~4:15    Modal EOD monitor (agent)
~4:20    Strategy Lab --settle (primary EOD at 16:20 only)
Every 30m Modal intraday monitor (agent) — Polygon FALLBACK marks only;
         does NOT close; must never re-OPEN CLOSED / NEVER_FILLED
```

**Live marks/closes SoT:** local `candidates/tws_intraday_sync.py` (TWS clientId **96**).
Modal cannot reach `127.0.0.1:7497`. Agents **cannot** create Windows tasks — Aaron runs once:

```powershell
schtasks /Create /F /TN "QAlpha Live TWS Sync" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\candidates\start_tws_intraday_scheduled.ps1`"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /RI 30 /DU 06:00
```

Verify: `schtasks /Query /TN "QAlpha Live TWS Sync" /FO LIST`  
Manual repair (TWS open): `.\venv\Scripts\python.exe candidates\tws_intraday_sync.py --repair`  
`--repair` re-reads today's CLOSED IBKR_PAPER sells; TWS fill px/reason wins over stop_price; pool rebuilt (no double-close).

### Agent morning list (Phase 2 — TWS pipeline)

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
📊 Live Status      — agent pool, watchlist, regime (VIX/sizing; SPY$/SMA50 removed from banner)
📋 Trade Log
📈 Performance
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
| Windows Task Scheduler | Agent 9:20 + Lab 9:35 |

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
