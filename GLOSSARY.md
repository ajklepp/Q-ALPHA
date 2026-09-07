# Q-ALPHA / Strategy Lab Glossary

Plain-English guide to the specialized terms used in the Peak Hour Performers dashboard, live-paper stack, research experiments, Strategy Lab archive, and ticker profiler. Each entry has a **definition** (precise) and a **plain-English** note (why it matters).

---

## Trade outcome metrics

### MFE (Maximum Favorable Excursion)
**Definition:** The best (highest) price reached in your favor after entry, expressed as a percent of the entry price, before the trade is fully flat. Dashboard “Ran up” uses similar peak math; for a missed name it starts from the signal reference price rather than a filled entry.  
**Plain English:** How high the stock ran from the relevant starting price. Even a losing or missed trade can show a large peak move.

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

### Equity / mark-to-market (MTM)
**Definition:** Equity is current cash plus the market value of open positions. MTM P&L reprices open shares at the latest available quote; it is unrealized until the shares are sold.  
**Plain English:** What the paper account is worth right now, including trades that are still moving.

### Hit 1R / hit 2R
**Definition:** A trade hits 1R or 2R when its favorable move reaches one or two times its initial stop risk. Peak Hour uses +1R to change stop behavior; Option D research labels success at +2R before the stop. EXP-0021’s research-only `hit_1r` label specifically tests +5% before −5% from the signal close and is not the dashboard win rate.  
**Plain English:** 1R means the stock moved up by the amount originally risked; 2R means it moved up twice that amount. A research hit does not necessarily mean a closed paper trade made money.

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

### TSD profile / analog context
**Definition:** Current Peak Hour profile built from historical TSD launch analogs, used as soft ranking/risk context and, when valid, to suggest the broker kill distance. Analog count and win rate do not hard-veto an entry. This is separate from the broader dashboard ticker profile and archived Strategy Lab profile statistics.  
**Plain English:** The live strategy’s ticker-specific behavior card. It can inform rank and risk, but weak history does not automatically reject a valid launch.

### Ticker prior / path prior
**Definition:** Historical follow-through estimate used as a soft continuation-ranking term. With at least three comparable completed 1-hour signals, the system uses path history; otherwise it falls back to TSD profile analog context.  
**Plain English:** A small ranking nudge based on how similar signals previously behaved for that ticker.

---

## Live selection and entry models

### Peak Hour Performers
**Definition:** Q-ALPHA’s primary long-only IBKR paper strategy. It scans a daily HTF-qualified universe after completed 1-hour bars, ranks continuation candidates, and can admit at most two new names per hourly scan while capacity remains.  
**Plain English:** The live-paper system looks for already-strong stocks that appear ready to continue, then buys the best available candidates rather than every signal.

### Peak hours
**Definition:** Hours 07, 11, 12, and 13 ET receive a continuation-score bonus. They are preferences, not the only tradable hours; completed 1-hour bars from hours 05–15 ET are eligible.  
**Plain English:** The historically strongest times get extra ranking weight, but a good setup outside those four hours can still be selected.

### TSD signal engine
**Definition:** WaveTrend, trend, money-flow, and volume signal logic inherited from the older 3-hour TSD research track. Peak Hour applies its BUY-cross and `early_bull` logic to completed 1-hour bars; the legacy 3-hour scan is not the live trigger.  
**Plain English:** The technical engine came from the older system, but the current strategy makes its entry decisions on completed hourly candles.

### HTF (Higher Time Frame) universe
**Definition:** Daily pre-filter requiring a 20-day range of at least 25%, close above SMA50, rising SMA20, and price of at least $5. Passing names receive a continuous HTF rank score for ordering.  
**Plain English:** The daily-chart quality screen. Only stocks with enough movement and an established uptrend reach the hourly launch scan.

### 1H LAUNCH scan
**Definition:** Evaluation performed at `:15` after each completed 1-hour bar during the configured 05:00–15:00 ET hours. It applies launch, quality, capacity, and deduplication checks before queueing or buying candidates.  
**Plain English:** The hourly decision point where the system asks, “Is this move early and healthy enough to buy now?”

