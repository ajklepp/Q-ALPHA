# Q-ALPHA SYSTEM HANDOFF
## Operational state — hand this to a fresh assistant with zero prior context

**Version focus:** Strategy Lab live forward + agent paper path  
**Calendar note:** First Strategy Lab **live** session target = **Monday 2026-08-24**  
**Companion architecture doc:** `Q_ALPHA_MASTER_CONTEXT.md`  
**Glossary:** `GLOSSARY.md` (also **📖 Glossary** dashboard tab)

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
| Data | Needs TWS; IBKR **live MD broken** (Error 420) | Polygon 1-min (delayed OK) |

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

**Settle task** (register similarly): **`QAlpha Strategy Lab Settle`** · **Mon–Fri 16:40 ET** ·
`start_lab_settle_scheduled.ps1` → `live_forward.py --settle`  
(Morning entry also auto-settles any overnight opens first.)

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
| EOD | `🧪 EOD: Pool A $… (…%), Pool B $… (…%), trades today: n, winner: A/B/tie. Forward R² N=…` |
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
11:00    Agent session recap Telegram
~4:15    Modal EOD monitor
Every 30m Modal intraday monitor
```

---

## DASHBOARD TABS

```
📊 Live Status      — agent pool, watchlist, regime (VIX/sizing; SPY$/SMA50 removed from banner)
📋 Trade Log
📈 Performance
🔧 System Health
📓 Daily Reviews
🔬 Ticker Profiles  — precomputed JSON; Refresh gated if no POLYGON on Cloud
🧪 Strategy Lab     — SIM A vs B; OOS R² panel (backtest + forward MIN_N=20); Supabase anon-first
📖 Glossary         — renders GLOSSARY.md
```

Local: `.\start_dashboard.ps1` (detached). Cloud auto-deploys on push to `main`; reboot if cache stale.

---

## INFRASTRUCTURE

| Piece | Role |
|-------|------|
| GitHub `ajklepp/Q-ALPHA` | Source of truth |
| Streamlit Cloud | Public dashboard |
| Supabase | Agent tables + `strategy_lab_state` |
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
- Exit comparison = A Trailing vs B Target, dual $3k pools, max 10 slots each, ~1% risk.  
- LIVE **resumes/compounds**; only `reset_forward.py` zeros to $3000.  
- Forward R² UI gated at **N≥20**; forward bar is **R² ≥ 0** (beat the mean); backtest −0.24 is context only.  
- AT1 regression (**Option A**): tip scan-merge order is authoritative — see `strategy_lab/AT1_BASELINE.md`.  
- AI entry engine **deferred** (need Level 2).

---

## KNOWN DEBT (do not implement from this list without a dedicated spec)

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
- Profiles: Cloud hides Refresh when no Polygon key; lookback / analog-day fixes earlier.

---

## IBKR / DATA CAVEATS

```
TWS paper port: 7497
Client IDs: ibkr_connector=1 · autonomous_agent=5 (keep separate)
IBKR paper MD: Error 420 — live/delayed/realtime bars fail; historical OK
Polygon: use for Strategy Lab and research aggregates
```

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

# Dashboard detached
.\start_dashboard.ps1

# Task checks
schtasks /Query /TN "QAlpha Strategy Lab" /V /FO LIST
schtasks /Query /TN "QAlpha Autonomous Agent" /V /FO LIST
schtasks /Query /TN "QAlpha Strategy Lab Settle" /FO LIST
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

*Handoff updated: 2026-08-23 · First Lab live target: 2026-08-24 09:35 ET*
