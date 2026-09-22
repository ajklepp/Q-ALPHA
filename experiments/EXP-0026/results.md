# EXP-0026 — ITM long-call study

Research only. Long calls. Separate from Track 100 equity. No orders.

**Status:** NOT_RUN
**Generated:** 2026-09-22T01:41:03.200642+00:00
**Runtime:** 0.0s
**Recommendation:** **NOT_RUN**

**Reason:** `not_executed_in_cloud`

This copy was written in the repo without calling Polygon or Modal. The cloud session has no POLYGON_API_KEY and no Modal token. No option bars were requested and no P&L was computed. Run the laptop PowerShell in this file. If the options plan refuses the bars, that run replaces this file with NOT_RUN and still leaves P&L empty. TWS at 127.0.0.1:8787 is not a backfill for the 2023-2025 windows.

Option performance was not computed. This file has no P&L, Sharpe, drawdown, walk-forward score, or Monte Carlo p-value.

## Rules (what a laptop run will apply)

See `README.md`. Constants:

```json
{
  "book_usd": 5000.0,
  "multiplier": 100,
  "itm_min_pct": 0.05,
  "itm_target_pct": 0.08,
  "itm_max_pct": 0.12,
  "dte_min": 21,
  "dte_max": 45,
  "dte_exit": 7,
  "max_hold_sessions": 5,
  "atr_window": 14,
  "stop_atr_mult": 1.0,
  "target_r": 2.0,
  "trail_arm_r": 1.0,
  "premium_stop_frac": 0.5,
  "sharpe_min": 1.5,
  "max_dd_floor": -0.15,
  "equity_cost_not_used": 0.0015,
  "side": "BUY_TO_OPEN_CALL",
  "delta": "not_fetched_percent_itm_is_the_depth_rule"
}
```

## Laptop Modal

```powershell
Set-Location C:\Users\ajkle\Documents\Q-ALPHA
.\venv\Scripts\python.exe -m modal run experiments/EXP-0026/study_itm_long_call_modal.py
```

Optional read-only TWS bridge probe (does not build P&L):

```powershell
Set-Location C:\Users\ajkle\Documents\Q-ALPHA
.\candidates\start_options_bridge.ps1
.\venv\Scripts\python.exe experiments\EXP-0026\probe_options_bridge.py
```
