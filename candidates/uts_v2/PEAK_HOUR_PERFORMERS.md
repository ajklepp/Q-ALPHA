# Peak Hour Performers — v3.0

**Product name:** Peak Hour Performers  
**System version:** 3.0 (dashboard)  
**Track:** Live IBKR paper · $3,000 starting pool

## Edge
1H LAUNCH at bar-close hours **07 / 11 / 12 / 13 ET**, scan at **:15** (delayed Polygon — no front-run).  
Daily HTF pre-filter (range ≥25%, close > SMA50, SMA20 rising, price ≥ $5).  
Continuous HTF rank among passers; analogs soft-only (never veto).

## Exits
- Kill only until +1R, then BE lock `entry×0.997`
- Strategy A 4-tranche trail
- Idle flatten day ≥6 if never +1R and not trailing
- No day-2 99% tighten · no ORB structure arm

## Capacity / sizing
- 2 new entries per hourly scan · slots N = 2..10
- Slot-then-size: S=$300 frozen until N=10, then S=equity/10
- Paper gate: 20 closed legs / 45% WR before size-up beyond ladder

## Live Paper schedule
- **Sole entry:** `QAlpha TSD Scheduler` → `--tick --live` → 1H @ :15  
- Trail + Live TWS Sync (07:00 / 30m)  
- **Disabled / not Live:** Setup Watch, gap Autonomous Agent, Approval Runner  
- Research-only (keep code): polygon hunt, 3H `tsd_scan_ibkr` dry, `setup_watch_agent.py`
- **Scan funnel (research):** each 1H scan writes `results/peak_hour_scans/php_scan_*.json`  
  Weekly: `.\venv\Scripts\python.exe candidates\uts_v2\php_weekly_funnel.py --days 7 --write`

## Reset
```bash
.\venv\Scripts\python.exe candidates\uts_v2\reset_peak_hour_performers.py --also-reset-cloud
```
Archives book/pool/queue/scheduler under `candidates/archive/` (never commit).  
`--also-reset-cloud` clears Supabase TSD tables and seeds pool=$3000 so dashboard KPIs match.

## Lineage (code)
- UTS v2.6 operating system (`bc7b9bb` / lag `:15` `c11000b`)
- Scorer cleanup (`4050ca7`)
- This branding + clean-slate reset
