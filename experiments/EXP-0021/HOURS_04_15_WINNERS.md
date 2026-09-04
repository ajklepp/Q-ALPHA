# EXP-0021 — Winner existence by hour (04→15)

Generated: 2026-09-04T18:59:20.384981-04:00
Universe: 274 HTF symbols · 8313 signals · 90d
Runtime: 433s

## Question

If we scan **every 1H from 04:00 through 15:00** with continuation scoring, is there a hitchable winner each hour — so we expand *hours* (and keep 2 slots) instead of sizing up?

## Definitions

- **Clock** = (trading date, close-hour) with ≥1 admitted signal
- **Winner @ clock** = best `day_mfe` among admits that hour ≥ 5% (or hit +1R)
- **Hitch** = top-1 / top-2 by `continuation_score_v1` intersects that hour’s true top-3 by `day_mfe`
- Premarket path = remaining same-calendar-day bars (includes RTH) — causal at signal close

## Existence (is there a winner to hitch?)

| Hour | signals | clocks | clocks w/ MFE≥5% | clocks w/ hit+1R | clocks w/ MFE≥3% | med best day MFE | p90 best |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 04 | 0 | 0 | 0% | 0% | 0% | 0.0% | 0.0% |
| 05 | 550 | 61 | 79% | 77% | 93% | 7.5% | 15.4% |
| 06 | 242 | 59 | 59% | 58% | 80% | 5.3% | 12.0% |
| 07 | 250 | 61 | 59% | 57% | 75% | 5.8% | 12.7% |
| 08 | 550 | 63 | 78% | 76% | 95% | 7.9% | 24.5% |
| 09 | 507 | 62 | 74% | 74% | 85% | 7.3% | 17.2% |
| 10 | 872 | 62 | 73% | 73% | 90% | 7.1% | 12.3% |
| 11 | 506 | 63 | 33% | 33% | 65% | 4.2% | 8.9% |
| 12 | 419 | 58 | 28% | 28% | 60% | 3.5% | 7.9% |
| 13 | 448 | 63 | 24% | 24% | 54% | 3.1% | 7.4% |
| 14 | 538 | 61 | 25% | 23% | 54% | 3.1% | 7.5% |
| 15 | 613 | 61 | 15% | 11% | 49% | 2.8% | 5.6% |

## Ranker hitch (score_v1 → top-3 that hour)

| Hour | 1-slot top3 hit | 2-slot top3 hit |
|---:|---:|---:|
| 04 | 0% | 0% |
| 05 | 51% | 77% |
| 06 | 80% | 95% |
| 07 | 79% | 95% |
| 08 | 57% | 79% |
| 09 | 68% | 82% |
| 10 | 34% | 52% |
| 11 | 56% | 75% |
| 12 | 71% | 83% |
| 13 | 67% | 87% |
| 14 | 54% | 70% |
| 15 | 51% | 69% |

## Verdict

- **Close-hour 04:** 0 signals — with Polygon start-labeled 1H bars, a 04:00–05:00 candle is recorded as **close-hour 05**. There is no separate “4am close” bucket in this feed.
- **05 / 06 / 08 / 09 (newly measured):** most clocks **do** have a hitchable ≥5% day-MFE name in the HTF set (**59–79%** of clocks). Med best day MFE **5–8%**.
- **07 / 10:** still strong existence (**59% / 73%** clocks with ≥5%).
- **11–15:** existence **thins** (33% → 15%) — winners still happen, not every day.
- **2-slot hitch** into that hour’s true top-3: **strong on 05–09 / 07** (77–95%); weaker at **10** (52%) then recovers midday.

**Fundamental read (your thesis):** expanding **hours** (with score + 2-slot cap) is how you hitch morning expanders — not upsizing. A winner is **not** guaranteed every single clock, but **premarket→10** often has one; afternoon is optional/selective.

Live stays shipped `{5–15}` `:15` (05/06/08/09 added for hitch). No 04; no evening.
