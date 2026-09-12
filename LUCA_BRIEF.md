# Q-ALPHA — LUCA conversation brief

Read-only architecture + mission map for conversation. **Does not change trading behavior.**

**Repo:** https://github.com/ajklepp/Q-ALPHA · Aaron Klepp (Ontario)  
**Canonical laptop path:** `C:\Users\ajkle\Documents\Q-ALPHA` (not OneDrive)  
**As-of:** 2026-09-12 (`main` tip `9e991de`)  
**Companion docs:** `Q_ALPHA_HANDOFF.md` (ops, **stale in places**), `Q_ALPHA_MASTER_CONTEXT.md` (research contract, **Aug 24**), `GLOSSARY.md` (terms), `candidates/tsd_scan_pipeline/DESIGN.md` (live paper authority)

**Doc freshness (important):** treat **code + DESIGN.md + this brief** as current. `Q_ALPHA_HANDOFF.md` (2026-09-04) still lists Strategy Lab dashboard tabs and `continuation_score` v1.1; live code is **v1.6**, dashboard tabs are Live Status / 3R Paper / Weekly Review. `candidates/uts_v2/PEAK_HOUR_PERFORMERS.md` still says v3.1 / v1.1.

---

## 1) Mission

Q-ALPHA is a **long-only** momentum / continuation system. It is **not** live (real-money) trading.

| World | What it is | Money | Status |
|-------|------------|-------|--------|
| **Peak Hour Performers (PRIMARY)** | Hourly 1H continuation paper on IBKR TWS | IBKR **paper** port **7497** + local `$3,000` TSD pool | **Live paper** — laptop + TWS required |
| **3R shadow paper** | Same fills, different software exits | No second broker book | Parallel bakeoff only |
| **Gap Autonomous Agent** | Morning gapper brackets | Residual `pool_state.json` | **DISABLED** / runoff only |
| **Strategy Lab** | Dual $3k SIM A/B exit test on Polygon | Fake money, no IBKR | **Mothballed** 2026-09-04 |
| **Modal experiments** | Historical Option-D / LightGBM / studies | No broker | Research only |
| **Track 100** | NDX100 Wave Cross study | n/a | **Other repo** — not Q-ALPHA |

**Capital model (Peak Hour):** one deployable paper pool starting at **$3,000** (`tsd_pool.py` `DEFAULT_STARTING_POOL`). Equity = cash (`pool`) + cost-basis deployed. **Slot-then-size:** unit `S = $300` until `N = 10` concurrent full slots, then `S = equity / 10`. Max **2 new names per hourly scan**. Share lots of **4**. T4-only runners do **not** consume a full slot. Paper size-up beyond the ladder is gated at **20 closed legs / 45% WR** (`candidates/uts_v2/paper_gate.py`) — the ladder itself still grows with equity.

**Not real money.** Paper / SIM first. IBKR **live** port is **7496** — do not use it unless Aaron explicitly asks. Long-only: BUY to open; SELL only to exit/reduce longs or cover accidental shorts.

**Philosophy:** learn from *this* book’s data; report fails as loudly as wins; no look-ahead; temporal splits only in research.

---

## 2) Main runtime components

### Live paper loop (what actually trades)

```
04:30 ET     HTF universe refresh (Polygon daily gates)
05:15–15:15  QAlpha TSD Scheduler --tick --live
             → 1H LAUNCH @ :15 (bar close + 15m Polygon delay — no front-run)
             → continuation_score v1.6 nominates
             → Attention Pool (score ∪ popularity ∪ soft-extension)
             → Case Review ENTER / WAIT / REJECT  (score alone cannot buy)
             → watch queue + 1m micro-confirm
             → pullback Limit BUY (EH) / Market BUY (RTH)  clientId 93
~60s         Trail monitor --loop --adaptive  clientId 95 (fallback 85, 75)
             → kill / BE / T1–T4 keep-profit / base-break / idle flatten
             → also polls WATCHING names for micro-confirm
~07:00 / 30m Live TWS Sync  clientId 96
             → marks, filled-flat→CLOSED, pool rebuild, Supabase mirror
Fri 17:00    Weekly scorecard + options study (offline)
```

**Authority (v3.2):** continuation score **nominates**; **case review decides**. BUY only case-ENTER, still capped at 2/scan + capacity. See `candidates/tsd_scan_pipeline/DESIGN.md`.