### LAUNCH vs EXTENSION
**Definition:** LAUNCH describes an earlier continuation setup, NEUTRAL is neither early nor clearly stretched, and EXTENSION describes a move that may already be chased. Moderate extension is score-demoted; scan score at least 75 or an `extended` bar state is hard-blocked.  
**Plain English:** LAUNCH means there may still be room; EXTENSION means the stock may have already made too much of its move.

### Continuation score (v1.4)
**Definition:** The current live ranking score used to order eligible Peak Hour candidates for limited slots. It combines prior path quality, HTF context, bar state, peak-hour preference, relative strength, gap penalties, and an earnings soft boost. It ranks candidates; it is not a predicted probability.  
**Plain English:** The tie-breaker that decides which valid setup gets scarce account space. Higher means preferred relative to the other candidates in that scan.

### Relative strength (RS) vs SPY / sector
**Definition:** A stock’s recent return minus the return of SPY or its sector ETF over the same window. Continuation v1.4 uses leadership or lag as a soft score adjustment, not a standalone entry rule.  
**Plain English:** Whether the stock is outrunning the broad market and its peers—or falling behind them.

### Scan score vs launch score
**Definition:** `scan_score` summarizes the underlying wave/trend/money-flow/volume signal. `launch_score` rates how early and trigger-ready the setup looks. The continuation score is the final relative ranking used for scarce slots.  
**Plain English:** Scan score describes technical strength, launch score asks “early enough?”, and continuation score decides priority.

### Bar state (orange / red / yellow / green / extended)
**Definition:** Shape classification of the signal candle. Orange is doji/coiled, red is a down candle, yellow is a weaker green candle, green is a stronger green candle, and extended is already stretched. Color adjusts ranking; color alone does not veto an entry.  
**Plain English:** A compact description of what the trigger candle looked like—not a standalone BUY/SELL light.

### Watch queue / launch status
**Definition:** Decision-time staging record for ranked names. RANKED passed launch eligibility; TAKE is one of the top two selected for admission; QUEUED passed into entry handling but is not proof of a fill; ENTERED/TAKEN means IBKR confirmed a fill; SKIP means no entry. A later-ranked name is not automatically promoted when a top-two name fails downstream.  
**Plain English:** The waiting room between “interesting setup” and “filled BUY,” including exactly how far a candidate advanced.

### Hard gate vs soft score
**Definition:** A hard gate rejects a candidate when it fails. A soft score term only moves the candidate up or down the ranking. Peak hours, bar color, relative strength, most catalyst/news fields, and moderate extension are soft; instrument safety, core HTF/launch requirements, severe extension, deduplication, and capacity are hard.  
**Plain English:** Some rules decide “allowed or not”; others decide “which allowed setup do we prefer?”

### Trade thesis / frozen evidence
**Definition:** Plain-English explanation assembled from information available at decision time and stored with taken or missed candidates. It is frozen so later price action cannot rewrite why the system acted.  
**Plain English:** A timestamped “why this looked interesting then” card, not a story invented after seeing the outcome.

The entry models below are primarily Strategy Lab/research terminology, not the current Peak Hour entry trigger.

### immediate
**Definition:** Archived Strategy Lab model that enters at the close of the first one-minute bar at or after 09:30 ET, with no reclaim filter.  
**Plain English:** Buy at the end of the first regular-session minute. Fastest Lab entry; no extra confirmation that the open was “good.”

### orb_reclaim
**Definition:** Archived Strategy Lab model that forms the first-N-minute opening range, then enters on the first subsequent one-minute close above the range high. A prior dip below the range is not required.  
**Plain English:** Wait for the opening box to finish, then buy a confirmed candle close above its high.

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
**Definition:** The primary U.S. equity session, 09:30–16:00 Eastern. Peak Hour generally uses market orders during RTH and outside-RTH-capable limit orders in extended sessions.  
**Plain English:** Normal stock-market hours. Order handling changes outside this window because ordinary market orders are not available in the same way.

---

## Exit strategies

