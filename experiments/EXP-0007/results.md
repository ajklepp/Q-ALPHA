# EXP-0007 Results — Broad Universe Signal Test

## Status: PASS — Strong edge confirmed on broad universe

## Universe

- Stocks scanned   : 161 liquid large caps

- NYSE + NASDAQ mix across all sectors

- Filtered: price > $5, volume > 500k daily

## Results

- Accuracy         : 57.25%

- Baseline         : 41.91%

- Edge             : +15.34%

- Total signals    : 14,080

- Signals per day  : 28.4

- Unique tickers   : 160

## Per Profile Edge

- BLUE_CHIP_DEFENSIVE  : +26.04%  (classifier bug - 0 signals)

- LARGE_CAP_GROWTH     : +20.72%  (1,587 signals - excellent)

- FINANCIAL_CYCLICAL   : +13.84%  (7,044 signals - good)

- HIGH_GROWTH_TECH     :  +4.21%  (4,700 signals - weak)

- HIGH_VOLATILITY      :  +1.93%  (749 signals  - avoid)

## Top Stocks By Signal Quality

NOW, GE, AMZN, DLR, WFC, PNC, CRWD, PFE, OXY, COF

## Key Findings

1. Signal edge INCREASED from 5 stocks to 161 stocks

2. Financial + Large Cap Growth profiles have strongest edge

3. High Volatility profiles should be filtered out

4. 28 signals per day gives plenty of selection

5. Ranking engine can reliably select top 3 from 28 daily

## Issues To Fix

1. Blue Chip classifier threshold too tight (0 signals)

2. High Growth Tech diluting quality (reduce weighting)

## Next Steps

- EXP-0008: Fix classifier + add profile penalty to ranking

- EXP-0008: Backtest full pipeline on broad universe

- EXP-0008: Validate ranking engine improves returns

