# MODEL PROXY ONLY — NOT FILLS

**model_proxy_pnl**. NOT gospel. NOT broker fills. NOT live paper. NOT a new entry rule.

Call proxy **n/a** (n/a on $5,000) on 0 contracts. Track 100 stock P&L on the same tape **n/a** (n/a on $5,000, n/a on half of $5,000).
Win rate: calls n/a, tape n/a.
On the fills that bought a call, tape stock P&L was n/a (win n/a, n=0).

Premiums are Black–Scholes marks with volatility frozen from the recent window. They are not fills.

- Tape: `None`
- Tape rows: 0. Status: NOT_RUN.
- IV: not measured — tape missing
- Stock bars: `None`.
- 2x map: NVDL→NVDA, TSLL→TSLA, plus the table in this file. Tape underlying column wins.
- Runtime: 0.0 seconds.

## Skips

- None.

## IV


## Assumptions

- Entries and exits are the tape's. No SMA-cross filter from the other EXP-0027 study.
- ~8% ITM (5–12% band), Friday expiry inside 21–45 DTE, one contract if debit ≤ that fill's notional, else $500.
- Exit mark is the model value on the tape's exit session. Option stop rules are not a second exit when the tape has one.
- Levered tickers use the tape underlying column, else the documented 2x table (NVDL→NVDA, TSLL→TSLA, …).
- European BS, r = 0.04, q from the other study or 0. IV frozen backward. No Polygon option OHLC.

## Laptop

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
.\experiments\EXP-0027\run_t100_fills_bs_proxy.ps1
```

## Why this copy has no dollars

Track 100 ops_stack_5k_trades (filter ON + C_ratchet) was not in this checkout. No entry list was invented. Rejected candidates/track100_cloud/paper_book.json: 26 closed, win rate 42.3%. That is the v12A paper ledger, not the filter-on C_ratchet half-equity tape.

## Fills

| Entry | Exit | Traded | Underlying | Reason | Stock $ | Call $ | Debit |
|---|---|---|---|---|---:|---:|---:|
| — | — | — | — | no tape rows | — | — | — |
