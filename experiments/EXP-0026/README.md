# EXP-0026 — ITM long calls (research)

Long calls on liquid underlyings. Parallel to Track 100 equity: this study is not joined to that book, and it does not change Peak Hour or Track 100 paper defaults. No live orders.

**Status of this copy:** needs laptop Modal. The cloud checkout cannot see `POLYGON_API_KEY` or a Modal token, so option bars were not fetched and there is no P&L. A laptop run either fills `results.md` from real option bars or replaces it with `NOT_RUN` and still leaves P&L empty.

## Why this structure

A 100-share stock seat can lose far more than the planned stop if price gaps. One long call controls those 100 shares, and the most the call can lose is the debit paid. The research question is whether that bound is usable on a **$5,000** book for SPY / QQQ / IWM / NVDA / TSLA-class names — including how often one contract simply does not fit in the book.

## Rules

**Side.** Buy to open a call. Sell only to close that call. No puts, no short calls, no credit spreads.

**Depth.** 5% to 12% in the money, closest to **8%** (`strike ≈ prior close × 0.92`). On liquid 21–45 day calls this is the depth people usually mean by about **0.70–0.80 delta**. The coded rule is the percent in the money, not a delta print. Historical option bars from Polygon do not include delta, and this study does not invent a Black-Scholes delta or treat it as the broker's. Live greeks are not applied backwards onto old dates.

**Expiry.** 21 to 45 calendar days at entry. If 7 calendar days or fewer remain, close on that session's option close.

**When a new call is allowed.** All of this is known before the entry open:

- SPY's prior close is at or above its 50-day average. The average excludes that prior close. If there is not enough SPY history, there is no trade (this does not call `get_regime()` and it does not default to bull).
- The name's prior close has just crossed above its 20-day average. Each average excludes the close being tested.
- The prior session is not a split-sized jump in the unadjusted close.

**Entry print.** The call's daily open on the signal session. If that open is missing, the signal is a skip. The stock open that same session is the underlying price the stop is measured from. The strike is chosen from the prior close.

**Sizing versus $5,000.** One contract (multiplier 100) or nothing. Debit = option open × 100. If the debit is above $5,000, skip it and count `premium_exceeds_book`. There is no fractional contract and no mini-option. Cash from a close is available the next session, not the same morning. When two names signal the same morning, earlier names in `SPY, QQQ, IWM, NVDA, TSLA, AAPL, MSFT, AMZN, META, AMD` get the cash first.

**Stop / trail analogue.** This is not the equity `BracketPosition` (that 4-slice trailer stays in EXP-0012). One call is one ticket.

- R = 1 × ATR(14) of sessions before the entry.
- Initial underlying stop = stock entry open − R.
- Target = stock entry open + 2R.
- If a session's high tags +1R and the trade is still open, the **next** session's stop moves up to the stock entry open. It does not move on the same bar.
- If the underlying low tags the stop, the call is closed at **that session's option close**.
- If the stop and the +2R target both print in the same underlying bar, the trade is ambiguous and is dropped. It is not given a P&L.
- If the option close is at or below half the entry premium, close it (premium stop).
- If none of those hit, close on the fifth session.

**Exit.** Those rules above, whichever comes first. The recorded exit is the option daily close. That is a bar proxy for the moment the stock touched the stop, not a tick fill and not a broker fill. Gross P&L is (exit close − entry open) × 100. The equity `COST_PER_TRADE` of 0.0015 is not subtracted, because that figure is a stock assumption and this study will not invent an option spread or commission.

**What “bounded” means in the output.** Each closed trade stores the debit (max loss on the call) and the 100-share stock notional (the seat the call stands in for). A gap through the stock stop cannot make the call loss larger than the debit, as long as the option close is ≥ 0. If a computed loss ever exceeded the debit, the run discards the results instead of publishing them.

**Walk-forward.** Four forward windows: 2023, 2024 H1, 2024 H2, 2025. A window passes only with at least 5 allocated trades, Sharpe ≥ 1.5, a positive return, and max drawdown not worse than −15%. The study passes only if at least 3 of 4 windows pass, plus the full-sample gates (Sharpe ≥ 1.5, drawdown, positive return, beats SPY close-to-close, Monte Carlo p < 0.05 on 5,000 bootstrap draws). There is no LightGBM in this study; the precision gate is N/A and is not counted as a pass. Failures stay labeled **FAIL**.

## How to run (laptop)

From the repo root. Needs the Modal secrets `polygon-api-key` (`POLYGON_API_KEY`) and `q-alpha-secrets` (attached so this matches other Q-ALPHA studies; the function does not read it and does not write Supabase or send orders).

```powershell
Set-Location C:\Users\ajkle\Documents\Q-ALPHA
.\venv\Scripts\python.exe -m modal run experiments/EXP-0026/study_itm_long_call_modal.py
```

The same command is in `run_on_laptop.ps1`. The script then runs the bridge probe below. `results.md` and `study_itm_long_call_metrics.json` are rewritten on the laptop.

Underlying prices are unadjusted, so strikes match the prices that traded. A split-sized jump is skipped rather than treated as a signal.

## Polygon options limits

Stocks daily bars are the same Polygon path as the other Q-ALPHA Modal studies (`polygon-api-key`).

Option discovery is `GET /v3/reference/options/contracts` (call, expired, `as_of` the entry date when the API accepts it). Option prices are `GET /v2/aggs/ticker/O:…/range/1/day/…` with `adjusted=false`. Those routes need an **options** entitlement. HTTP 401/403 or a `NOT_AUTHORIZED` body stops the study. EXP-0021 recorded Polygon options snapshots as not entitled on 2026-09-06. This runner checks again; it does not assume the plan is unchanged, and it does not replace a refused option bar with a stock return.

The probe asks for one June 2024 SPY call and that month's daily bars. Fewer than 3 bars, an empty contract list, or a refusal becomes `NOT_RUN`. A refusal later in the loop discards any partial trades so a half sample is not published as the result.

There is a 0.12s pause between Polygon requests, with retries on rate limit and server errors.

## TWS bridge (optional, not the backtest)

| | |
|---|---|
| Bridge | `http://127.0.0.1:8787` (loopback only) |
| TWS paper | `127.0.0.1:7497`, clientId **71** |
| Start | `.\candidates\start_options_bridge.ps1` |
| History route | `GET /v1/options/hist` defaults to about **10 D** of **1 hour** MIDPOINT bars |

Modal runs in the cloud and cannot open the laptop's `127.0.0.1:8787`. The bridge is read-only: order routes return 403/405. Ten days of hourly mids cannot fill the 2023–2025 windows, so the probe does not build P&L.

```powershell
Set-Location C:\Users\ajkle\Documents\Q-ALPHA
.\candidates\start_options_bridge.ps1
.\venv\Scripts\python.exe experiments\EXP-0026\probe_options_bridge.py
```

That writes `bridge_probe.json` (`pnl_usd` null). It is a local note, not a result.
