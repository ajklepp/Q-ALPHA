# EXP-0021 results — Continuation Ranker bakeoff

**Verdict: PASS**

Generated: 2026-09-04T18:06:47.878146-04:00
Runtime: 0.1s
Corpus: 595 signals / 28 symbols / 90d lookback
Social: StockTwits + Polygon news velocity; X=off (no bearer)

## Gate checks

- Challenger beats Peak Hour v0 on expander capture without expectancy/WR collapse

## Peak Hour v0 (baseline)

| Metric | Value |
|---|---|
| Slots taken | 174 |
| Win rate (hit_1r) | 9.2% |
| Expectancy (R) | 0.326 |
| Median MFE | 1.33% |
| Expander capture | 47.4% (18/38) |
| OOS WR / Exp | 6.5% / 0.283 |

## All-hours + continuation_score_v1 (challenger)

| Metric | Value |
|---|---|
| Slots taken | 265 |
| Win rate (hit_1r) | 8.7% |
| Expectancy (R) | 0.281 |
| Median MFE | 1.22% |
| Expander capture | 60.5% (23/38) |
| OOS WR / Exp | 5.1% / 0.282 |

## Case studies (feature explanation)

- **IREN** @ 2026-06-11 06:00:00-04:00 hour=7 peak=1 scan=14 orange room20d=33.8% bounce=0.00 vol=0.0x v0_admit=1 score_v0=79.3 score_v1=55.8 day_mfe=8.0% hit_1r=1 news24=0 st=0
- **TARS** @ 2026-09-02 09:00:00-04:00 hour=10 peak=0 scan=53 yellow room20d=-0.8% bounce=0.00 vol=1.1x v0_admit=0 score_v0=95.2 score_v1=30.2 day_mfe=9.7% hit_1r=1 news24=0 st=0
- **CHPT** @ 2026-06-17 10:00:00-04:00 hour=11 peak=1 scan=64 orange room20d=14.1% bounce=0.00 vol=0.6x v0_admit=0 score_v0=75.5 score_v1=24.9 day_mfe=6.0% hit_1r=1 news24=0 st=0
- **ARX** @ 2026-09-04 09:00:00-04:00 hour=10 peak=0 scan=60 green room20d=0.3% bounce=0.00 vol=0.5x v0_admit=0 score_v0=101.6 score_v1=-15.9 day_mfe=0.7% hit_1r=0 news24=0 st=0
- **JANX** @ 2026-07-08 13:00:00-04:00 hour=14 peak=0 scan=20 yellow room20d=1.1% bounce=0.00 vol=0.5x v0_admit=0 score_v0=94.6 score_v1=21.8 day_mfe=3.8% hit_1r=0 news24=0 st=0

## Live promotion

**Pilot PASS** on 28-symbol / 90d corpus — **do not auto-wire live yet.**

Next: confirm on full HTF daily universe (cached `tsd_universe_*.json`), then Chat B wires hourly :15 scan clock + ranker under the 2-slot cap. Peak Hour live paper stays as-is until that follow-up.

## Costs

COST_PER_TRADE = 0.0015 (reported expectancy is path R before cost; slot counts are pre-cost).
