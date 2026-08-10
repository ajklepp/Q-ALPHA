# EXP-0001 Results — AAPL Random Forest

## Status: REJECTED

## What We Tested

Random Forest predicting UP/DOWN on AAPL over 5 days

Using 13 technical indicator features

## Results

- Train Accuracy : 78.35%

- Test Accuracy  : 52.92%

- Baseline       : 60.48%

- Overfit Gap    : 25.43%

## Quant Assassin Verdict

FAIL — Two critical issues:

1. Does not beat baseline

2. Massive overfit gap (25%) — model is memorising not learning

## What We Learned

- Volatility is the strongest signal (0.122 importance)

- Simple UP/DOWN prediction is too noisy

- One stock is not enough training data

## Next Experiment

- Change target to: price up MORE THAN 1% in 5 days

- Reduce model depth to fight overfitting

- Train on 10 stocks instead of 1

