# TSD Pipeline — IBKR Phase 1 Probe

**Run at:** 2026-08-30 04:17:39 EDT
**Host:** 127.0.0.1:7497 clientId=94
**Mode:** READ-ONLY (no orders)

**Accounts:** ['DUR857496']

## Test 1 — 3H bar parity + TSD signals

### TSLA
- Bars received: **360** (useRTH=False, 60 D)
- Last bar: `2026-08-28 17:00:00-04:00` close=348.12
- wt1=-2.714837480158162, wt2=8.23125691220958, trend=-0.8851586563301269, score=37.44
- Recent BUY crosses:
  - `2026-08-13 08:00:00-04:00` close=334.59 wt1=5.02 wt2=4.01 score=87.8
  - `2026-08-17 04:00:00-04:00` close=343.73 wt1=45.98 wt2=44.75 score=50.4
  - `2026-08-19 08:00:00-04:00` close=347.39 wt1=1.37 wt2=-9.37 score=86.6
  - `2026-08-21 04:00:00-04:00` close=349.30 wt1=30.35 wt2=26.54 score=63.0
  - `2026-08-27 08:00:00-04:00` close=353.70 wt1=-20.86 wt2=-27.52 score=77.3
- Last 5 bar timestamps:
  - `2026-08-28 05:00:00-04:00`
  - `2026-08-28 08:00:00-04:00`
  - `2026-08-28 11:00:00-04:00`
  - `2026-08-28 14:00:00-04:00`
  - `2026-08-28 17:00:00-04:00`

### NVDA
- Bars received: **360** (useRTH=False, 60 D)
- Last bar: `2026-08-28 17:00:00-04:00` close=217.88
- wt1=10.18144876809555, wt2=21.969861305823983, trend=-1.7342164309069457, score=23.73
- Recent BUY crosses:
  - `2026-08-12 05:00:00-04:00` close=219.49 wt1=-5.96 wt2=-7.09 score=58.7
  - `2026-08-17 05:00:00-04:00` close=226.64 wt1=41.82 wt2=39.72 score=54.0
  - `2026-08-21 04:00:00-04:00` close=218.37 wt1=-44.22 wt2=-46.81 score=31.8
  - `2026-08-25 04:00:00-04:00` close=210.85 wt1=-72.73 wt2=-73.70 score=5.7
  - `2026-08-26 17:00:00-04:00` close=219.53 wt1=-25.22 wt2=-31.15 score=65.0
- Last 5 bar timestamps:
  - `2026-08-28 05:00:00-04:00`
  - `2026-08-28 08:00:00-04:00`
  - `2026-08-28 11:00:00-04:00`
  - `2026-08-28 14:00:00-04:00`
  - `2026-08-28 17:00:00-04:00`

### SPY
- Bars received: **360** (useRTH=False, 60 D)
- Last bar: `2026-08-28 17:00:00-04:00` close=769.33
- wt1=40.74152673775731, wt2=51.56722811418353, trend=-0.6151615973505448, score=7.08
- Recent BUY crosses:
  - `2026-08-11 05:00:00-04:00` close=774.27 wt1=40.10 wt2=38.82 score=47.8
  - `2026-08-12 08:00:00-04:00` close=772.22 wt1=9.40 wt2=7.27 score=62.5
  - `2026-08-19 08:00:00-04:00` close=772.20 wt1=-34.94 wt2=-39.48 score=64.0
  - `2026-08-21 05:00:00-04:00` close=765.93 wt1=-48.96 wt2=-50.94 score=14.9
  - `2026-08-25 04:00:00-04:00` close=766.38 wt1=-37.42 wt2=-44.51 score=45.4
- Last 5 bar timestamps:
  - `2026-08-28 05:00:00-04:00`
  - `2026-08-28 08:00:00-04:00`
  - `2026-08-28 11:00:00-04:00`
  - `2026-08-28 14:00:00-04:00`
  - `2026-08-28 17:00:00-04:00`

## Test 2 — Extended hours (TSLA useRTH False vs True)

- useRTH=False: **360** bars
- useRTH=True:  **180** bars
- Delta (extended-only bars): **180**
- 07:00 ET bars: **not found** in last window (check alignment)

Sample extended-only timestamps (in ext not rth):
  - `2026-08-27 17:00:00-04:00`
  - `2026-08-28 04:00:00-04:00`
  - `2026-08-28 05:00:00-04:00`
  - `2026-08-28 08:00:00-04:00`
  - `2026-08-28 17:00:00-04:00`

## Test 3 — Pacing benchmark (3H historical per symbol)

- **N=100**: 239.0s total, **2.39s/symbol**, ok=98, fail=2
  - Safe delay estimate: **2.49s** between symbols
- **N=200**: 465.6s total, **2.33s/symbol**, ok=196, fail=4
  - Safe delay estimate: **2.43s** between symbols
- **N=300**: 688.9s total, **2.30s/symbol**, ok=286, fail=14
  - Safe delay estimate: **2.40s** between symbols

## Test 4 — Live price snapshot (5 symbols)

- Probe time (ET): `2026-08-30 04:40:58 EDT`

- **TSLA**: last=nan bid=-1.0000 ask=-1.0000 close=354.8100 → OK
- **NVDA**: last=nan bid=-1.0000 ask=-1.0000 close=227.9800 → OK
- **SPY**: last=nan bid=-1.0000 ask=-1.0000 close=771.1000 → OK
- **AAPL**: last=nan bid=-1.0000 ask=-1.0000 close=314.5800 → OK
- **AMD**: last=nan bid=-1.0000 ask=-1.0000 close=476.6700 → OK

## Test 5 — keepUpToDate (SPY 3H)

- Initial bars: **60**
- Waiting 20s for historicalDataUpdate…
- updateEvent callbacks: **0**
- **INCONCLUSIVE** — no update in 20s (may be normal between 3H closes)

## Summary

| Test | Status |
|------|--------|
| 3H parity + TSD | see per-symbol above |
| Extended hours | see TSLA delta |
| Pacing | see N=100/200/300 timings |
| Live prices | see per-symbol OK/FAIL |
| keepUpToDate | see callback count |

*Next: Phase 1 dry-run `tsd_scan_ibkr.py` after Pine parity spot-check.*