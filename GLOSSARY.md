# Q-ALPHA / Strategy Lab Glossary

Plain-English guide to the specialized terms used in the dashboard, Strategy Lab, and ticker profiler. Each entry has a **definition** (precise) and a **plain-English** note (why it matters).

---

## Trade outcome metrics

### MFE (Maximum Favorable Excursion)
**Definition:** The best (highest) price reached in your favor after entry, expressed as a percent of the entry price, before the trade is fully flat.  
**Plain English:** How high the stock ran *for* you at its peak. Even a losing trade can have a big MFE if it spiked up and then fell — the profiler cares about that peak because it is what “room to the upside” looked like.

### MAE (Maximum Adverse Excursion)
**Definition:** The worst (lowest) price against you after entry, as a percent of entry, before the trade is fully flat.  
**Plain English:** How deep the stock dipped *against* you at its worst. Used to size stops and trails so you are not stopped out by normal noise.

### R:R / Reward:Risk
**Definition:** Ratio of expected upside (often a target or MFE percentile) to downside risk (often `safe_max_stop_pct`).  
**Plain English:** “How many dollars of potential reward for each dollar you risk.” Higher is better, but only if the numbers are based on enough history.

### Win rate
**Definition:** Share of closed trades with positive P&L (wins ÷ trades taken).  
**Plain English:** How often you are right. A high win rate with tiny wins and big losses can still lose money — look at expectancy and R-multiples too.

### R-multiple / R (risk unit)
**Definition:** Profit or loss measured in units of initial risk (1R = dollars risked to the stop on that trade). Hitting “2R” means making twice what you risked.  
**Plain English:** A ruler for trades of different sizes. “+2R” means “made twice my planned risk,” whether the account risked $30 or $300.

### P&L (Profit & Loss) — realized vs unrealized
**Definition:** *Realized* P&L is locked in after exits; *unrealized* is mark-to-market while the position is still open.  
**Plain English:** Realized = money already booked. Unrealized = what you’d get if you closed *right now* (it can still change).

### Drawdown / Max drawdown
**Definition:** Peak-to-trough decline in equity (or pool value); max drawdown is the worst such decline over the period.  
**Plain English:** How far the account fell from its high-water mark. Painful but important — two strategies with the same return can feel very different if one dips harder.

### Expectancy / avg return per setup
**Definition:** Average P&L (or return %) per trade/setup over a sample; roughly (win% × avg win) − (loss% × avg loss).  
**Plain English:** What you expect to make *on average* each time you take a setup. Positive expectancy is the long-run engine; win rate alone is not.

---

## Profile / statistics

### Percentiles (p25 / p50 / p75 / p90)
**Definition:** Points on the distribution of analog outcomes: p50 is the median (half of analogs below, half above); p75/p90 are higher (more optimistic) cut-points.  
**Plain English:** “Typical” vs “good” vs “great” days from history. **p50 = median** — the middle outcome, not the average (averages get pulled by a few huge days).

### Confidence tiers (HIGH / MEDIUM / LOW / INSUFFICIENT)
**Definition:** Profiler label for how trustworthy the analog sample is (sample size / history quality). INSUFFICIENT means stats are not meaningful for live risk.  
**Plain English:** How much you should trust the profile numbers. HIGH ≈ solid sample; INSUFFICIENT ≈ “interesting chart, but don’t bet the farm on these percentiles.”

### Analogs / analog days
**Definition:** Past sessions for the same (or similar) ticker that look like today’s setup; the profiler measures MFE/MAE on those days.  
**Plain English:** “Days like this one in the past.” More good analogs → more credible stop/target guesses.

### safe_max_stop_pct
**Definition:** Profiler-suggested hard-stop distance (fraction/percent of entry) used as the kill-all level when confidence is adequate.  
**Plain English:** “How far the stock can go against you before we admit the thesis is wrong and exit everything.”

### Lookback days
**Definition:** How far back in calendar/trading history the analog finder searches.  
**Plain English:** The window of past data we are allowed to learn from. Longer lookback finds more analogs but can mix in very different market eras.

### History flags (`*` limited, `**` insufficient)
**Definition:** UI markers on tickers: `*` = limited history / small sample or extended past the preferred window; `**` = insufficient — informational only.  
**Plain English:** Asterisks next to a ticker name saying “thin data” (`*`) or “don’t treat these stats as tradeable” (`**`).

---

## Entry models

### immediate
**Definition:** Enter at/near the first regular-session bar (typically 09:30 ET open/close of that bar) with no reclaim filter.  
**Plain English:** Buy the open of the regular session. Fastest entry; no extra confirmation that the open was “good.”