### Three-layer protection (Kill / BE / Trail)
**Definition:** Peak Hour keeps a broker kill stop active while shares remain, arms a near-breakeven structure lock only after +1R, and manages T1–T4 with software trailing logic.  
**Plain English:** First survive with a hard emergency stop; after the trade proves itself, protect near breakeven; then let profit-taking trails manage the run.

### Breakeven (BE) lock
**Definition:** After price first touches +1R, the structure layer can ratchet near entry (currently about 0.3% below entry). It does not arm merely because the opening range formed.  
**Plain English:** Once the stock has moved enough in your favor, the system stops giving it the full original risk.

### Idle no-1R / day-6 flatten
**Definition:** A Peak Hour position that has never reached +1R and is not actively trailing is flattened on or after trading day 6.  
**Plain English:** If the trade has done nothing useful for nearly a week, free the capital instead of waiting indefinitely.

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
**Definition:** Peak Hour broker-side GTC stop-limit SELL covering all remaining shares. It uses profile MAE p75 only when the distance is between 2% and 6%; otherwise it uses the 5% fallback. Its quantity shrinks after partial exits, and the deeper broker kill remains active after the +1R breakeven lock arms.  
**Plain English:** The “thesis is dead” emergency exit. It always protects the shares still open, even after closer software protection becomes active.

### Trailing stop (ratchet) vs price target
**Definition:** A *trail* moves the stop up as price makes new highs (ratchet = never loosens); a *target* is a fixed sell price for a tranche.  
**Plain English:** Trail = protect gains as it runs. Target = “I’m happy to sell this piece at $X.”

### Runner
**Definition:** The last tranche (often T4 / 10%) left on a trail with no hard upside cap. A Peak Hour position with only T4 remaining stays managed but no longer consumes a full capacity slot.  
**Plain English:** The small leftover position kept for a large trend without blocking the account from taking another full trade.

### Time-cap / max-hold
**Definition:** Forced exit after a maximum number of trading days. Peak Hour can flatten an idle never-1R position from day 6 and retains Strategy A’s maximum hold around 20 trading days.  
**Plain English:** “If it hasn’t worked by then, we’re out.” Dead trades leave earlier; even working trades do not stay forever.

### Exit reasons (`trail` / `target` / `kill` / `time_cap`)
**Definition:** Labels recording why each tranche or leg closed. Current Peak Hour layers include broker Kill, breakeven/Structure, 3-hour base breakdown, software Trail, idle-no-1R, and maximum-hold exits; archived Strategy Lab results may also show fixed Target.  
**Plain English:** A scoreboard of *how* the shares got out—not just whether the trade won or lost.

### Leg / add-on
**Definition:** Each confirmed entry fill is stored and trailed as a separate leg. A Peak Hour symbol may have one initial entry plus up to two add-ons, so the Trade Log counts completed legs rather than always counting unique ticker campaigns.  
**Plain English:** Buying the same trend again creates another separately managed slice with its own entry and exit history.

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

### Option D target
**Definition:** Research label equal to 1 only when price reaches entry plus 2× initial ATR stop risk before the stop is hit, within five trading days; stop-first or timeout is 0.  
**Plain English:** A strict “did it reach 2R before failing or running out of time?” test—not simply whether the stock finished higher later.

### Temporal split / walk-forward validation
**Definition:** Models are trained and tested in chronological order, never with randomly shuffled market history. Walk-forward validation repeats this across four forward-moving windows to test different periods.  
**Plain English:** Learn from the past, then test on the future—several times—without leaking tomorrow into yesterday.

### Sharpe ratio
**Definition:** Risk-adjusted return, calculated as average strategy return divided by return volatility and annualized under the experiment’s convention. Q-ALPHA’s current experiment pass threshold is at least 1.5.  
**Plain English:** Reward earned per unit of return variability. Higher is better, but it must be supported by enough trades and stable validation.

### Monte Carlo p-value
**Definition:** Significance test using 5,000 simulations to estimate how often randomized outcomes match or beat the observed Sharpe. The current pass gate is `p < 0.05`.  
**Plain English:** A check on whether the result looks meaningfully better than luck; below 5% is the required bar.

