# Q-ALPHA SYSTEM HANDOFF
## Complete State Document — v1.0.0
### Day 1: 2026-08-18

---

## WHO YOU ARE

Aaron Klepp — experienced day trader, Ontario Canada.
Drives 12-16 hours/day. Cannot trade manually.
Building a fully autonomous AI momentum trading system.
Starting capital: $3,000 USD, compounding.
Broker: Interactive Brokers Canada (paper trading now, live soon).

---

## WHAT Q-ALPHA IS

A fully autonomous momentum trading system that:
- Scans NYSE + NASDAQ every morning via IBKR live data
- Identifies stocks gapping 3%+ with 2x+ volume
- Watches them at market open via real-time price action
- Enters the best setups autonomously (no human approval)
- Manages bracket stops automatically via IBKR
- Reports everything via Telegram to Aaron's phone
- Shows live dashboard at: https://q-alpha-lshnrvza2radqpkjrkf52m.streamlit.app

---

## INFRASTRUCTURE STACK

| Component | Purpose | Cost |
|-----------|---------|------|
| GitHub: ajklepp/Q-ALPHA | Code repository (public) | Free |
| Modal cloud | EOD monitor + intraday monitor scheduling | ~$2/month |
| IBKR TWS Paper | Order execution + live data | Free |
| Supabase | Database (trades, scans, health, reviews) | Free |
| Streamlit Cloud | Always-on dashboard | Free |
| Polygon.io $79/mo | Historical data + news backup | $79/month |
| OpenRouter (Bilbo key) | AI catalyst summarization via Claude | Pay per use |
| Telegram @MyQalphaBot | Real-time alerts to phone | Free |
| Windows Task Scheduler | Runs autonomous_agent.py at 9:20 AM | Free |

---

## DAILY SCHEDULE

```
9:15 AM  Aaron opens TWS (only manual action required)
9:20 AM  Task Scheduler runs autonomous_agent.py locally
9:20-9:29 IBKR live scan of universe (~300 tickers)
9:29 AM  Telegram: "Watching X candidates at open"
9:30 AM  Market opens — AI watches all candidates
9:30-11:00 AI enters best setups (max 3/day)
11:00 AM Telegram: session recap (entered/skipped)
4:15 PM  Modal EOD monitor checks brackets
4:15 PM  Telegram: P&L report
Every 30min Modal intraday monitor updates dashboard P&L
8:00 PM  Cursor Automation writes daily_review_{date}.md
```

---

## FILE STRUCTURE

```
Q-ALPHA/
├── candidates/
│   ├── autonomous_agent.py      ← MAIN: daily trading agent (9:20 AM)
│   ├── scheduler.py             ← Modal: EOD + intraday monitors
│   ├── position_monitor.py      ← EOD bracket check
│   ├── intraday_monitor.py      ← Live P&L updates every 30min
│   ├── ibkr_connector.py        ← IBKR bracket order placement
│   ├── position_sizer.py        ← Position sizing + PoolManager
│   ├── paper_trader.py          ← Paper trade logging
│   ├── supabase_sync.py         ← Database sync
│   ├── catalyst_ai.py           ← OpenRouter/Claude catalyst summary
│   ├── stock_profiler.py        ← (PHASE 2 — not built yet)
│   ├── universe.json            ← 300 ticker universe
│   ├── pool_state.json          ← Current pool value + positions
│   └── paper_trades.json        ← All trades logged
├── dashboard.py                 ← Streamlit dashboard (root level)
├── experiments/
│   ├── EXP-0012/ through EXP-0017/  ← Backtest experiments
│   ├── daily_reviews/           ← Cursor automation writes here
│   ├── improvement_log.md       ← Running list of improvements
│   └── PHASE_2_SUMMARY.md       ← Phase 2 backtest conclusions
├── .cursorrules                 ← Cursor AI rules
├── Q_ALPHA_MASTER_CONTEXT.md    ← Full system spec
├── requirements.txt             ← Python dependencies
├── .env                         ← API keys (never commit)
└── .streamlit/
    └── config.toml              ← Dark theme config
```

---

## ENVIRONMENT VARIABLES (.env)

```
POLYGON_API_KEY=...
SUPABASE_URL=https://zabyiqhyliuvrwqbnxkq.supabase.co
SUPABASE_SECRET_KEY=...        (rotated 2026-08-13)
SUPABASE_PUBLISHABLE_KEY=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...
SUPABASE_PASSWORD=...
SUPABASE_PROJECT=q-alpha
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
OPENROUTER_API_KEY=...         (key name: Bilbo)
```

Modal secret name: `q-alpha-secrets` (contains all above)
Streamlit Cloud secrets: SUPABASE_URL + SUPABASE_SECRET_KEY

---

## SUPABASE TABLES

