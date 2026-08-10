# EXP-0004 Results — Volatility Test + Defensive Stocks

## Status: PASS — All 4 variants passed 3/3 checks

## Tests Run

A1 — All stocks with volatility features

A2 — All stocks without volatility features

B1 — Defensive stocks with volatility features

B2 — Defensive stocks without volatility features

## Final Comparison

Test                          Acc    Gap   Recall  Pass

A1 All stocks + volatility   55.4%  5.2%  23.2%   3/3

A2 All stocks - volatility   54.0%  6.0%  20.4%   3/3

B1 Defensive + volatility    57.8%  6.2%  20.8%   3/3

B2 Defensive - volatility    57.1%  6.6%  22.7%   3/3

## Key Findings

1. Volatility adds ~1.4% signal — real but small

2. Defensive stocks outperform by +2.4% accuracy

3. Defensive stocks beat baseline by +16.57% vs +9.54%

4. Edge precision still below 50% — needs monitoring

5. All overfit gaps under control at 5-7%

## Quant Assassin Verdict

PASS — Defensive stock universe with full features

is our best configuration so far

## Decision

Proceed to backtester — test if edge translates to profit

after transaction costs

## Candidate Configuration

- Universe    : JNJ, WMT, XOM, JPM, MSFT

- Features    : Full feature set including volatility

- Threshold   : ~0.514

- Target      : Price up more than 1% in 5 days