### Precision@0.60 / base-rate lift
**Definition:** Among rows where LightGBM assigns probability at least 0.60, precision is the share that truly hit the Option D target. Lift compares that precision with the unconditional positive rate.  
**Plain English:** When the model says “high confidence,” how often is it right—and is that better than choosing a setup without the model?

### Expander capture / slot capture
**Definition:** Share of the strongest same-day continuation opportunities that a limited-slot ranking policy selected. Used in continuation-ranker research alongside expectancy so quality is not improved by simply missing major runners.  
**Plain English:** Of the day’s biggest available movers, how many did the ranking actually put into the scarce slots?

### Missed-move ledger
**Definition:** Weekly-review record of ranked Peak Hour candidates that were not filled, preserving decision-time evidence and later measuring how far they ran from the reference price.  
**Plain English:** The system’s receipt for opportunities it passed over, used to study whether slot selection missed important runners.

### Ship gate / HOLD / FAIL
**Definition:** Predeclared evidence threshold for promoting a research change. PASS supports the frozen gate; FAIL misses it; INVESTIGATE/PROMISING means replicate before production; HOLD/HOLD_DIAGNOSTIC retains information without wiring it live. Only explicit shipped/live wording describes production behavior.  
**Plain English:** A rule decided before seeing results that prevents “interesting” from quietly turning into “live.”

### Research diagnostics (Hurst / Hill alpha / volatility clustering)
**Definition:** Hurst estimates persistence versus mean reversion; Hill alpha estimates tail thickness; short-interval volatility clustering/expansion asks whether movement is intensifying or fading. EXP-0024/0025 found useful diagnostics but no rule strong enough to add to live continuation v1.4.  
**Plain English:** These measurements describe whether moves persist, how extreme returns can be, and whether the tape is alive—but they currently observe the strategy rather than control it.

---

## System

### SIM / Polygon paper vs IBKR paper vs live
**Definition:** *SIM / Polygon paper* = research using Polygon data and fake money with no broker. *IBKR paper* = broker-connected Peak Hour paper account. *Live* = real capital. Strategy Lab is currently mothballed/archived.  
**Plain English:** Three different worlds: historical simulation, broker paper trading, and real-money trading. Current Peak Hour operation is IBKR paper.

### Pool
**Definition:** Deployable paper cash tracked separately from the market value of open positions. Peak Hour starts with one $3,000 TSD pool; the archived Strategy Lab kept separate $3,000 A/B pools.  
**Plain English:** The strategy’s available bankroll. Buying reduces cash and selling returns proceeds; cash plus open-position value equals current equity.

### Regime (BULL / BEAR)
**Definition:** Market-environment label. Live Status displays the SPY HMM regime; older agent paths and historical Lab rows may still use SPY vs SMA50.  
**Plain English:** “Risk-on” vs “risk-off” backdrop. Momentum systems often behave differently in each.

### SPY HMM
**Definition:** The Live Status regime model from EXP-0023. It fits a two-state Gaussian Hidden Markov Model to recent daily SPY returns, calls the higher-return state “bull,” and displays **BULL** when its filtered bull probability is at least 55%; otherwise it displays **BEAR**. It is retained as research/dashboard context and does not change the continuation score, entry gates, or position sizing.  
**Plain English:** A probability model that estimates whether SPY currently behaves more like its stronger or weaker market state. It replaces the old SMA50 regime label on the dashboard without changing which Peak Hour trades are taken.

### VIX
**Definition:** The actual VIX is CBOE’s near-term S&P 500 implied-volatility index. The Live Status `VIX: NORMAL/ELEVATED` badge currently uses an annualized SPY realized-volatility proxy, not a direct VIX quote.  
**Plain English:** A market-stress indicator. On this dashboard it is an approximation from recent SPY movement rather than the official “fear gauge” feed.

### Dynamic slots / slot ladder
**Definition:** Concurrent full-position capacity. Peak Hour uses a dynamic slot ladder from 2 to 10: the $300 unit size stays fixed while slot count grows, then unit size grows once 10 slots are available. A T4-only runner no longer consumes a full slot.  
**Plain English:** Account equity determines how many full seats exist. The system grows the number of seats before making each seat larger.