```
trades            — all paper trades with P&L
pool_snapshots    — daily pool value history
daily_scans       — morning scan results
system_health     — component last-run timestamps
daily_reviews     — Cursor automation trade reviews
```

---

## CORE TRADING RULES (from Phase 0 research)

### Universe Filters
```
Price:          $5 - $50 (need 6+ shares for bracket)
Gap:            3% - 50% pre-market
Volume ratio:   >= 1.5x normalized pre-market
Dollar volume:  >= $2M daily average
Stock type:     Common stock only (no ETFs, warrants)
Min shares:     6 (required for 3-tranche bracket)
```

### Quality Scoring (0-100)
```
Gap sweet spot 3-6%:    25 pts (best)
Gap 6-10%:              20 pts
Gap 10-15%:             12 pts
Gap 15-25%:             6 pts
Gap > 25%:              2 pts (likely exhausted)

Vol ratio >= 10x:       30 pts
Vol ratio >= 7x:        25 pts
Vol ratio >= 5x:        20 pts
Vol ratio >= 3x:        14 pts
Vol ratio >= 2x:        8 pts
Vol ratio >= 1.5x:      4 pts

Price $10-$35:          20 pts (ideal for bracket)
Price $5-$10:           15 pts
Price $35-$50:          12 pts

Has news catalyst:      15 pts
Dollar vol >= $20M:     10 pts
Dollar vol >= $10M:     8 pts
Dollar vol >= $5M:      6 pts
Dollar vol >= $2M:      4 pts
```

### Entry Conditions (all must be true)
```
gap_holding:     current_price > prev_close × 1.015
above_vwap:      current_price > VWAP
vol_confirming:  up_volume > down_volume × 1.1
not_dumping:     current_price > open_price × 0.97
not_broke_str:   current_price > first_candle_low × 0.99
min_wait:        >= 2 minutes after open
```

### Skip Conditions (any triggers skip)
```
gap_filled:      current_price < prev_close × 1.005
hard_dump:       current_price < open_price × 0.95
broke_structure: current_price < first_candle_low × 0.99
                 (after 5 minutes)
```

### Position Sizing
```
Per trade:    10% of current pool
Tranches:     33% / 33% / 34%
Max per day:  3 trades
Max total:    10 positions
T3 trail:     Does NOT count against 10 slot limit
              Same ticker eligible for re-entry when T3 only
Pool floor:   $2,000 (halt new entries below this)
```

### Stop Placement
```
Structure-based: below first 5-min candle low after open
Minimum:         2% below entry
Maximum:         7% below entry
ATR cap:         Excludes gap day from ATR calculation
                 (gap day inflates ATR massively)
```

### Bracket Targets
```
T1 exit (33%): entry + 1×stop_distance (1R)
T2 exit (33%): entry + 2×stop_distance (2R)
T3 trail (34%): trails with price, no fixed target
Max hold:       5 trading days (time exit)
```

---

## BACKTEST RESULTS (Phase 2 — completed)

Best experiment: EXP-0017
```
Strategy:       Gap≥3% + vol≥2x filter + bracket system
Selector:       Rules-based (no ML needed)
Sharpe:         1.87
Max Drawdown:   -5.52%
Win Rate:       ~39%
Trades/year:    ~52 (test period)
Total return:   +27.2% (OOS 2023-2024)
Walk-forward:   2/4 windows
```

Key finding: The gap filter + bracket system IS the edge.
ML model added no value. Rules-based entry sufficient.

---

## IBKR CONNECTION

```
TWS Port:       7497 (paper trading)
                7496 (live trading — not yet)
Client IDs used:
  ibkr_connector.py:    clientId=1
  autonomous_agent.py:  clientId=5
  (keep these separate to avoid conflicts)

Paper account: DUR857496
Live account:  Funding in progress (~2-4 weeks)

Data subscriptions active:
  US Securities Snapshot Bundle (NP,L1): $10/month
  US Equity Add-On Streaming (NP):       $4.50/month
  NASDAQ TotalView-OpenView (NP,L2):     $16.50/month
  NOTE: Paid subs require funded live account to activate
        Currently using free paper data
```

---

## KNOWN ISSUES / ACTIVE BUGS

```
1. Pool state inconsistency
   EOD monitor recalculates pool from scratch each run
   paper_trades.json and pool_state.json can drift
   FIX: pool recalc in position_monitor.py (implemented)

2. Modal cron timezone
   All crons use EDT (UTC-4) — switches to EST in November
   Will need cron adjustment in November
   Current EOD: "15 20 * * 1-5" = 4:15 PM EDT

3. Supabase sync on Modal
   supabase package must be in Modal image pip_install
   scheduler.py image includes: "supabase"

4. State sync between local and Modal volume
   autonomous_agent.py writes to local candidates/
   EOD monitor reads from /state/ (Modal volume)
   sync_to_modal() in autonomous_agent.py handles this
   Must run after every session
```

