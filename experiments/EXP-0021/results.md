# EXP-0021 results — Continuation Ranker bakeoff

**Verdict: PASS**

Generated: 2026-09-04T18:23:04.978753-04:00
Runtime: 439.3s
Corpus: 6565 signals / 272 symbols / 90d lookback / universe=htf_universe
Scan clock: hourly :15 · slots=2 · Social: StockTwits+news; X=off

## Gate checks

- Challenger beats Peak Hour v0 on expander capture without expectancy/WR collapse

## Peak Hour v0 (baseline)

| Metric | Value |
|---|---|
| Slots taken | 473 |
| Win rate (hit_1r) | 10.4% |
| Expectancy (R) | 0.220 |
| Median MFE | 1.31% |
| Expander capture | 16.1% (69/428) |
| OOS WR / Exp | 9.2% / 0.243 |

## All-hours + continuation_score_v1 (challenger)

| Metric | Value |
|---|---|
| Slots taken | 720 |
| Win rate (hit_1r) | 10.0% |
| Expectancy (R) | 0.284 |
| Median MFE | 1.40% |
| Expander capture | 20.8% (89/428) |
| OOS WR / Exp | 11.8% / 0.323 |

## Case studies (feature explanation)

- **IREN** @ 2026-06-11 06:00:00-04:00 hour=7 peak=1 scan=14 orange room20d=33.8% bounce=0.00 vol=0.0x v0_admit=1 score_v0=79.3 score_v1=54.8 day_mfe=8.0% hit_1r=1 news24=0 st=0
- **TARS** @ 2026-09-02 09:00:00-04:00 hour=10 peak=0 scan=53 yellow room20d=-0.8% bounce=0.00 vol=1.1x v0_admit=0 score_v0=95.2 score_v1=30.2 day_mfe=9.7% hit_1r=1 news24=0 st=0
- **CHPT** @ 2026-06-17 10:00:00-04:00 hour=11 peak=1 scan=64 orange room20d=14.1% bounce=0.00 vol=0.6x v0_admit=0 score_v0=75.5 score_v1=24.9 day_mfe=6.0% hit_1r=1 news24=0 st=0
- **ARX** @ 2026-09-04 09:00:00-04:00 hour=10 peak=0 scan=60 green room20d=0.3% bounce=0.00 vol=0.5x v0_admit=0 score_v0=101.6 score_v1=-15.9 day_mfe=0.7% hit_1r=0 news24=0 st=0
- **JANX** @ 2026-07-08 13:00:00-04:00 hour=14 peak=0 scan=20 yellow room20d=1.1% bounce=0.00 vol=0.5x v0_admit=0 score_v0=94.6 score_v1=21.8 day_mfe=3.8% hit_1r=0 news24=0 st=0

## Hour buckets (all HTF signals — evening decision)

| Hour ET (bar close) | n | hit_1r | Exp R | med MFE | med day MFE | peak? |
|---|---:|---:|---:|---:|---:|---|
| 07 | 471 | 23.4% | 0.239 | 2.45% | 2.54% | Y |
| 10 | 1263 | 11.7% | 0.300 | 1.66% | 1.66% |  |
| 11 | 948 | 5.4% | 0.273 | 1.25% | 1.26% | Y |
| 12 | 690 | 2.8% | 0.254 | 1.11% | 1.11% | Y |
| 13 | 665 | 2.4% | 0.248 | 0.95% | 0.95% | Y |
| 14 | 711 | 2.7% | 0.221 | 0.79% | 0.79% |  |
| 15 | 816 | 0.4% | 0.184 | 0.70% | 0.70% |  |
| 16 | 1001 | 0.0% | 0.000 | 0.00% | 0.00% |  |

**Evening / after-hours:** do **not** run entry scans after the 14:15 clock (last useful RTH bar close = 14:00). Hours ≥15 have little/no same-day path left; post-16:00 / evening extended-hours :15 adds noise and gap risk, not expander capture. Ops may still run trail/marks only.


## Live promotion

**SHIPPED:** hourly :15 for **05–15 ET**, 2-slot cap, `continuation_score_v1`.
`ALLOWED_HOURS={5,6,7,8,9,10,11,12,13,14,15}`. Premarket 05/06/08/09 from hitch study.
No 04 (empty label); no evening entry clock.

## Costs

COST_PER_TRADE = 0.0015 (reported expectancy is path R before cost; slot counts are pre-cost).