### Cross-book deduplication
**Definition:** Safety check preventing a new Peak Hour BUY when the same symbol is already open in the TSD book or legacy gap-agent runoff book.  
**Plain English:** Do not accidentally own the same stock twice through two different system paths.

### TWS / IBKR
**Definition:** Trader Workstation (TWS) is the Interactive Brokers application/API used for paper orders, broker positions, quotes, and reconciliation. IBKR is the broker; TWS is the connection used by the runners.  
**Plain English:** Polygon supplies research/scan data; TWS is the broker-side doorway that actually places and verifies paper trades.

### Source of Truth (SoT)
**Definition:** The authoritative record when multiple copies exist. For live positions and fills, IBKR/TWS is authoritative; local TSD state manages strategy logic; Supabase mirrors state for the dashboard.  
**Plain English:** When screens disagree, the source of truth is the record we trust first.

### Local fallback — Supabase lag
**Definition:** Dashboard label indicating that local queue/book state is being shown because the Supabase mirror is unavailable or behind. It does not mean the broker position changed.  
**Plain English:** The trading machine has fresher information than the cloud dashboard copy.

### Mark vs settle
**Definition:** Archived Strategy Lab operations: mark refreshes prices and unrealized state during a run; settle performs the main end-of-day reconciliation of exits and completed outcomes. Both are designed to be repeatable without duplicating actions.  
**Plain English:** Mark updates the scoreboard; settle closes the books on what actually happened that day.

### Legacy runoff
**Definition:** Residual positions from the disabled gap agent that may still appear on Live Status only so they can be monitored and closed. The path cannot open intentional new positions.  
**Plain English:** Old trades are allowed to finish safely, but the retired strategy is not taking new ones.

### Long-only
**Definition:** The system opens positions with BUY orders only. SELL orders may reduce or close an existing long; covering an accidental broker short is cleanup, not a strategy.  
**Plain English:** Q-ALPHA bets on stocks rising and never intentionally sells short.

### sweep_reclaim quality tag
**Definition:** Pass/fail (or timing) annotation of whether a sweep-reclaim pattern occurred, even when the entry model is not `sweep_reclaim`.  
**Plain English:** A sticky note on the trade: “this open also had a failed-breakdown flavor” — useful for later edge studies, not always the trigger itself.

### Supabase sync (anon vs service key)
**Definition:** Cloud Postgres mirror for TSD positions, pool snapshots, launch boards, closed legs, missed moves, and health. Runners and the current Live Status client use the configured service/secret credential; TSD tables also support read-only client patterns where configured. Supabase is not the broker source of truth.  
**Plain English:** The trading machine saves dashboard-ready copies in the cloud, while IBKR remains the final authority for real paper-account fills and positions.

### Polygon.io
**Definition:** Market-data REST API used for the HTF universe, hourly bars, profiles, SPY HMM inputs, and research experiments. Requests use `POLYGON_API_KEY` and retry/rate-limit handling.  
**Plain English:** The primary source of historical and scan price/volume data; IBKR/TWS remains authoritative for broker fills and positions.

### LightGBM
**Definition:** Gradient-boosted tree classifier required for newer model experiments to predict whether a setup hits the Option D target. It is not the heuristic continuation v1.4 ranker used by Peak Hour live paper.  
**Plain English:** The research ML model asks whether a setup may reach 2R; it does not currently choose Peak Hour entries.

### Cost per trade / COST_PER_TRADE
**Definition:** Required 0.15% assumed trade friction in Q-ALPHA experiments. It is a backtest contract, not an extra deduction applied to Peak Hour’s broker-paper pool accounting.  
**Plain English:** Simulated fees keep research results from looking unrealistically clean; paper fills are recorded from the broker instead.

### Bracket / BracketPosition
**Definition:** Sacred multi-slice kill-and-trailing implementation retained by the legacy gap agent and experiment baseline. Peak Hour TSD uses its own corresponding broker-kill, structure, and four-tranche software-trail state.  
**Plain English:** The established four-piece exit machinery is not casually rewritten, but the current Peak Hour book and the older gap-agent book store that machinery differently.

---

*Informational only. Peak Hour Performers currently runs in IBKR paper; Strategy Lab is archived SIM; neither uses real money.*