---

## WHAT'S BEEN TRIED AND FAILED

```
FAILED APPROACHES:
  - LightGBM model: no signal in daily bar data alone
  - Pre-market gap filter without intraday confirmation:
    all trades stopped out at open (gap-and-crap)
  - Next-day open entry: enters at top of move
  - Static hardcoded ticker universe: 47% tickers delisted
  - MOC order without gap-day confirmation: too late in move
  - Vol ratio 2x vs full daily average: always 0 pre-market
    (fixed: normalized against expected PM volume)
  - Stocks > $50: not enough shares for bracket mechanics
  - ETFs in universe: violent moves that immediately reverse
```

---

## PHASE 2 ROADMAP (not yet built)

### Stock Profiler (stock_profiler.py)
```
Purpose: Intelligent stop placement using stock history
How:
  1. Pull stock's full Polygon history
  2. Find similar gap days (same gap%, similar vol)
  3. Analyze how far it pulled back on those days
  4. Feed to Claude: "what stop would have worked?"
  5. Claude recommends stop based on THIS stock's behavior
  
Why it matters:
  Every stock has a personality
  High-float stocks need wider stops (they shake more)
  Low-float biotech needs tighter stops (move fast)
  Current structure-based stop is a placeholder
```

### Claude Learning from Q-ALPHA Data
```
Week 3+ plan:
  Feed our own trade results back to Claude
  "Here are 50 trades. 25 won, 25 lost.
   What separates them?"
  Claude learns from OUR data not internet advice
  All internet trading advice = retail trap
  We build genuine alpha from our own results
```

---

## HOW TO START A NEW CONVERSATION

Paste this at the start of any new Chatbox conversation:

```
I am Aaron Klepp building Q-ALPHA, a fully autonomous 
momentum trading system. 

Read this full handoff document — it contains everything
about the system state, what's built, what works, 
what's failed, and what's next:

[paste Q_ALPHA_HANDOFF.md contents]

Today is Day X of v1.0.0 autonomous operation.
The system has been running since 2026-08-18.

Current focus: [describe what you're working on]
```

---

## PERSONALITY / PHILOSOPHY NOTES

These matter for continuity across conversations:

```
1. "Everything on the internet is retail trap advice"
   Claude should learn from Q-ALPHA's OWN data
   Not from what "experts" say about trading

2. Full autonomy is the goal
   Aaron should not need to make any trading decisions
   Only open TWS at 9:15 AM. Everything else automatic.

3. Paper trade first, prove it works, then go live
   Live account funding in ~2-4 weeks
   30 days of paper trading minimum before real money

4. When something doesn't work — diagnose before fixing
   Don't just patch symptoms
   Find the root cause first

5. The system learns and improves daily
   Cursor automation writes daily reviews
   Patterns identified over weeks → scoring improvements
   This is a living system not a static one

6. VPS for future
   When going live: $15/month Windows VPS
   IB Gateway running 24/7
   No laptop needed at all
   Modal connects to VPS IP
```

---

## QUICK COMMAND REFERENCE

```bash
# Run autonomous agent manually (with TWS open)
python candidates/autonomous_agent.py

# Deploy Modal scheduler
modal deploy candidates/scheduler.py

# Run EOD monitor manually
modal run candidates/scheduler.py::run_eod_monitor

# Run intraday monitor manually
modal run candidates/scheduler.py::run_intraday_monitor

# Sync local state to Modal
python -c "
import subprocess, sys
for local, remote in [
    ('candidates/paper_trades.json', 'paper_trades.json'),
    ('candidates/pool_state.json', 'pool_state.json')
]:
    subprocess.run([sys.executable, '-m', 'modal', 'volume', 
                   'put', 'qalpha-state', local, remote, '--force'])
"

# Update Modal secrets from .env
$vars = Get-Content .env | Where-Object { $_ -match '=' -and $_ -notmatch '^#' -and $_.Trim() -ne '' }
$cmd = "modal secret create q-alpha-secrets --force " + ($vars -join ' ')
Invoke-Expression $cmd

# Push to GitHub + Streamlit auto-deploys
git add .
git commit -m "your message"
git push
```

---

## DASHBOARD URL

```
https://q-alpha-lshnrvza2radqpkjrkf52m.streamlit.app

Tabs:
  Live Status   — pool, positions, today's scan
  Trade Log     — all closed trades
  Performance   — equity curve, monthly returns
  System Health — component last-run times
  Daily Reviews — Cursor automation analysis
```

---

*Document generated: 2026-08-17*
*System version: v1.0.0*
*Day 1: 2026-08-18*