### orb_reclaim
**Definition:** Enter after price reclaims the Opening Range (often first 5 minutes) after dipping into/below it.  
**Plain English:** Wait for an early dip through the opening range, then buy when price climbs back above it — confirmation that buyers returned.

### vwap_reclaim
**Definition:** Enter after price dips below VWAP and then reclaims (closes/trades back above) VWAP.  
**Plain English:** Buy when the stock falls under the “fair average” price for the day and then gets back above it.

### sweep_reclaim (failed-breakdown reversal)
**Definition:** Enter after a liquidity sweep / breakdown below a key low that quickly fails and reclaims that level. In Strategy Lab it is also used as a **quality tag**, not always the live entry model.  
**Plain English:** The stock briefly breaks a support level (shaking out weak hands), then snaps back above it — classic failed-breakdown. Lab tags whether that pattern would have fired even when entry is `immediate`.

### premarket_median_limit
**Definition:** Place a limit at the premarket median trade price; fill only if RTH trades through that limit.  
**Plain English:** Try to buy at the “middle” of the premarket print, not chase the open. You may get no fill.

### premarket_vwap_limit
**Definition:** Same idea using premarket VWAP as the limit price.  
**Plain English:** Aim to buy at the volume-weighted premarket average. Again, fill is not guaranteed.

### VWAP (Volume-Weighted Average Price)
**Definition:** Average price weighted by volume over a session (or premarket window).  
**Plain English:** The volume-aware “fair” price so far. Institutions often care whether price is above or below VWAP.

### ORB (Opening Range Breakout)
**Definition:** The high/low of the first N minutes of RTH (commonly 5); breakouts/reclaims of that range are ORB-style setups.  
**Plain English:** The box the stock paints in the first few minutes. Breaking out of (or reclaiming) that box is a classic day-trade pattern.

### Gap % / gapper
**Definition:** Percent difference between prior close and today’s open (or premarket reference); a “gapper” is a name with a large gap.  
**Plain English:** How much the stock jumped overnight. Q-ALPHA hunts catalyst gappers in the small/mid-cap universe.

### Premarket
**Definition:** Trading activity before 09:30 ET (extended hours).  
**Plain English:** The warm-up session before the official open — where gaps and early volume show up.

### RTH (Regular Trading Hours)
**Definition:** The primary U.S. equity session, 09:30–16:00 Eastern.  
**Plain English:** Normal stock-market hours. Most Strategy Lab entries and exits assume RTH bars.

---

## Exit strategies

### Strategy A (Trailing)
**Definition:** Scale-out with per-tranche trailing stops that ratchet up as MFE percentiles are hit; includes a runner tranche.  
**Plain English:** Take some profit in stages and let stops chase price up. Designed to keep a piece for big runners.

### Strategy B (Target)
**Definition:** Scale-out toward fixed/profile targets rather than (primarily) trailing; still has kill and time rules.  
**Plain English:** Aim for predefined profit targets. Lab runs A and B on the **same** entry to compare exits.

### Tranche / scale-out (40 / 30 / 20 / 10)
**Definition:** Split the position into four slices (T1–T4) by share weight ~40%, 30%, 20%, 10% (auto-collapsed if share count is tiny).  
**Plain English:** Don’t exit all at once. Sell chunks as the trade works so early profit is banked while a runner can continue.

### Kill-all / hard stop
**Definition:** Single catastrophic stop that flattens **all** remaining tranches if hit (`safe_max_stop_pct` or fallback ~7%).  
**Plain English:** The “thesis is dead” emergency exit — dump everything, no hoping.

### Trailing stop (ratchet) vs price target
**Definition:** A *trail* moves the stop up as price makes new highs (ratchet = never loosens); a *target* is a fixed sell price for a tranche.  
**Plain English:** Trail = protect gains as it runs. Target = “I’m happy to sell this piece at $X.”

### Runner
**Definition:** The last tranche (often T4 / 10%) left on a trail with no hard upside cap, held for extended trend.  
**Plain English:** The small leftover position you leave on in case the stock goes parabolic.

### Time-cap / max-hold
**Definition:** Forced exit after a maximum number of trading days (Strategy A uses a hold-day cap, e.g. ~20).  
**Plain English:** “If it hasn’t worked by then, we’re out.” Stops capital from sitting forever in a dead trade.

### Exit reasons (`trail` / `target` / `kill` / `time_cap`)
**Definition:** Counted labels for why each tranche closed.  
**Plain English:** A scoreboard of *how* you got out — trailed for profit, hit a target, stopped out hard, or hit the calendar limit.

---

## Validation / stats

### In-sample vs Out-of-sample (OOS)
**Definition:** *In-sample* = data used to fit/choose the model or split; *out-of-sample* = held-aside data never used for that fit, used only to test.  
**Plain English:** Studying for the test vs taking a brand-new test. Looking good on the study set is easy; looking good on new data is the real exam.