| Process | File | Role |
|---------|------|------|
| Scheduler | `candidates/tsd_scan_pipeline/scheduler.py` | Sole **entry** clock; `--tick --live` |
| 1H scan | `candidates/tsd_scan_pipeline/tsd_1h_launch_scan.py` | HTF → rank → case → queue → enter |
| Ranker | `candidates/tsd_scan_pipeline/tsd_launch_score.py` | `CONTINUATION_SCORE_VERSION = "v1.6"` |
| Case | `candidates/tsd_scan_pipeline/tsd_case_review.py` | ENTER / WAIT / REJECT + LLM fusion (live only) |
| Micro-confirm | `candidates/tsd_scan_pipeline/tsd_micro_confirm.py` | 1m tape hold / dump abort / no-chase |
| Entries | `candidates/tsd_scan_pipeline/tsd_entry.py` | Session-aware BUY + kill STP LMT |
| Trail | `candidates/tsd_scan_pipeline/tsd_trail_monitor.py` | Exits + WATCHING poll; lockfile singleton |
| Sync | `candidates/tws_intraday_sync.py` | Broker SoT → book / pool / cloud |
| Cloud write | `candidates/tsd_supabase_sync.py` | Mirror for Streamlit |
| Telegram | `candidates/tsd_scan_pipeline/tsd_notify.py` | Best-effort via `autonomous_agent.send_telegram` |

Windows tasks (Aaron’s laptop): **QAlpha TSD Scheduler**, **QAlpha TSD Trail Monitor**, **QAlpha Live TWS Sync**, **QAlpha TSD Weekly Reports**. Register: `candidates/register_tsd_tasks.ps1`.

### How Peak Hour relates to “TSD”

**TSD** (Trend / WaveTrend / money-flow / volume) is the **signal engine** inherited from a 3-hour swing track. **Peak Hour Performers** is the **product**: same engine applied to **completed 1-hour bars** (hours 05–15 ET), plus HTF universe, continuation ranker, case review, and keep-profit exits. Legacy 3H `tsd_scan_ibkr` / `--polygon` / `--tws` without redirect are **research**, not the live trigger.

**UTS v2** (`candidates/uts_v2/`) is the operating-system lineage (capacity, reset, weekly funnel). Product card: `candidates/uts_v2/PEAK_HOUR_PERFORMERS.md`.

### Disabled / archive (keep code, do not re-enable)

| Track | Code | Why it stays off |
|-------|------|------------------|
| Gap agent | `candidates/autonomous_agent.py` | Morning gapper; runoff only |
| Approval runner | `candidates/local_approval_runner.py` | Human-approve path for gap agent |
| Setup Watch | `candidates/setup_watch_agent.py` | Second entry bot — disabled |
| TWS morning scan | `candidates/tws_scan_pipeline/` | Gap-agent shortlist (~50×3 → watch 10 / trade 3) |
| Strategy Lab | `strategy_lab/live_forward.py` | SIM A trail vs B target; no IBKR |
| Modal gap monitors | `candidates/scheduler.py` (`qalpha-scheduler`) | Intraday/EOD for **gap** book; skips `IBKR_PAPER` marks |

### Dashboard (display only)

Streamlit Cloud: entry `dashboard.py`. Tabs in code: **Live Status · 3R Paper · Trade Log · Performance · System Health · Daily Reviews · Weekly Review · Glossary**. Live Status is TSD-primary (Supabase `tsd_*` first; gap runoff secondary). **3R Paper** reads the shadow book, not IBKR exits.

---

## 3) Data sources and source of truth

| Source | Used for | SoT? |
|--------|----------|------|
| **IBKR / TWS paper 7497** | Orders, fills, positions, kill STP LMT, some scanners / news / last | **Broker SoT** for fills & open qty |
| **Local TSD state** | Strategy logic, tranches, queue, pool cash | **Strategy SoT** (`tsd_book_state.json`, `tsd_pool_state.json`, `tsd_watch_queue.json`) |
| **Polygon** (`POLYGON_API_KEY`) | HTF universe, 1H bars, profiles, news, Lab/experiments; **15-min delayed** ($79 Developer) | **Scan / research SoT** — not fills |
| **Supabase** | Dashboard mirror: `tsd_positions`, `tsd_pool_snapshots`, `tsd_watchlist`, `tsd_watch_queue`, `tsd_closed_legs`, `tsd_missed_moves`, `ticker_profiles`, `strategy_lab_state` | **Display SoT** — never broker truth |
| **Telegram** `@MyQalphaBot` | Alerts only (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) | Not a book |
| **OpenRouter** | Case-review PRINT/OUTLOOK (`gpt-4o-mini`); catalyst copy | Soft evidence; can REJECT on contradictions |
| **StockTwits** | Optional social in `tsd_social.py` | Soft rank only; X is off |
| **Modal** | Heavy experiments + leftover gap EOD | Cannot reach `127.0.0.1:7497` |
| **GitHub `main`** | Source code SoT | Runtime state is **not** in git |

