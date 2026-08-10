# EXP-0002 Results — 10 Stock Random Forest

## Status: PARTIAL PASS — Needs improvement

## Changes From EXP-0001

- Target changed to: price up MORE THAN 1% in 5 days

- Model depth reduced from 6 to 4

- Trained on 10 stocks instead of 1

## Results

- Train Accuracy : 59.71%

- Test Accuracy  : 54.01%

- Overfit Gap    :  5.70%

- Baseline       : 45.83%

- Beats Baseline : YES by 8.19%

## Quant Assassin Verdict

PARTIAL PASS

- Overfit gap fixed

- Beats baseline

- BUT model almost never predicts Edge (recall 0.02)

- Misses 98% of real opportunities

## What We Learned

- Volatility dominates feature importance again

- Class imbalance is the main problem now

- Model defaults to predicting No Edge almost always

## Next Experiment

- Add class_weight=balanced to force equal attention

- Review feature set — volatility too dominant