### Out-of-Sample R²
**Definition:** Coefficient of determination between predicted values (e.g. profiler MFE p50) and actual outcomes on OOS (or live forward) pairs. Not clamped — can be negative.  
**Plain English:** “How much of the real outcomes does our prediction explain on data we didn’t cheat with?” **1.0** = perfect; **0** = no better than predicting the average every time; **negative** = *worse* than just guessing the average MFE. Strategy Lab also shows a **live rolling** forward OOS R² as new trades close.

### Overfitting / data-mining bias
**Definition:** Fitting noise or hunting many patterns until something “works” on past data but fails on new data.  
**Plain English:** Memorizing the practice quiz. The backtest looks amazing; live trading disappoints. Comparing backtest vs forward R² (once N is large) helps spot this.

### Sample size (N)
**Definition:** Number of independent observations (completed prediction pairs, trades, analogs, etc.).  
**Plain English:** How many examples you have. Two lucky (or unlucky) trades can make any statistic look crazy.

### MIN_N gating (dashboard uses 20)
**Definition:** UI rule: do not display forward OOS R² or gap “holding/overfit” verdicts until forward (and for gap, backtest) N ≥ 20.  
**Plain English:** We hide scary tiny-N R² numbers (like −6 at N=2). Until ~20 completed pairs, the dashboard says “collecting data” because small samples are mostly noise.

---

## System

### SIM / Polygon paper vs IBKR paper vs live
**Definition:** *SIM / Polygon paper* = Strategy Lab and research using Polygon data and fake money (no broker). *IBKR paper* = Interactive Brokers paper account. *Live* = real capital.  
**Plain English:** Three different “worlds.” Strategy Lab is explicitly **not** the IBKR agent — it’s a sandbox comparing exits on Polygon bars.

### Pool
**Definition:** Simulated cash sleeve for a strategy (Lab: $3,000 each for A and B) that grows/shrinks with closed P&L.  
**Plain English:** Each strategy’s play-money bankroll. Same starting size so A vs B is a fair fight.

### Regime (BULL / BEAR)
**Definition:** Market-environment label (agent uses SPY vs SMA50-style logic; Lab may tag regimes on historical rows).  
**Plain English:** “Risk-on” vs “risk-off” backdrop. Momentum systems often behave differently in each.

### VIX
**Definition:** CBOE Volatility Index — implied volatility of near-term S&P 500 options.  
**Plain English:** The market’s “fear gauge.” High VIX ≈ nervous markets; the live dashboard may show it for context/sizing.

### Slots (x / 10)
**Definition:** Concurrent open-position capacity; Lab and agent cap at `MAX_SLOTS = 10` per pool/book.  
**Plain English:** How many trades can be open at once. “3/10” means three seats filled, seven free.

### sweep_reclaim quality tag
**Definition:** Pass/fail (or timing) annotation of whether a sweep-reclaim pattern occurred, even when the entry model is not `sweep_reclaim`.  
**Plain English:** A sticky note on the trade: “this open also had a failed-breakdown flavor” — useful for later edge studies, not always the trigger itself.

### Supabase sync (anon vs service key)
**Definition:** Cloud Postgres sync: *service* (secret) key writes from runners; *anon* (publishable) key is what the Strategy Lab dashboard tab uses to **read** forward state on Streamlit Cloud.  
**Plain English:** Two passwords with different powers. The overnight runner uses the powerful key to save state; the public dashboard uses the read-friendly key so Cloud can show Lab results without exposing admin rights.

### Polygon.io
**Definition:** Market-data vendor (REST) used for bars, scans, and profiles; rate-limited in experiments.  
**Plain English:** Where price/volume history comes from for research and Strategy Lab paper.

### LightGBM
**Definition:** Gradient-boosted tree classifier used in newer Q-ALPHA experiments to predict whether a trade hits 2R before stop within the horizon.  
**Plain English:** The ML model that scores “is this setup likely to work?” — separate from the Strategy Lab A/B exit race.

### Cost per trade / COST_PER_TRADE
**Definition:** Assumed round-trip friction (e.g. 0.15% in experiments) subtracted so backtests are not fantasy-clean.  
**Plain English:** Fake fees so paper results don’t ignore spreads and slippage.

### Bracket / BracketPosition
**Definition:** Multi-slice position object with kill + trailing logic (4-slice profile) used by the live agent path.  
**Plain English:** The packaged “enter with stops and scale-outs already defined” structure — treated as sacred in the agent codebase.

---

*Informational only. Strategy Lab is SIM / Polygon paper — not IBKR and not real money unless you are on the live agent path.*
