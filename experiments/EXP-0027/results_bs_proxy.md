# MODEL PROXY ONLY — NOT FILLS

**model_proxy_pnl**. NOT gospel. NOT broker fills. NOT live paper. NOT Track 100.

Proxy P&L **$-2,097.64** (-41.95% on $5,000).
Win rate 38.46%. Trades 13. Max DD -43.12%.
Informational research gates: **FAIL** (Sharpe, drawdown, walk-forward, Monte Carlo). A pass would still be a model proxy.
Same-signal 100-share stock baseline (all technical signals, net of 0.15% once): **$-14,782.32** on 33 trades, win rate 18.18%, max DD $-14,782.32.
100-share stock on the entries the call book actually took: **$-3,192.03** on 13 trades.

These dollars are Black–Scholes marks with a frozen volatility. They are not fills. Do not trade from this file.

## What was measured

- Window: 2026-01-16 → 2026-09-16 (Track 100 ops stack). Fetch warmup from 2025-09-01.
- Names: SPY, NVDA, TSLA, AVGO.
- Stock bars: `yahoo_chart_daily_ohlc_not_polygon`.
- Signals: 33. Allocated calls: 13.
- Option headline is gross BS P&L. After a 0.15% debit haircut (not a spread): $-2,142.42.
- Runtime: 1.8 seconds.

## IV used

- **SPY**: IV 0.099 via `realized_vol_fallback` (source `realized_vol_fallback`). 10-day RV window 2026-09-02 → 2026-09-16.
- **NVDA**: IV 0.322 via `realized_vol_fallback` (source `realized_vol_fallback`). 10-day RV window 2026-09-02 → 2026-09-16.
- **TSLA**: IV 0.490 via `realized_vol_fallback` (source `realized_vol_fallback`). 10-day RV window 2026-09-02 → 2026-09-16.
- **AVGO**: IV 0.328 via `realized_vol_fallback` (source `realized_vol_fallback`). 10-day RV window 2026-09-02 → 2026-09-16.

IV from the recent window is frozen and applied to every past entry. That is not the volatility the market was charging on the entry date.

## Assumptions

- European Black–Scholes, continuous r = 0.04, q = {'SPY': 0.011, 'NVDA': 0.0003, 'TSLA': 0.0, 'AVGO': 0.007}.
- r and q are constants, not Treasury or dividend prints.
- No early exercise. SPY and AVGO calls are slightly cheap versus American models.
- Strike is the nearest $1 inside 5–12% ITM of the prior close (target 8%). Not a listed chain.
- Expiry is the Friday closest to 32 calendar days, inside 21–45. Holiday Fridays are still used.
- Entry premium uses the stock open and DTE/365. Later premiums use that session's stock close and one day less.
- Underlying stop, +1R breakeven on the next session, +2R, half the model premium, 5 sessions, or 7 DTE.
- Same-bar stop and target is dropped and has no P&L.
- One contract. Debit above $5,000 is a skip (`premium_exceeds_book`), not a fraction.
- Long calls only. No puts, no short calls.
- No Polygon option OHLC was requested or invented.
- After hours, a null underlying last/mid is ignored. Spot is the last stock-hist close, then this study's last daily close.
- Call IV uses ~10 days of bridge hist (hourly mids, then daily). One live call quote mid/last/close is an extra sample.
- Phase 9A put-credit mids are not call IV.

## Stock bars

- POLYGON_API_KEY was not in the environment
- modal package not installed; cloud VM cannot see laptop secrets

Entries align with the Track 100 ops stack window 2026-01-16 → 2026-09-16. Bars from 2025-09-01 exist only so SMA50 and ATR are known before the first entry. This is not the 2023–2025 walk-forward.

## By name

| Name | IV | IV source | Call trades | Call P&L | Call win rate |
|---|---:|---|---:|---:|---:|
| SPY | 0.099 | realized_vol_fallback | 0 | $0.00 | n/a |
| NVDA | 0.322 | realized_vol_fallback | 9 | $-424.58 | 44.44% |
| TSLA | 0.490 | realized_vol_fallback | 1 | $-541.02 | 0.00% |
| AVGO | 0.328 | realized_vol_fallback | 3 | $-1,132.04 | 33.33% |

## Exit reasons (allocated calls)

