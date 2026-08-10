# EXP-0003 Results — Balanced Random Forest

## Status: PASS — First clean pass on all 3 checks

## Changes From EXP-0002

- Added class_weight="balanced"

- Added precision-recall threshold tuning (threshold=0.508)

## Results

- Train Accuracy : 60.41%

- Test Accuracy  : 55.36%

- Overfit Gap    :  5.04%

- Baseline       : 45.83%

- Beats Baseline : YES by 9.54%

- Edge Recall    : 22.66%

## Quant Assassin Verdict

PASS — All 3 checks passed for first time

- Overfit gap acceptable : 5.04%

- Beats baseline         : 9.54%

- Edge recall acceptable : 22.66%

## Star Performers

- JNJ  : 70.56% accuracy vs 27.82% baseline

- TSLA : 55.44% accuracy, 69.88% edge recall

- XOM  : 57.86% accuracy vs 38.10% baseline

## Failures

- META : 48.39% — below baseline, no edge

- GOOGL: 50.81% — marginal

## Key Concern

Volatility dominates features (0.181 importance)

Is the model predicting direction or just volatility?

## Next Experiment

- EXP-0004A: Remove volatility features, retest

- EXP-0004B: Focus on defensive stocks only (JNJ, WMT, XOM, JPM)

