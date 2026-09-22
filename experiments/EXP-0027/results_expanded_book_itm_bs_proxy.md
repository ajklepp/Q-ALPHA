# MODEL PROXY ONLY — NOT FILLS

Expanded stock P&L n/a vs ITM call proxy n/a on the same 0 closed expanded_half_equity_2 fills. Status: NOT_RUN.

**model_proxy_pnl**. NOT gospel. NOT broker fills. NOT live paper. NOT a new entry rule.

Stock dollars and entry/exit dates are the expanded tape's. The call is one Black–Scholes ~8% ITM contract marked on those same dates. Premiums are not fills.

- Book: `expanded_half_equity_2`
- Tape: `None`
- Closed fills: 0 of 0 rows. Calls priced: 0.
- Stock max DD (exit-order realized, from $5,000): n/a.
- Call max DD (same method, priced calls only): n/a.
- IV: not measured — expanded trade list missing
- Stock bars: `None`.
- Window the study used: 2026-01-16 → 2026-09-16. Fills outside that window: 0.
- Filter expected on the tape: paper_filter_winloss_v1 ON. Exit expected: C_ratchet_struct.
- Runtime: 0.0 seconds.

## Skips

- None.

## Assumptions

- Same fills as the expanded half-equity book. The ops-stack +33% tape is not a fallback.
- ~8% ITM (5–12% band), Friday expiry inside 21–45 DTE, one contract.
- Debit must fit that fill's notional. If the tape omits notional, the seat default is $1,250 (half of $5,000 split across 2 seats).
- Exit mark is the model value on the tape's exit session.
- Percents use the $5,000 account, which is how the expanded study quoted −14.69%. Half-equity percent is also in the JSON.
- Max DD is realized P&L in exit order. It is not a daily mark of open seats.
- European BS, r = 0.04. IV frozen backward. No Polygon option OHLC.
- A summary-only JSON (win rate and P&L, no trade rows) is NOT_RUN. Those headline dollars are not copied.

## Laptop

One command. It points at `C:\Users\ajkle\Documents\Track 100\results`, starts the options bridge only when port 8787 is down, and writes this file again.

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
.\experiments\EXP-0027\run_expanded_book_itm_bs_proxy.ps1
```

If `universe_expand_movers_trades.json` and `.csv` are missing, the proxy has no fills. Re-run the expand study in Track 100 on branch `cursor/universe-expand-movers-3067` (the branch that contains the universe-expand code), then run the command above again:

```powershell
cd C:\Users\ajkle\Documents\Track 100
py -3 -m modal run cloud/universe_expand_modal.py
```

## Why this copy has no option dollars

universe_expand_movers_trades.json / .csv was not in this checkout or under TRACK100_TRADES. The ops-stack +33% tape was not used. No entry list was invented. On the laptop, if those files are missing, run `py -3 -m modal run cloud/universe_expand_modal.py` from Track 100 (branch cursor/universe-expand-movers-3067) and then the EXP-0027 PowerShell command.

## Fills

| Entry | Exit | Traded | Underlying | Book | Stock $ | Call $ | Debit |
|---|---|---|---|---|---:|---:|---:|
| — | — | — | — | — | — | — | — |