- target_2r: 1
- time_5d: 3
- underlying_stop: 9

## Skips

- premium_exceeds_book: 9
- skipped_cash: 11
- SPY: premium_exceeds_book 9
- TSLA: skipped_cash 8
- AVGO: skipped_cash 3

## Informational gates

These use the EXP-0013 thresholds on an 8-month proxy. A FAIL here is a research note. A PASS would still not be a fill.

- **FAIL** sharpe: -0.2731665743368577 (need >= 1.5)
- **FAIL** max_drawdown: -0.43121665590643365 (need >= -0.15)
- **FAIL** positive_return: -0.41952803425289886 (need > 0)
- **FAIL** beats_spy_buy_hold: {'strategy': -0.41952803425289886, 'spy': 0.09020330374879215} (need strategy return > SPY close-to-close)
- **FAIL** walk_forward: 0/4 (need >= 3/4)
- **FAIL** monte_carlo: 0.47840000000000005 (need p < 0.05)

Walk-forward 0/4.

- **FAIL** W1 2026-01-16 → 2026-03-15: n=5 pnl=$-1,971.84 sharpe=-2.643 dd=-39.44%
- **FAIL** W2 2026-03-16 → 2026-05-15: n=1 pnl=$977.07 sharpe=4.222 dd=0.00%
- **FAIL** W3 2026-05-16 → 2026-07-15: n=2 pnl=$-342.92 sharpe=-0.094 dd=-23.23%
- **FAIL** W4 2026-07-16 → 2026-09-16: n=5 pnl=$-759.95 sharpe=-0.452 dd=-33.55%

Monte Carlo (5,000, seed 42): p=0.47840000000000005 ran=True FAIL

## Trades

| Entry | Exit | Name | Strike | DTE | Reason | Call P&L | Debit | Stock 100 P&L |
|---|---|---|---:|---:|---|---:|---:|---:|
| 2026-01-16 | 2026-01-20 | NVDA | 172.0 | 35 | underlying_stop | $-898.01 | $1,925.79 | $-746.36 |
| 2026-01-20 | 2026-01-20 | AVGO | 324.0 | 31 | underlying_stop | $-826.78 | $2,586.37 | $-1,229.26 |
| 2026-01-26 | 2026-01-30 | NVDA | 173.0 | 32 | time_5d | $297.97 | $1,659.87 | $368.93 |
| 2026-02-09 | 2026-02-13 | NVDA | 171.0 | 32 | underlying_stop | $-163.14 | $1,580.81 | $-27.64 |
| 2026-02-26 | 2026-02-26 | AVGO | 306.0 | 29 | underlying_stop | $-381.89 | $2,524.58 | $-1,498.05 |
| 2026-05-07 | 2026-05-11 | NVDA | 191.0 | 29 | target_2r | $977.07 | $1,946.41 | $1,350.89 |
| 2026-06-02 | 2026-06-03 | NVDA | 206.0 | 31 | underlying_stop | $-1,011.66 | $2,331.69 | $-852.86 |
| 2026-07-09 | 2026-07-15 | NVDA | 188.0 | 29 | time_5d | $668.74 | $1,864.39 | $773.33 |
| 2026-07-22 | 2026-07-24 | AVGO | 356.0 | 30 | underlying_stop | $76.63 | $2,984.52 | $-57.03 |
| 2026-08-04 | 2026-08-10 | NVDA | 190.0 | 31 | time_5d | $531.36 | $2,305.78 | $593.30 |
| 2026-08-14 | 2026-08-18 | TSLA | 313.0 | 35 | underlying_stop | $-541.02 | $3,867.55 | $-1,147.99 |
| 2026-08-28 | 2026-08-28 | NVDA | 210.0 | 35 | underlying_stop | $-743.99 | $2,060.68 | $-685.39 |
| 2026-09-03 | 2026-09-08 | NVDA | 206.0 | 29 | underlying_stop | $-82.93 | $2,212.35 | $-33.90 |

## Laptop rerun

The cloud VM has no IBKR. This file is whatever that VM could measure. On the laptop the same script prefers Polygon and the bridge, and overwrites this report.

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
.\experiments\EXP-0027\run_on_laptop.ps1
```

One command after the pull: `.\experiments\EXP-0027\run_on_laptop.ps1`