**When screens disagree:** TWS position/fill → then local book → then Supabase. Dashboard “local fallback” means the laptop is ahead of cloud, not that the broker changed.

**Polygon delay is load-bearing:** 1H scan waits until **:15** so the completed hour is in the API. SIM Lab entries at 09:30 1-min close are not executable on the delayed tier until ~09:45 — a known real-money blocker.

Schema: `candidates/sql/tsd_cloud.sql`. Sync: `candidates/tsd_supabase_sync.py` (via TWS sync). Env names are locked in `.cursorrules` (`POLYGON_API_KEY`, `SUPABASE_*`, etc.). Never commit `.env` or live `*_state.json`.

### TWS client IDs (do not collide)

| ID | Owner |
|----|--------|
| 1 | `ibkr_connector.py` (legacy brackets) |
| 5 | Gap `autonomous_agent.py` (disabled) |
| **71** | **options_bridge** (PR #2 — BSF read-only; not on `main` yet) |
| 85 / 75 | Trail fallbacks |
| 88 / 89 | News probes |
| **93** | 1H launch / TSD scan |
| 94 | Setup Watch / IBKR probe (disabled / research) |
| **95** | Trail monitor (primary) |
| **96** | Live TWS sync |
| 97 | Spike / flatten / TWS scan-only |
| 98–99 | MD / popularity probes |
| 3910–3919, 39100–39199 | Reserved for Best Strategy Finder’s own clients |

Paper account historically documented as **DUR857496**. Live port **7496** is out of scope.

---

## 4) Orders, risk, exits (high level)

**Long-only.** See `.cursor/rules/long-only-no-shorting.mdc`.

### Entry path

1. Daily HTF hard gates (`tsd_htf_gates.py`): 20d range **25–150%**, close > SMA50, SMA20 rising, price ≥ $5. Upstream liquid universe: mcap ≥ **$300M**, 20d dollar vol ≥ **$5M** (`universe_tsd.py`).
2. Completed 1H bar in hours **05–15**: `buy_signal` OR `early_bull`, not `extension_hard` (scan ≥ 75 or `extended` bar).
3. `continuation_score` v1.6 ranks (same-day extension objective — not a probability). Soft: early-session bonus, RS, gap penalty, bar color. Hard: instrument safety, HTF, severe extension, dedupe, capacity.
4. Attention Pool (~12) + **case review**. Wreckage / toxic / thin↔deep contradictions → REJECT. Score cannot buy.
5. Queue: RANKED → TAKE (top 2 **new** risk) → WATCHING → micro-confirm → ENTERED on fill.
6. Micro-confirm: abort dump through −1.5% / structure; wait pullback if >+1.5%; skip dead tape (RVOL); EH wait ~20m; TWS last may hold-confirm. Confirmed buys: **pullback Limit** near signal (EH `outsideRth`); **Market** in RTH.
7. Cross-book dedupe: no BUY if symbol is already open in TSD or leftover gap book.
8. Add-ons: max **3** entries per ticker over a trend (1 + 2 add-ons); each fill is its own trailed **leg**.

**Autopsy P0 (shipped `5eef568`):** `already_confirmed` / UNCHANGED do **not** consume the 2/hour NEW cap. `no_fill_timeout` requotes once (+10 bps, 30s) then **promotes the next CASE ENTER**.

### Risk / sizing

- Per-entry notional = `min(S, cash / remaining slots)` — never half the pool.
- Shares = largest multiple of 4 that fits.
- Broker **kill** = GTC StopLimit SELL on remaining shares. Kill % = profile MAE p75 only if in **[2%, 6%]**, else **5%**. Hybrid: if structure risk is **1–2.5%**, stop just under structure; **never** place the primary kill at a wide area-low. Soft-skip entry if structure risk **> 3.5%**.
- After T1 banks, shared kill can tighten (keep-profit / BE path).
- Overnight 20:00–04:00 ET: do not escalate software exits; GTC kills may rest.

### Three-layer exits (live 4T keep-profit)

| Layer | What | Code |
|-------|------|------|
| L1 Kill | Broker STP LMT, always on while shares remain | `tsd_kill.py`, `tsd_exit.py` |
| L2 Structure / BE | **KILL ONLY until +1R**; then BE lock ≈ entry × 0.997 | `tsd_structure.py` |
| L3 Trail | T1 **hard-banks at +2%** (no T1 trail); T2–T4 trail at 3.5 / 6 / 10% with 2.5% trail | `tsd_keep_profit.py`, `tsd_trail.py` (adapts Lab `strategy_a`) |

Other exits: **3H base-break** (`tsd_base_break.py`; day-1 waits for +1R/T1 after autopsy), **idle flatten day ≥ 6** if never +1R and not trailing, **max hold ~20** trading days, **stuck-kill escalate** (price through STP LMT → aggressive limit / flatten; skip dead overnight), **manual** (Aaron), **broker_flat** reconcile when TWS POS=0 after a confirmed fill.

**Shadow 3R** (`tsd_shadow_multi_target.py`): same fills; software banks 0.35 / 0.50 / 0.90R at 50/25/25 + 5% kill. **No second broker SELLs.** Dashboard tab **3R Paper**.

**Sacred research bracket** (`BracketPosition` in EXP-0012) is **not** the live Peak Hour book. Do not rewrite EXP-0012. Lab Strategy A is the trail math Peak Hour adapted.

---

## 5) Folder map

```
Q-ALPHA/
├── LUCA_BRIEF.md                 ← this file
├── Q_ALPHA_HANDOFF.md            ← ops runbook (partially stale)
├── Q_ALPHA_MASTER_CONTEXT.md     ← research contract / Lab architecture
├── GLOSSARY.md                   ← terms (also dashboard Glossary tab)
├── TRACK_100_MOVED.md            ← Track 100 is another repo
├── .cursorrules                  ← agent law (look-ahead, Option D, env names)
├── .cursor/rules/                ← long-only, Modal-always, autonomy, auto-push
├── dashboard.py                  ← Streamlit entry
├── dashboard_live_status.py      ← Live Status helpers
├── dashboard_3r_paper.py         ← shadow 3R tab
├── dashboard_weekly_research.py  ← Weekly Review / PHP funnel
├── data_pipeline.py / features.py / model.py  ← root research stubs; ask before editing
│
├── candidates/                   ← LIVE PAPER + disabled agent (do not casually edit)
│   ├── tsd_scan_pipeline/        ← Peak Hour / TSD (PRIMARY)
│   │   ├── DESIGN.md
│   │   ├── scheduler.py · tsd_1h_launch_scan.py · tsd_trail_monitor.py
│   │   ├── tsd_case_review.py · tsd_keep_profit.py · tsd_exit.py
│   │   └── results/
│   │       ├── LEARNING_AUTOPSY_20260911.md
│   │       ├── last_1h_launch.json          ← launch-board SoT (gitignored scans)
│   │       ├── peak_hour_scans/             ← php_scan_*.json (gitignored)
│   │       ├── scorecard_*.md · options_study_*.md
│   │       └── results.md                   ← pipeline phase log
│   ├── uts_v2/                   ← product card, reset, weekly funnel
│   ├── tws_scan_pipeline/        ← gap-agent TWS scanners (disabled path)
│   ├── sql/                      ← Supabase DDL
│   ├── autonomous_agent.py       ← gap agent (DISABLED)
│   ├── tws_intraday_sync.py · tsd_supabase_sync.py
│   ├── ibkr_connector.py         ← legacy brackets; not the Peak Hour path
│   ├── scheduler.py              ← Modal qalpha-scheduler (gap monitors)
│   ├── tsd_book_state.json · tsd_pool_state.json   ← LIVE; gitignored
│   └── options_bridge/           ← NOT on main; see PR #2
│
├── strategy_lab/                 ← mothballed SIM A/B (Polygon only)
│   ├── live_forward.py · strategy_a.py · strategy_b.py
│   └── results/                  ← do not commit unless asked
│
├── experiments/                  ← Modal research EXP-0002 … EXP-0025
│   ├── EXP-0012/                 ← working baseline — NEVER TOUCH
│   ├── EXP-0021/                 ← continuation ranker (shipped into Peak Hour)
│   └── PHASE_2_SUMMARY.md
│
├── profiles/                     ← sample analog JSON (cloud uses Supabase)
├── ideas/                        ← IDEA-0001
├── cloud/                        ← Modal universe scanner / test
├── tools/                        ← Q-ALPHA-READONLY mirror for Chat A
└── tests/                        ← Peak Hour / dashboard unit tests
```

**Do not commit:** `.env`, `tsd_book_state.json`, `tsd_pool_state.json`, `tsd_watch_queue.json`, `pool_state.json`, `strategy_lab/results/*`, generated `peak_hour_scans/`.

**Reset Peak Hour (destructive):** `candidates/uts_v2/reset_peak_hour_performers.py --also-reset-cloud`. Archives under `candidates/archive/`. Only if Aaron wants a clean slate.

---

## 6) Recent learning / known issues

Canonical write-up: `candidates/tsd_scan_pipeline/results/LEARNING_AUTOPSY_20260911.md` (week ending 2026-09-11).

**Week facts:** 44 hourly scans → 684 launches → **16** book entries (2.34%). Friday **entered=0** all 11 hours. Closed PHP table **−$139.74** (2W / 13L). Exit mix: **`base_break_down` 8/15**. Survivors ATRC/NX showed intended T1 banks. Extra **SMR** manual −$10.36 not in the entered-16 list.

**P0 (must-fix then; now shipped on `main` in `5eef568`):**

1. **`already_confirmed` burned the 2/hour cap** (STX, MMED) — cap now counts **NEW** risk only.
2. **`no_fill_timeout` zeroed the hour** (NUAI) — one requote, then promote next CASE ENTER.
3. **Single trail-monitor owner** — lockfile + treat venv+pythoncore as one tree; never kill the child. Primary client **95**.

**P1 still worth remembering (partially addressed in same commit):**

- CASE_REJECT vs continuation rank disagreement (hour-15 SMR REJECT while lower MMED ENTER-unchanged) — now alerts; constructive leaders can soft REJECT→WAIT.
- Day-1 `base_break_down` harvesting losers — day-1 base-break now waits for +1R/T1.
- Mark drift `tsd_mark_mismatch` — soft-warn, not a trading signal.
- Scorecard “0 scans” vs PHP 44 — scorecard now reads PHP scans.
- Options overlay study was empty that week (`--skip-options` / no rows).

**Earlier shipped lessons (do not re-litigate without new data):**

| Lesson | Commit / file |
|--------|----------------|
| T1 bank at +2% (not a 4% trail that only frees ~+7%) | `326ab17`, `tsd_keep_profit.py` |
| Micro-confirm before BUY | `724dc4d` |
| EH wait / RVOL skip / pullback limits | `9561ab9` |
| False full exit → broker orphan (JANX) | `4752bd7` |
| Stuck STP LMT escalate; skip 20:00–04:00 | `2789045` |
| Track 100 is **out of this repo** | `TRACK_100_MOVED.md` |

**Open debt (do not implement from this list without a spec):** Streamlit Cloud still uses **service role** on a public app (Lab tab was the exception); Polygon 15-min delay vs real-money fills; paper L2 is **partial IEX**; Modal cron EDT vs EST; `use_container_width` warnings.

**JANX residual flatten** is a **separate open draft PR #1** (`cursor/janx-residual-exit-flatten-630b`) — fail-closed flatten when kill STP LMT gaps through. Not this brief.

---

## 7) options_bridge / PR #2 — BSF read-only bridge

**Not merged as of this brief.** Open PR: https://github.com/ajklepp/Q-ALPHA/pull/2  
Branch: `cursor/options-bridge-readonly-42af`  
Title: *Read-only local options bridge for BSF Phase 9A*

**Purpose:** Best Strategy Finder (a **separate** product) needs TWS paper **market data** (quotes, option chain, hist) without opening `ib_insync` or logging into IBKR itself. Q-ALPHA’s laptop already has TWS paper on 7497. The bridge is a thin **loopback HTTP JSON** process.

| | |
|--|--|
| Bind | **127.0.0.1:8787** only (never `0.0.0.0`) |
| TWS | 127.0.0.1:**7497**, clientId **71** |
| Auth | None — no IBKR credentials, no cloud Gateway |
| Orders | **Forbidden** — `/order`, `/placeOrder`, cancel/modify → 403/405; `placeOrder` blocked in the IB proxy |
| Imports | Does **not** import Peak Hour / TSD / book state |

Paths: `/v1/health`, `/v1/underlying/quote`, `/v1/options/chain`, POST `/v1/options/qualify`, `/v1/options/quote(s)`, `/v1/options/hist`, POST `/v1/phase9a/put_credit_snapshot`.

Layout on that branch: `candidates/options_bridge/` (`server.py`, `ib_session.py`, `config.py`) + `candidates/start_options_bridge.ps1` / `stop_options_bridge.ps1`. Tests: `tests/test_options_bridge.py` (FakeSession only — **do not** `ib.connect` from CI or a cloud VM).

**Q-ALPHA vs BSF:** this repo **hosts** the laptop data socket. BSF consumes it. The bridge must not place Peak Hour orders or share TSD client IDs (71 is reserved; 93/95/96 stay Peak Hour).

---

## 8) What Q-ALPHA owns vs stay out of scope

### Q-ALPHA owns (this repo)

- **Live paper Peak Hour / TSD** on Aaron’s laptop + TWS 7497.
- Local book/pool/queue, trail monitor, scheduler, TWS sync, Telegram, Supabase mirror, Streamlit dashboard.
- **Research** that feeds Peak Hour: EXP-0021 continuation, HTF hitch studies, keep-profit / 3R bakeoffs, weekly funnel, missed-move ledger.
- **Mothballed** Strategy Lab + disabled gap agent (keep for runoff / replay; do not mix books).
- **Modal** experiment runners (`experiments/EXP-*`) for historical / Polygon work.
- **Read-only options_bridge** (PR #2) as a laptop data service for BSF — not a trading strategy.

### Stay out of Q-ALPHA (unless Aaron explicitly expands scope)

| Out of scope | Where it lives / why |
|--------------|----------------------|
| **Track 100** / NDX Wave Cross | https://github.com/ajklepp/track-100 — `TRACK_100_MOVED.md` |
| **Best Strategy Finder** trading / options strategies | Separate product; Q-ALPHA only offers the read-only data bridge |
| **Real-money / port 7496** | Irreversible; ask first |
| **Intentional shorting** | Forbidden |
| **Rewriting EXP-0012** / sacred `BracketPosition` | Baseline lock |
| **Editing `/candidates` live paths for Lab/research** | Need explicit approval (`.cursorrules` §10) |
| **Re-enabling** gap agent, Approval Runner, Setup Watch, Lab tasks | Disabled on purpose |
| **Mixing books** | Lab SIM ≠ TSD paper ≠ gap runoff ≠ 3R shadow |
| **AI “smart entry v2” on delayed Polygon only** | Deferred until real L2 / order-flow (partial paper L2 only) |
| **Committing live state / secrets** | `.env`, `tsd_*_state.json`, scan JSON |
| **Cloud agents placing IBKR orders** | Modal/cloud cannot reach TWS; do not pretend |

### Research vs live (how to talk about it)

- **Live** = Peak Hour 1H @ :15 + trail + TWS sync. Heuristic ranker + case review — **not** LightGBM.
- **Shadow** = 3R ladder on the same fills; software only.
- **Research** = `experiments/`, `strategy_lab/`, PHP walk-forwards (`php_wf_*`, `php_day_replay.py`). Option D / Sharpe ≥ 1.5 gates apply to **new Modal experiments**, not to the live continuation score.
- **EXP-0021** shipped `continuation_score` into live (now v1.6). EXP-0023 HMM regime is **dashboard context only** (does not size or gate). EXP-0024/0025 diagnostics did **not** ship into v1.4+.
- Heavy Polygon/OpenRouter jobs → **Modal**. Anything needing TWS → **laptop only**.

---

## Conversation starter for LUCA

You are talking about Aaron’s **Q-ALPHA** stack: Peak Hour Performers **IBKR paper** ($3k, long-only, 2 slots/hour, case review, keep-profit 4T). You are **not** the live trail process. Do not connect to TWS, place orders, or use secrets to trade. If a question is about Track 100 or BSF strategy logic, it is out of this repo (BSF may only use the read-only options bridge). Prefer `DESIGN.md` + this brief over the September-4 handoff when they disagree.

---

*Informational only. Peak Hour is IBKR paper; Strategy Lab is archived SIM; neither is real money.*
