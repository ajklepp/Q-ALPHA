# EXP-0021 — Ship gate: continuation_score v1.1

**Generated:** 2026-09-05T17:50:14.755414-04:00
**Corpus:** `corpus_htf_universe_social.csv` · slots=2
**Verdict:** **SHIP v1.1**

## v1 vs v1.1 (taken slots)

| Version | n | WR | Exp R | Capture |
|---|---:|---:|---:|---:|
| v1 | 720 | 10.0% | 0.285 | 43.3% |
| **v1.1** | 720 | 10.4% | 0.297 | 42.8% |

### OOS (signal_date ≥ 2026-08-11)

- v1: n=228 WR=11.8% exp=0.335
- v1.1: n=228 WR=12.3% exp=0.357

## Deeper: multi-year filter (SMA200 > -15%) + v1.1 rank

- n=702 WR=9.3% exp=0.281 cap=37.2%
- Ship filter only if it beats v1.1 without killing capture — see numbers.

## Deeper: news strata (admits)

| Bucket | n | WR | Exp R |
|---|---:|---:|---:|
| no_news | 3131 | 6.4% | 0.252 |
| any_news | 222 | 6.8% | 0.285 |
| type:earnings | 28 | 10.7% | 0.354 |
| type:none | 189 | 5.8% | 0.268 |

## Dilution flag (admits)

| | n | WR | Exp R |
|---|---:|---:|---:|
| dilution | 6 | 16.7% | 0.170 |
| clean | — | 6.4% | 0.254 |

## Live action

- **SHIPPED `continuation_score_v1.1` to live paper** (`CONTINUATION_SCORE_VERSION = "v1.1"`).
- Do **not** add multi-year admit filter (exp 0.281 &lt; v1.1 0.297; capture down).
- News stays soft-rank; dilution/distress remain soft penalties.
- X remains off.
