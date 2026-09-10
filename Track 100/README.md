# Track 100

**Isolated research study — does not modify Q-ALPHA live paper.**

Goal: test the TradingView **Wave Cross (TSD)** trough + green-dot long (TSLA chart)
on Nasdaq-100 / liquid names over ~3 months.

## Locked result

See **[STRATEGY.md](STRATEGY.md)** — Track100_v2:

- NDX100 only
- Entry: `early_bull` AND wave ≤ −53
- Stop 6% / target 12% / 40×1H time cap
- Study: **+0.48% avg net**, PF **1.30**, N=762 (Jun–Sep 2026)

## Run

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
py -3 "Track 100\sanity_tsla.py"
py -3 "Track 100\run_study.py"
py -3 "Track 100\refine_v2.py"
py -3 "Track 100\scan_live.py"
```

Results under `Track 100/results/` (`results.md`, `results_v2.md`, locks, caches).
