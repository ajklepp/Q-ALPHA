# EXP-0021 — Blind-spot #4: Sector RS / peer co-move

**Generated:** 2026-09-06T00:13:22.926793-04:00
**Runtime:** 24.9s (Modal)
**Recommendation:** **SOFT**

Best real passer: **boost_rs_spy_lead**. Soft overlay only if you approve.

## Coverage

`{"symbols": 267, "meta_ok": 267, "fetch_errors": 0, "admit_rows": 3353, "rs_labeled": 3353, "rs_coverage": 1.0, "alone_up_rate": 0.053683268714583954}`

## How this maps to Peak Hour

- Lone-wolf spikes fade more than group moves (quant peer / industry momentum).
- RS vs SPY / sector ETF + peer breadth as admit/rank context.
- Live v1.2 has no sector/peer term today.

## Strata (RS-labeled admits)

### RS vs SPY (5d)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| rs_spy_lag_< -3% | 974 | 7.3% | 0.250 | 0.012 |
| rs_spy_flat | 733 | 4.1% | 0.245 | 0.011 |
| rs_spy_lead_>3% | 1646 | 7.0% | 0.260 | 0.012 |

### RS vs sector ETF (5d)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| rs_sec_lag | 944 | 7.3% | 0.257 | 0.012 |
| rs_sec_flat | 785 | 4.6% | 0.237 | 0.010 |
| rs_sec_lead | 1624 | 6.8% | 0.260 | 0.012 |

### Peer breadth (prior day)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| breadth_<0.35 | 1305 | 7.4% | 0.256 | 0.012 |
| breadth_0.35_0.6 | 494 | 5.1% | 0.226 | 0.012 |
| breadth_>=0.6 | 949 | 6.2% | 0.275 | 0.012 |

### Alone-up (stock up, peers weak)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 3173 | 6.4% | 0.255 | 0.012 |
| 1 | 180 | 7.8% | 0.226 | 0.011 |

## Bakeoff vs v1.2

| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v12 | 720 | 10.4% | 0.299 | 42.8% | 0.357 | — |
| boost_rs_spy_lead | 720 | 11.1% | 0.301 | 46.1% | 0.353 | YES |
| boost_rs_sector_lead | 720 | 11.0% | 0.303 | 48.3% | 0.351 | YES |
| demote_alone_up | 720 | 10.7% | 0.292 | 41.7% | 0.347 | no |
| combo_rs_peer | 720 | 9.6% | 0.295 | 41.1% | 0.313 | no |
| skip_alone_up | 715 | 9.9% | 0.301 | 40.6% | 0.351 | no |
| skip_rs_spy_lag | 692 | 9.1% | 0.291 | 37.2% | 0.327 | no |
| skip_breadth_lt_0.35 | 651 | 7.8% | 0.273 | 31.1% | 0.296 | no |
| prefer_breadth_ge_0.6 | 425 | 8.5% | 0.316 | 20.6% | 0.304 | no |

## Decision needed

- Reply: **ADD soft / ADD hard / HOLD**
- No live rewrite from this run.

## Notes

- SIC from Polygon ticker details; sector ETF via coarse SIC map.
- RS uses prior closes only (no look-ahead).
- Peer breadth = same SIC2 peers' prior-day up fraction.
- Baseline = live v1.2.
