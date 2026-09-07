# EXP-0023 Round-2 — Cross-sectional + SPY regime

**Generated:** 2026-09-07T03:57:52.721046+00:00
**Runtime:** 15.1s
**Recommendation:** **FAIL**

Round-2 also FAIL. Closest=ew_spy_bull edge_sharpe=0.217.

**Ship:** `[]`
**Gate:** `[]`

## Bakeoff vs equal-weight universe

| Strategy | Sharpe | EW Sharpe | Edge | Ret | EW Ret | MDD | Pass% | Gate | Ship |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| ew_spy_bull | 1.23 | 1.01 | +0.22 | 0.940 | 1.133 | -17.5% | 32% | no | no |
| cs_mom60 | 1.21 | 1.01 | +0.20 | 1.790 | 1.133 | -38.5% | 18% | no | no |
| cs_mom20_spy_bull | 1.11 | 1.01 | +0.10 | 1.204 | 1.133 | -22.9% | 36% | no | no |
| cs_mom120 | 1.04 | 1.01 | +0.03 | 1.602 | 1.133 | -34.3% | 36% | no | no |
| cs_mom20 | 1.02 | 1.01 | +0.01 | 1.453 | 1.133 | -37.8% | 32% | no | no |
| cs_rs20 | 1.02 | 1.01 | +0.01 | 1.453 | 1.133 | -37.8% | 32% | no | no |
| ew_universe | 1.01 | 1.01 | +0.00 | 1.133 | 1.133 | -29.8% | 0% | no | no |
| cs_mom20_lowvol | 0.46 | 1.01 | -0.55 | 0.399 | 1.133 | -26.6% | 18% | no | no |
| cs_mr_resid | 0.30 | 1.01 | -0.71 | 0.503 | 1.133 | -43.5% | 4% | no | no |
| cs_low_vol | 0.21 | 1.01 | -0.79 | 0.156 | 1.133 | -21.9% | 32% | no | no |
| cs_mr_z | 0.07 | 1.01 | -0.93 | 0.104 | 1.133 | -42.5% | 4% | no | no |

## Notes

- Round-1 time-series timing FAIL; Round-2 = cross-section + SPY regime.
- Long-only top quintile (or EW). No shorts.
- Standalone research.
