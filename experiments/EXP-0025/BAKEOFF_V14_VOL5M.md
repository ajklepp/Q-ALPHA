# EXP-0025b — Large-sample bakeoff: v1.4 vs +5m vol-alive

**Generated:** 2026-09-07T04:36:32.945971+00:00
**Runtime:** 18.5s (Modal)
**Live score baseline:** `v1.4`
**Recommendation:** **HOLD_DIAGNOSTIC**

Expanding-vol strata still richer than baseline, but soft score variants did not clear ship gate vs v1.4 on full-corpus slots. HOLD — do not wire.

## Design
`{"admits": 3353, "symbols": 267, "symbols_5m_ok": 267, "lookback_min": 180, "slots": 2, "oos_cut": "2026-08-11", "expand_pts": 10.0, "alive_pts": 12.0, "dead_pts": 12.0}`

## Signatures (full corpus)
`{"n_admit": 3353, "n_vol5_ok": 1695, "pct_vol5_ok": 0.506, "expand_n": 210, "expand_wr": 0.09047619047619047, "alive_n": 97, "alive_wr": 0.12371134020618557, "dead_n": 1240, "dead_wr": 0.029838709677419355, "base_wr": 0.06441992245750075, "med_acf_train": 0.050772509076590464, "med_rv_train": 0.6401111295093891}`

## Bakeoff (top-2 slots / date-hour)

| Variant | n | WR | Exp | MFE | Capture | OOS Exp | OOS WR | Pass vs v1.4 | Vacuous |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| v14 | 720 | 10.3% | 0.2976 | 0.0231 | 43.3% | 0.3513 | 11.8% | — | False |
| v14_expand | 720 | 10.4% | 0.2947 | 0.0228 | 41.7% | 0.3386 | 11.4% | no | False |
| v14_alive | 720 | 11.1% | 0.2857 | 0.0227 | 41.7% | 0.3056 | 11.8% | no | False |
| v14_cluster | 720 | 10.8% | 0.2873 | 0.0231 | 43.3% | 0.3313 | 11.8% | no | False |
| v14_rv_quartile | 720 | 10.6% | 0.2983 | 0.0231 | 42.2% | 0.3525 | 12.3% | no | False |
| v14_combo | 720 | 11.1% | 0.2840 | 0.0226 | 39.4% | 0.3043 | 11.4% | no | False |

**Winners:** `[]`

## Decision

- Reply **ADD soft** / **HOLD** for live `continuation_score` wire.
- No live rewrite from this file alone.

## Notes

- Full all_hours_admit corpus (3353) — larger than EXP-0025 sample (1400).
- 5m fetched per symbol once; pre-signal slice only.
- Missing 5m ⇒ neutral (no boost/demote) so v1.4 still ranks them.
- Research only — no live tsd_launch_score edit in this run.
