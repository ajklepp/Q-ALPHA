# =============================================================================
# Q-ALPHA AUTONOMOUS AGENT — AI scans, decides, enters, manages (no approval)
# =============================================================================
#
# Runs locally via Windows Task Scheduler at 9:20 AM ET.
# Requires TWS open and logged into paper account (port 7497).
#
# WINDOWS TASK SCHEDULER SETUP (run once):
# 1. Open Task Scheduler → Create Basic Task
# 2. Name: QAlpha Autonomous Agent
# 3. Trigger: Daily at 9:20 AM (weekdays)
# 4. Action: Start a program
#    Program:   C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\venv\Scripts\python.exe
#    Arguments: candidates\autonomous_agent.py
#    Start in:  C:\Users\ajkle\OneDrive\Documents\Q-ALPHA
# 5. Ensure TWS paper is open before 9:20 AM
#
# Timeline:
#   9:20-9:29  Phase 1: IBKR pre-market scan
#   9:29       Phase 2: Pre-market Telegram summary
#   9:30-11:00 Phase 3: Watch candidates + enter trades
#   11:00      Phase 4: Session recap Telegram
#   After      Phase 5: Sync state to Modal volume
# =============================================================================
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, time as dtime
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv
from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopOrder

CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trader import PaperTrade, PaperTradesStore
from position_sizer import PoolManager
from state_paths import CANDIDATES_DIR as _CANDIDATES_DIR, is_trading_day, state_path
from universe_filter import (
    EXCLUDE_SYMBOLS,
    is_leveraged_or_fund,
    passes_universe_safety_gate,
)

load_dotenv(ROOT / ".env")

# ── Constants ───────────────────────────────────────────────────────────────
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 5  # unique — avoid conflict with other TWS connections
ET = pytz.timezone("America/New_York")
MAX_TRADES_DAY = 3
ENTRY_OPEN = dtime(9, 30)
ENTRY_CLOSE = dtime(11, 0)
SCAN_START = dtime(9, 20)
MIN_GAP_PCT = 0.03
MAX_GAP_PCT = 0.50
MIN_PRICE = 5.00
MAX_PRICE = 50.00
MIN_PM_VOL_RATIO = 1.5
MIN_DOLLAR_VOL = 2_000_000
TOP_N_CANDIDATES = 10
EXPECTED_PM_VOL_PCT = 0.10
IBKR_BATCH_SIZE = 80

# ── IBKR message-throttle budget (scan_premarket) ───────────────────────────
# ib_insync 0.9.86 self-throttles OUTBOUND API messages: Client.MaxRequests
# messages per Client.RequestsInterval second. Excess messages are parked in
# Client._msgQ and are only drained while the asyncio event loop runs — which,
# in this synchronous script, means only inside ib.sleep(). So the sleep that
# follows a batch has to be long enough to pay for that batch's own messages,
# or the queue carries a permanent backlog into every later batch.
IB_MAX_MSGS_PER_SEC = 45     # ib_insync Client.MaxRequests per RequestsInterval
IB_MSGS_PER_TICKER = 2       # reqMktData + cancelMktData, one of each per ticker
IB_SETTLE_MARGIN_SEC = 0.5   # slack for TWS round-trip before fields are read
MIN_BATCH_SETTLE_SEC = 3     # never less patient than the flat sleep this replaces

# ── Opening-candle capture (watch_and_enter) ────────────────────────────────
# The structure stop, broke_structure and not_dumping all describe the FIRST
# MINUTE OF THE SESSION. Bars are therefore selected by timestamp relative to
# the 9:30 open, never by position in the subscription, so that the subscription
# can start early (see subscribe_realtime_bars) without dragging pre-market bars
# into the opening candle.
REALTIME_BAR_SEC = 5                      # reqRealTimeBars supports 5s bars only
BARS_PER_MINUTE = 60 // REALTIME_BAR_SEC  # 12 five-second bars == one minute of tape
PRE_OPEN_POLL_SEC = 5                     # clock re-check interval while waiting for the bell
# One full bar period plus slack: the 9:30:00 bar is only published once its
# five seconds have elapsed, so capture cannot be judged before then.
OPEN_BAR_SETTLE_SEC = REALTIME_BAR_SEC + 2


def send_telegram(message: str) -> None:
    """Send a Telegram alert; no-op if credentials missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        print(f"Telegram sent: {message[:60]}...")
    except Exception as exc:
        print(f"Telegram failed: {exc}")


def load_universe() -> list[str]:
    """
    Load tradable CS ticker symbols from universe.json.

    Denied symbols and fund/derivative names are removed here so they are never
    even quoted. Legacy string-only universes carry no name, so the deny list is
    the only defense until refresh_universe() rebuilds the file.
    """
    universe_path = _CANDIDATES_DIR / "universe.json"
    if not universe_path.exists():
        print("universe.json not found — cannot scan")
        return []

    data = json.loads(universe_path.read_text(encoding="utf-8"))
    tickers = data.get("tickers", [])
    if not tickers:
        return []

    if isinstance(tickers[0], dict):
        return [
            t["symbol"].upper()
            for t in tickers
            if t.get("type", "CS") == "CS" and t.get("symbol")
            and t["symbol"].upper() not in EXCLUDE_SYMBOLS
            and not is_leveraged_or_fund(str(t.get("name", "") or ""))
        ]
    return [
        str(t).upper() for t in tickers
        if str(t).upper() not in EXCLUDE_SYMBOLS
    ]


def score_candidate(c: dict) -> float:
    """
    Score candidate 0-100 based on signal quality.
    Higher score = better setup (volume + price weighted over gap alone).
    """
    score = 0.0
    gap = c.get("gap_pct", 0)
    vol_ratio = c.get("pm_vol_ratio", 0)
    price = c.get("prev_close", 0)
    has_news = c.get("news_catalyst", False)
    dollar_vol = c.get("dollar_volume", 0)

    if 0.03 <= gap <= 0.06:
        score += 25
    elif 0.06 < gap <= 0.10:
        score += 20
    elif 0.10 < gap <= 0.15:
        score += 12
    elif 0.15 < gap <= 0.25:
        score += 6
    elif gap > 0.25:
        score += 2

    if vol_ratio >= 10.0:
        score += 30
    elif vol_ratio >= 7.0:
        score += 25
    elif vol_ratio >= 5.0:
        score += 20
    elif vol_ratio >= 3.0:
        score += 14
    elif vol_ratio >= 2.0:
        score += 8
    elif vol_ratio >= 1.5:
        score += 4

    if 10 <= price <= 35:
        score += 20
    elif 5 <= price < 10:
        score += 15
    elif 35 < price <= 50:
        score += 12

    if has_news:
        score += 15

    if dollar_vol >= 20_000_000:
        score += 10
    elif dollar_vol >= 10_000_000:
        score += 8
    elif dollar_vol >= 5_000_000:
        score += 6
    elif dollar_vol >= 2_000_000:
        score += 4

    return round(score, 1)


def get_atr14_ibkr(ib: IB, ticker: str, current_price: float) -> float:
    """
    Calculate ATR14 from IBKR daily bars.
    Excludes today's bar (gap day inflates ATR). Capped 2%-7% of price.
    """
    try:
        contract = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="20 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            keepUpToDate=False,
        )
        if len(bars) < 15:
            return round(current_price * 0.04, 2)

        bars = list(bars)[:-1]
        true_ranges = []
        for i in range(1, len(bars)):
            h = bars[i].high
            l = bars[i].low
            pc = bars[i - 1].close
            true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))

        atr = sum(true_ranges[-14:]) / 14
        atr = max(current_price * 0.02, min(atr, current_price * 0.07))
        return round(atr, 2)
    except Exception as exc:
        print(f"ATR failed for {ticker}: {exc}")
        return round(current_price * 0.04, 2)


def get_news_catalyst(ticker: str) -> tuple[bool, str]:
    """Check news catalyst via Yahoo Finance + SEC 8-K; summarize with OpenRouter."""
    headlines: list[str] = []

    try:
        import yfinance as yf
        from datetime import timezone

        yf_ticker = yf.Ticker(ticker)
        news = yf_ticker.news or []
        today = date.today()
        for article in news[:5]:
            pub = datetime.fromtimestamp(
                article.get("providerPublishTime", 0),
                tz=timezone.utc,
            ).date()
            if pub == today:
                title = article.get("title", "")
                if title:
                    headlines.append(title)
    except Exception:
        pass

    try:
        today_str = date.today().isoformat()
        resp = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={
                "q": ticker,
                "dateRange": "custom",
                "startdt": today_str,
                "forms": "8-K",
            },
            timeout=5,
        )
        data = resp.json()
        for hit in data.get("hits", {}).get("hits", [])[:2]:
            src = hit.get("_source", {})
            names = src.get("display_names", [])
            if names:
                headlines.append(f"SEC 8-K: {names[0].get('name', ticker)}")
    except Exception:
        pass

    if not headlines:
        return False, "🔀 No Catalyst: No news found"

    try:
        from catalyst_ai import summarize_catalyst
        return True, summarize_catalyst(ticker, headlines)
    except Exception:
        return True, f"📰 News: {headlines[0][:80]}"


def _batch_settle_seconds(batch_size: int) -> float:
    """
    Seconds of event-loop time needed to flush one scan batch's API messages.

    Derived from the message budget rather than guessed: each ticker costs
    IB_MSGS_PER_TICKER messages and ib_insync releases only
    IB_MAX_MSGS_PER_SEC of them per second, so an 80-ticker batch needs about
    3.6s just to reach TWS. A flat 3s under-drained by ~25 messages per batch,
    the backlog compounded, and from batch 2 onward the tail of every batch was
    read before its subscription existed — those tickers returned nan and were
    dropped with no log line. Floored at MIN_BATCH_SETTLE_SEC so this is never
    less patient than the sleep it replaces.
    """
    required = (IB_MSGS_PER_TICKER * batch_size) / IB_MAX_MSGS_PER_SEC
    return max(MIN_BATCH_SETTLE_SEC, round(required + IB_SETTLE_MARGIN_SEC, 1))


def scan_premarket(ib: IB) -> list[dict]:
    """
    Scan universe for gap candidates using IBKR live data.
    Runs 9:20-9:29 AM. Returns top N ranked candidates.

    Tickers whose quote never arrived are counted and reported instead of being
    silently discarded, because a throttled scan and a genuinely quiet market
    otherwise produce identical output.
    """
    universe = load_universe()
    if not universe:
        print("Empty universe — cannot scan")
        return []

    print(f"Scanning {len(universe)} tickers via IBKR...")
    print(
        f"Filters: gap {MIN_GAP_PCT:.0%}-{MAX_GAP_PCT:.0%} | "
        f"price ${MIN_PRICE:.0f}-${MAX_PRICE:.0f} | "
        f"vol >= {MIN_PM_VOL_RATIO}x | dolVol >= ${MIN_DOLLAR_VOL / 1e6:.0f}M"
    )

    candidates: list[dict] = []
    requested = 0   # market-data subscriptions actually issued to TWS
    no_quote = 0    # subscriptions that never produced a usable price

    for i in range(0, len(universe), IBKR_BATCH_SIZE):
        batch = universe[i : i + IBKR_BATCH_SIZE]
        subs: list[tuple[str, object, Stock]] = []

        for symbol in batch:
            try:
                contract = Stock(symbol, "SMART", "USD")
                ticker = ib.reqMktData(contract, "165", False, False)
                subs.append((symbol, ticker, contract))
            except Exception:
                continue

        requested += len(subs)
        ib.sleep(_batch_settle_seconds(len(batch)))

        for symbol, ticker, contract in subs:
            try:
                last = ticker.last if _valid_price(ticker.last) else ticker.close
                prev_close = ticker.close
                volume = ticker.volume or 0
                avg_vol = ticker.avVolume or 1

                # nan is truthy, so the old `ticker.last or ticker.close or 0`
                # produced nan for a starved subscription, every downstream
                # comparison was False and the ticker vanished unlogged.
                if not _valid_price(last) or not _valid_price(prev_close):
                    no_quote += 1
                    continue

                last = float(last)
                prev_close = float(prev_close)

                gap_pct = (last - prev_close) / prev_close

                if not (MIN_GAP_PCT <= gap_pct <= MAX_GAP_PCT):
                    continue
                if not (MIN_PRICE <= prev_close <= MAX_PRICE):
                    continue

                dollar_vol = prev_close * avg_vol
                if dollar_vol < MIN_DOLLAR_VOL:
                    continue

                expected_pm = avg_vol * EXPECTED_PM_VOL_PCT
                pm_vol_ratio = volume / expected_pm if expected_pm > 0 else 0.0
                if pm_vol_ratio < MIN_PM_VOL_RATIO:
                    continue

                est_shares = int(300 / last)
                if est_shares < 6:
                    continue

                candidates.append({
                    "ticker": symbol,
                    "last_price": round(last, 2),
                    "prev_close": round(prev_close, 2),
                    "gap_pct": round(gap_pct, 4),
                    "pm_vol_ratio": round(pm_vol_ratio, 2),
                    "avg_volume": int(avg_vol),
                    "dollar_volume": dollar_vol,
                    "news_catalyst": False,
                    "news_summary": "",
                    "quality_score": 0.0,
                })
            except Exception:
                continue
            finally:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass

    if no_quote:
        print(
            f"\n⚠️  No quote data: {no_quote}/{requested} tickers skipped "
            f"(TWS throttle backlog or missing market-data permission)"
        )
    else:
        print(f"\nQuote data received for all {requested} requested tickers")

    if not candidates:
        print("No candidates passed filters")
        return []

    print(f"\nFetching news for {len(candidates)} candidates...")
    for c in candidates:
        has_news, summary = get_news_catalyst(c["ticker"])
        c["news_catalyst"] = has_news
        c["news_summary"] = summary
        time.sleep(0.2)

    for c in candidates:
        c["quality_score"] = score_candidate(c)

    candidates.sort(key=lambda x: x["quality_score"], reverse=True)
    top = candidates[:TOP_N_CANDIDATES]

    print(f"\n{'=' * 50}")
    print(f"SCAN RESULTS — {datetime.now(ET).strftime('%H:%M ET')}")
    print(f"{'=' * 50}")
    print(f"Universe:          {len(universe)}")
    print(f"No quote data:     {no_quote}/{requested}")
    print(f"After all filters: {len(candidates)}")
    print(f"Top {TOP_N_CANDIDATES} candidates:")
    for idx, c in enumerate(top):
        news_flag = "📰" if c["news_catalyst"] else "  "
        print(
            f"  #{idx + 1:2d} {c['ticker']:6s} "
            f"gap={c['gap_pct']:+.1%} "
            f"vol={c['pm_vol_ratio']:.1f}x "
            f"${c['prev_close']:.2f} "
            f"score={c['quality_score']:.0f} {news_flag}"
        )
    print(f"{'=' * 50}")

    return top


def send_premarket_summary(candidates: list[dict], regime: str, vix: str) -> None:
    """Send 9:29 AM pre-market candidate watchlist via Telegram."""
    lines = [
        f"🔍 Q-ALPHA PRE-MARKET — {date.today().strftime('%b %d')}",
        f"Regime: {regime} | VIX: {vix}",
        f"{'─' * 28}",
        f"Watching {len(candidates)} candidates at open:",
        "",
    ]
    for idx, c in enumerate(candidates):
        news = "📰" if c["news_catalyst"] else "  "
        lines.append(
            f"{news}#{idx + 1} {c['ticker']:6s} "
            f"+{c['gap_pct']:.1%} | "
            f"{c['pm_vol_ratio']:.1f}x vol | "
            f"Score:{c['quality_score']:.0f}"
        )
        if c["news_catalyst"]:
            lines.append(f"   {c['news_summary'][:60]}")
    lines += [
        f"{'─' * 28}",
        "Monitoring starts at 9:30 AM open...",
    ]
    send_telegram("\n".join(lines))


def _place_intraday_bracket(ib: IB, order_plan: dict) -> dict:
    """
    Place DAY bracket order for intraday entry.
    IBKRConnector uses MOC during market hours — autonomous agent needs immediate DAY fill.
    """
    ticker = order_plan["ticker"]
    shares = int(order_plan["shares"])
    stop = float(order_plan["stop_price"])
    target_2r = float(order_plan["target_2r"])

    if shares <= 0:
        raise ValueError(f"Invalid share count for {ticker}: {shares}")

    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    parent = MarketOrder(
        action="BUY",
        totalQuantity=shares,
        tif="DAY",
        transmit=False,
    )
    parent_trade = ib.placeOrder(contract, parent)
    parent_id = parent_trade.order.orderId

    stop_loss = StopOrder(
        action="SELL",
        totalQuantity=shares,
        stopPrice=round(stop, 2),
        parentId=parent_id,
        tif="GTC",
        transmit=False,
    )
    ib.placeOrder(contract, stop_loss)

    take_profit = LimitOrder(
        action="SELL",
        totalQuantity=shares,
        lmtPrice=round(target_2r, 2),
        parentId=parent_id,
        tif="GTC",
        transmit=True,
    )
    ib.placeOrder(contract, take_profit)
    ib.sleep(1)

    return {
        "ticker": ticker,
        "parent_id": parent_id,
        "shares": shares,
        "stop": stop,
        "target": target_2r,
        "status": "SUBMITTED",
        "paper": True,
    }


def _get_open_tickers_today() -> set[str]:
    """Return tickers with active positions today."""
    store = PaperTradesStore()
    data = store.load()
    today = date.today().isoformat()
    active_statuses = {"OPEN", "T1_HIT", "T3_TRAIL", "PENDING_MOC"}
    return {
        t["ticker"].upper()
        for t in data.get("trades", [])
        if t.get("entry_date") == today
        and t.get("status") in active_statuses
        and t.get("approved_by") in ("autonomous_agent", "telegram_yes")
    }


def _count_trades_today() -> int:
    """Count autonomous + telegram entries opened today."""
    store = PaperTradesStore()
    data = store.load()
    today = date.today().isoformat()
    return sum(
        1
        for t in data.get("trades", [])
        if t.get("entry_date") == today
        and t.get("status") not in ("SKIPPED",)
        and t.get("approved_by") in ("autonomous_agent", "telegram_yes")
    )


def save_trade(trade_dict: dict) -> None:
    """Append trade to paper_trades.json using PaperTradesStore schema."""
    store = PaperTradesStore()
    data = store.load()
    data["trades"].append(trade_dict)
    store.save(data)
    print(f"Trade saved: {trade_dict['ticker']}")


def session_open_dt(day: date | None = None) -> datetime:
    """
    Timezone-aware 9:30 ET session open for `day` (default today).

    Built with ET.localize() and NOT datetime.combine(...).replace(tzinfo=ET):
    a pytz zone object carries a table of historical offsets and replace() picks
    the first entry, which for America/New_York is LMT (-04:56). That silently
    shifts every open-relative comparison by about four minutes.
    """
    return ET.localize(datetime.combine(day or date.today(), ENTRY_OPEN))


def bars_since_open(raw_bars: list, open_dt: datetime) -> list:
    """
    The subset of a real-time bar list belonging to the regular session.

    ib_insync builds RealTimeBar.time with datetime.fromtimestamp(t, utc), so it
    is a tz-aware UTC datetime marking the START of the bar, and comparing it to
    an ET-localized open is valid across zones. Selecting by timestamp — rather
    than by raw_bars[:12] — is what lets the subscription start before the bell
    without the pre-market tape being mistaken for the opening candle.
    """
    return [b for b in raw_bars if b.time >= open_dt]


def earliest_bar_time(rt_bars: dict) -> datetime | None:
    """
    Timestamp of the oldest bar across every subscription, or None if empty.

    This is the evidence that the subscription predated the bell: one bar at or
    before the open proves the feed was live when the session started, so no
    ticker's opening candle can have been missed. Every scan candidate cleared
    MIN_PM_VOL_RATIO and therefore traded pre-market, so on a punctual run this
    is always a pre-market bar.
    """
    times = [
        bars[0].time
        for bars, _contract in rt_bars.values()
        if len(bars) > 0
    ]
    return min(times) if times else None


def subscribe_realtime_bars(ib: IB, candidates: list[dict]) -> dict:
    """
    Open 5-second real-time bar subscriptions for the candidate list.

    Called BEFORE the pre-open wait, so bars are already flowing when the bell
    rings and the 9:30:00 bar is never missed. useRTH=False is required: with
    regular-trading-hours filtering on, TWS would deliver nothing until 9:30 and
    pre-subscribing would buy nothing.

    The universe safety gate is applied here as well as in watch_and_enter, so a
    blocked symbol is never subscribed even though watch_and_enter is what
    reports and skips it.

    Returns {ticker: (RealTimeBarList, Stock)}.
    """
    print(f"\nSubscribing to real-time bars for {len(candidates)} tickers...")
    rt_bars: dict[str, tuple[object, Stock]] = {}
    for c in candidates:
        if not passes_universe_safety_gate(c["ticker"]):
            continue
        try:
            contract = Stock(c["ticker"], "SMART", "USD")
            ib.qualifyContracts(contract)
            bars = ib.reqRealTimeBars(
                contract, REALTIME_BAR_SEC, "TRADES", False,
            )
            rt_bars[c["ticker"]] = (bars, contract)
            print(f"  Subscribed: {c['ticker']}")
        except Exception as exc:
            print(f"  Failed {c['ticker']}: {exc}")
    return rt_bars


def cancel_realtime_bars(ib: IB, rt_bars: dict) -> None:
    """Release every real-time bar subscription; never raises."""
    for _ticker, (bars, _contract) in rt_bars.items():
        try:
            ib.cancelRealTimeBars(bars)
        except Exception:
            pass


def watch_and_enter(ib: IB, candidates: list[dict], rt_bars: dict | None = None) -> dict:
    """
    Monitor candidates 9:30-11:00 AM; enter on confirmed gap+VWAP+volume setup.
    Places DAY bracket orders via IBKR.

    `rt_bars` is the mapping returned by subscribe_realtime_bars(), which main()
    calls before the pre-open wait so the opening bar is captured. When omitted
    the subscription is opened here instead, which is correct but can only see
    the tape from this moment on.

    Every candidate clears the universe safety gate BEFORE any market-data
    subscription, so a blocked symbol is never watched and never retried.

    Returns {"entered": [...], "skipped": [...], "tracker": {...}} on every path,
    including the early return taken when the opening candle was missed, because
    main() feeds the result straight into send_session_recap().
    """
    pool = PoolManager(state_path=state_path("pool_state.json"))

    entered: list[dict] = []
    skipped: list[dict] = []
    decided: set[str] = set()
    open_tickers = _get_open_tickers_today()

    gated: list[dict] = []
    for c in candidates:
        if passes_universe_safety_gate(c["ticker"]):
            gated.append(c)
        else:
            skipped.append({
                "ticker": c["ticker"],
                "reason": "failed universe safety gate",
                "price": c.get("last_price", 0.0),
            })
    if len(gated) != len(candidates):
        blocked = [s["ticker"] for s in skipped]
        send_telegram("🚫 Blocked (not tradable common stock): "
                      + ", ".join(blocked))
        candidates = gated

    candidate_tracker = {
        c["ticker"]: {
            "candidate": c,
            "bars": [],
            "peak_price": c["last_price"],
            "final_price": c["last_price"],
            "decision": "watching",
            "decision_time": None,
            "decision_reason": "",
        }
        for c in candidates
    }

    if not candidate_tracker:
        print("No candidates left to watch after the universe safety gate.")
        return {"entered": entered, "skipped": skipped, "tracker": candidate_tracker}

    if rt_bars is None:
        rt_bars = subscribe_realtime_bars(ib, candidates)
    else:
        # A symbol blocked by the gate above must not be watched even if it was
        # subscribed before the gate ran.
        rt_bars = {t: v for t, v in rt_bars.items() if t in candidate_tracker}

    session_open = session_open_dt()

    print("\nWaiting for market open (9:30 AM)...")
    while datetime.now(ET).time() < ENTRY_OPEN:
        ib.sleep(PRE_OPEN_POLL_SEC)
    ib.sleep(OPEN_BAR_SETTLE_SEC)

    # The opening candle is selected by timestamp, so the only way to miss it is
    # for the feed itself to have started after the bell. One bar at or before
    # the open proves it did not. Without that proof the structure stop would be
    # measured off an arbitrary post-open minute, silently mispricing the stop
    # and the broke_structure / not_dumping / hard_dump checks. Refusing costs
    # one day of opportunity; entering costs a wrong stop on every position.
    earliest = earliest_bar_time(rt_bars)
    if earliest is None or earliest > session_open:
        now_et = datetime.now(ET)
        reason = "opening candle missed — structure stop would be invalid"
        first_bar = (
            "no real-time bars arrived at all"
            if earliest is None
            else f"first bar is {earliest.astimezone(ET).strftime('%H:%M:%S')} ET"
        )
        msg = (
            f"🚫 Q-ALPHA: NO ENTRIES TODAY\n"
            f"Real-time feed started after the {ENTRY_OPEN.strftime('%H:%M')} "
            f"open ({first_bar}).\n"
            f"The opening candle was never captured, so the structure stop would "
            f"be invalid. Skipping {len(candidate_tracker)} candidate(s) rather "
            f"than entering on a mispriced stop."
        )
        print(msg)
        send_telegram(msg)
        for ticker, track in candidate_tracker.items():
            track.update(
                decision="skipped",
                decision_time=now_et.isoformat(),
                decision_reason=reason,
            )
            skipped.append({
                "ticker": ticker,
                "reason": reason,
                "price": track["candidate"].get("last_price", 0.0),
            })
        cancel_realtime_bars(ib, rt_bars)
        return {"entered": entered, "skipped": skipped, "tracker": candidate_tracker}

    while True:
        now_et = datetime.now(ET)

        if now_et.time() >= ENTRY_CLOSE:
            print("\nEntry window closed at 11:00 AM")
            break

        trades_today = _count_trades_today()
        if trades_today >= MAX_TRADES_DAY:
            print(f"Max trades reached ({MAX_TRADES_DAY})")
            break

        if not pool.can_open_trade():
            print("Pool at capacity")
            break

        minutes_since_open = (now_et.hour - 9) * 60 + now_et.minute - 30

        for ticker, track in candidate_tracker.items():
            if ticker in decided:
                continue
            if ticker in open_tickers:
                decided.add(ticker)
                continue

            bars_obj, _contract = rt_bars.get(ticker, (None, None))
            if bars_obj is None:
                continue

            raw_bars = list(bars_obj)
            session_bars = bars_since_open(raw_bars, session_open)

            # Nothing may be judged until a full opening minute of SESSION bars
            # exists: first_candle_low/high describe that minute, and this also
            # keeps the min()/max() below off an empty sequence in the first
            # seconds after the bell, when raw_bars holds only pre-market tape.
            if len(session_bars) < BARS_PER_MINUTE:
                continue

            recent = raw_bars[-BARS_PER_MINUTE:]
            # VWAP is session VWAP. raw_bars now reaches back into the pre-market
            # because the subscription starts before the bell, so it must not be
            # used here.
            all_bars = session_bars

            current_price = recent[-1].close
            open_price = session_bars[0].open_
            prev_close = track["candidate"]["prev_close"]

            track["final_price"] = current_price
            track["peak_price"] = max(track["peak_price"], current_price)
            track["bars"] = all_bars

            total_vol = sum(b.volume for b in all_bars if b.volume > 0)
            if total_vol > 0:
                vwap = sum(b.close * b.volume for b in all_bars if b.volume > 0) / total_vol
            else:
                vwap = current_price

            # ib_insync names the dataclass field open_, not open; b.open raises.
            up_bars = [b for b in recent if b.close >= b.open_]
            dn_bars = [b for b in recent if b.close < b.open_]
            up_vol = sum(b.volume for b in up_bars)
            dn_vol = sum(b.volume for b in dn_bars)

            # The opening candle: the first minute AT OR AFTER 9:30, guaranteed
            # to be BARS_PER_MINUTE long by the session-bar guard above.
            first_min = session_bars[:BARS_PER_MINUTE]
            first_candle_low = min(b.low for b in first_min)
            first_candle_high = max(b.high for b in first_min)

            gap_holding = current_price > prev_close * 1.015
            above_vwap = current_price > vwap
            vol_confirming = up_vol > dn_vol * 1.1
            not_dumping = current_price > open_price * 0.97
            min_wait = minutes_since_open >= 2

            gap_filled = current_price < prev_close * 1.005
            hard_dump = current_price < open_price * 0.95
            broke_structure = current_price < first_candle_low * 0.99

            structure_stop_dist = max(
                current_price * 0.02,
                min(current_price - first_candle_low, current_price * 0.07),
            )

            print(
                f"[{now_et.strftime('%H:%M:%S')}] "
                f"{ticker:6s} ${current_price:.2f} "
                f"(+{(current_price / prev_close - 1):.1%}) "
                f"VWAP:{'✅' if above_vwap else '❌'} "
                f"Gap:{'✅' if gap_holding else '❌'} "
                f"Vol:{'✅' if vol_confirming else '❌'} "
                f"Str:{'✅' if not broke_structure else '❌'}"
            )

            if gap_filled:
                reason = "Gap filled — price returned to prev close"
                print(f"  → SKIP {ticker}: {reason}")
                decided.add(ticker)
                skipped.append({"ticker": ticker, "reason": reason, "price": current_price})
                track.update(decision="skipped", decision_time=now_et.isoformat(), decision_reason=reason)
                send_telegram(f"⏭ {ticker} SKIPPED\n{reason}")
                continue

            if hard_dump:
                reason = f"Hard dump from open ({(current_price / open_price - 1):.1%})"
                print(f"  → SKIP {ticker}: {reason}")
                decided.add(ticker)
                skipped.append({"ticker": ticker, "reason": reason, "price": current_price})
                track.update(decision="skipped", decision_time=now_et.isoformat(), decision_reason=reason)
                send_telegram(f"⏭ {ticker} SKIPPED\n{reason}")
                continue

            if broke_structure and minutes_since_open >= 5:
                reason = f"Broke below first candle low ${first_candle_low:.2f}"
                print(f"  → SKIP {ticker}: {reason}")
                decided.add(ticker)
                skipped.append({"ticker": ticker, "reason": reason, "price": current_price})
                track.update(decision="skipped", decision_time=now_et.isoformat(), decision_reason=reason)
                send_telegram(f"⏭ {ticker} SKIPPED\n{reason}")
                continue

            all_conditions = (
                gap_holding
                and above_vwap
                and vol_confirming
                and not_dumping
                and not broke_structure
                and min_wait
            )

            if all_conditions:
                print(f"  → ENTERING {ticker} @ ${current_price:.2f}")

                pool_size = pool.position_size()
                shares = max(6, int(pool_size / current_price))

                t1_shares = int(shares * 0.33)
                t2_shares = int(shares * 0.33)
                t3_shares = shares - t1_shares - t2_shares

                stop_price = round(current_price - structure_stop_dist, 2)
                risk_ps = current_price - stop_price
                target_1r = round(current_price + risk_ps, 2)
                target_2r = round(current_price + risk_ps * 2, 2)
                target_3r = round(current_price + risk_ps * 3, 2)

                order_plan = {
                    "ticker": ticker,
                    "shares": shares,
                    "entry_price": current_price,
                    "stop_price": stop_price,
                    "target_1r": target_1r,
                    "target_2r": target_2r,
                    "target_3r": target_3r,
                    "tranche_1_shares": t1_shares,
                    "tranche_2_shares": t2_shares,
                    "tranche_3_shares": t3_shares,
                    "position_value": round(shares * current_price, 2),
                    "risk_dollars": round(shares * risk_ps, 2),
                }

                try:
                    result = _place_intraday_bracket(ib, order_plan)
                    position_value = shares * current_price
                    pool.open_trade(position_value)

                    atr_14 = get_atr14_ibkr(ib, ticker, current_price)
                    entry_reason = (
                        f"Gap +{(current_price / prev_close - 1):.1%} | "
                        f"VWAP ✅ | "
                        f"UpVol {up_vol / (dn_vol + 1):.1f}x | "
                        f"Score {track['candidate']['quality_score']:.0f}"
                    )

                    trade = PaperTrade(
                        ticker=ticker,
                        entry_date=date.today().isoformat(),
                        entry_price=current_price,
                        stop_price=stop_price,
                        target_1r=target_1r,
                        target_2r=target_2r,
                        target_3r=target_3r,
                        shares_total=shares,
                        shares_t1=t1_shares,
                        shares_t2=t2_shares,
                        shares_t3=t3_shares,
                        status="OPEN",
                        approved_by="autonomous_agent",
                        order_plan=order_plan,
                        atr_14=atr_14,
                        position_value=round(position_value, 2),
                        ibkr_order_id=result.get("parent_id"),
                        ibkr_status="SUBMITTED",
                        execution_mode="IBKR_PAPER",
                    )
                    trade_dict = trade.to_dict()
                    trade_dict["entry_reason"] = entry_reason

                    entered.append(trade_dict)
                    decided.add(ticker)
                    open_tickers.add(ticker)
                    track.update(
                        decision="entered",
                        decision_time=now_et.isoformat(),
                        decision_reason=entry_reason,
                    )
                    save_trade(trade_dict)

                    send_telegram(
                        f"✅ AI ENTERED {ticker}\n"
                        f"Price:  ${current_price:.2f}\n"
                        f"Stop:   ${stop_price:.2f} "
                        f"(-{structure_stop_dist / current_price:.1%})\n"
                        f"Target: ${target_2r:.2f} "
                        f"(+{risk_ps * 2 / current_price:.1%})\n"
                        f"Shares: {shares} ({t1_shares}/{t2_shares}/{t3_shares})\n"
                        f"Reason: {entry_reason}\n"
                        f"Catalyst: {track['candidate']['news_summary'][:60]}"
                    )
                    print(f"  ✅ ORDER PLACED — Order ID: {result.get('parent_id')}")

                except Exception as exc:
                    print(f"  ❌ ORDER FAILED: {exc}")
                    send_telegram(f"⚠️ ORDER FAILED for {ticker}: {exc}")

        if len(decided) == len(candidates):
            print("All candidates decided.")
            break

        ib.sleep(30)

    cancel_realtime_bars(ib, rt_bars)

    return {"entered": entered, "skipped": skipped, "tracker": candidate_tracker}


def send_session_recap(result: dict, candidates: list[dict]) -> None:
    """Send 11:00 AM recap and persist session tracker JSON."""
    entered = result["entered"]
    skipped = result["skipped"]
    tracker = result["tracker"]

    lines = [
        "🤖 Q-ALPHA SESSION RECAP",
        f"{date.today().strftime('%Y-%m-%d')} | "
        f"{datetime.now(ET).strftime('%H:%M ET')}",
        f"{'─' * 30}",
    ]

    if entered:
        lines.append(f"✅ ENTERED ({len(entered)}):")
        for t in entered:
            lines.append(f"  {t['ticker']} @ ${t['entry_price']:.2f}")
            lines.append(
                f"  Stop ${t['stop_price']:.2f} | Target ${t['target_2r']:.2f}"
            )
            lines.append(f"  {t.get('entry_reason', '')}")
    else:
        lines.append("✅ ENTERED: None")

    lines.append("")

    if skipped:
        lines.append(f"⏭ SKIPPED ({len(skipped)}):")
        for s in skipped:
            lines.append(f"  {s['ticker']}: {s['reason'][:50]}")

    still_watching = [
        t for t, track in tracker.items() if track["decision"] == "watching"
    ]
    if still_watching:
        lines.append(f"\n⏱ EXPIRED (no signal): {', '.join(still_watching)}")

    lines += [
        f"{'─' * 30}",
        f"Positions: {len(entered)}/{MAX_TRADES_DAY} today",
        "EOD report at 4:15 PM ET",
    ]

    recap = "\n".join(lines)
    send_telegram(recap)
    print(recap)

    tracker_path = state_path(f"session_tracker_{date.today().isoformat()}.json")
    save_data = {
        "date": date.today().isoformat(),
        "entered": entered,
        "skipped": skipped,
        "candidates": [
            {
                "ticker": t,
                "decision": track["decision"],
                "reason": track["decision_reason"],
                "peak_price": track["peak_price"],
                "final_price": track["final_price"],
                "candidate": track["candidate"],
            }
            for t, track in tracker.items()
        ],
    }
    tracker_path.parent.mkdir(parents=True, exist_ok=True)
    tracker_path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
    print(f"Session tracker saved: {tracker_path}")


def _run_modal_cmd(args: list[str]) -> None:
    """Run modal CLI; strip MODAL_ENVIRONMENT like local_approval_runner."""
    cmd = [sys.executable, "-m", "modal"] + args
    env = os.environ.copy()
    env.pop("MODAL_ENVIRONMENT", None)
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def sync_to_modal() -> None:
    """Push local state files to Modal volume for EOD monitor."""
    files = [
        (state_path("paper_trades.json"), "paper_trades.json"),
        (state_path("pool_state.json"), "pool_state.json"),
    ]
    for local_path, remote in files:
        if not local_path.exists():
            print(f"Skip sync — missing {local_path}")
            continue
        try:
            _run_modal_cmd([
                "volume", "put", "qalpha-state",
                str(local_path), remote, "--force",
            ])
            print(f"Synced {local_path.name} → Modal")
        except Exception as exc:
            print(f"Sync failed for {local_path.name}: {exc}")


SPY_SMA_PERIOD = 50            # SMA length for the regime filter
SPY_HISTORY_DURATION = "60 D"  # always covers SPY_SMA_PERIOD trading days
SPY_SNAPSHOT_WAIT = 2          # seconds to let a TWS snapshot arrive


def _valid_price(value: object) -> bool:
    """
    True only for a real, positive price.

    ib_insync fills unsubscribed ticker fields with float('nan'), and nan is
    truthy, so `ticker.last or ticker.close or 0` yields nan rather than 0 and
    every nan comparison is False. That is what silently produced BEAR on a
    bull day, so nan must be rejected explicitly.
    """
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return price > 0 and not math.isnan(price)


def fetch_regime_from_tws(ib: IB) -> dict | None:
    """
    SPY regime from the TWS snapshot plus daily bars.

    Returns None -- never a regime -- when the snapshot or history is unusable,
    so the caller falls back instead of trading a guess.
    """
    spy_contract = Stock("SPY", "SMART", "USD")
    ib.qualifyContracts(spy_contract)
    spy_ticker = ib.reqMktData(spy_contract, "", False, False)
    ib.sleep(SPY_SNAPSHOT_WAIT)
    snapshot = spy_ticker.last if _valid_price(spy_ticker.last) else spy_ticker.close
    ib.cancelMktData(spy_contract)

    if not _valid_price(snapshot):
        print("  TWS SPY snapshot unusable (no live data subscription?)")
        return None

    spy_bars = ib.reqHistoricalData(
        spy_contract, "", SPY_HISTORY_DURATION, "1 day", "TRADES", True,
        keepUpToDate=False,
    )
    closes = [b.close for b in spy_bars if _valid_price(b.close)]
    if len(closes) < SPY_SMA_PERIOD:
        print(f"  TWS SPY history too short: {len(closes)} bars "
              f"(need {SPY_SMA_PERIOD})")
        return None

    spy_price = float(snapshot)
    spy_sma50 = sum(closes[-SPY_SMA_PERIOD:]) / SPY_SMA_PERIOD
    return {
        "spy_regime": "BULL" if spy_price > spy_sma50 else "BEAR",
        "vix_regime": "NORMAL",
        "spy_price": spy_price,
        "spy_sma50": spy_sma50,
        "source": "TWS",
    }


def fetch_regime_from_polygon() -> dict | None:
    """
    Authoritative fallback via pre_market_scanner.fetch_spy_regime(), which
    raises on insufficient history instead of defaulting to a regime.

    Imported lazily because pre_market_scanner imports modal at module scope;
    the agent should not pay that cost on the normal TWS path.
    """
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        print("  Polygon fallback unavailable: POLYGON_API_KEY not set")
        return None
    try:
        from pre_market_scanner import fetch_spy_regime

        regime_data = dict(fetch_spy_regime(api_key))
    except Exception as exc:
        print(f"  Polygon regime fallback failed: {exc}")
        return None

    if not _valid_price(regime_data.get("spy_price")):
        print("  Polygon fallback returned no usable SPY price")
        return None

    regime_data["source"] = "Polygon fallback"
    return regime_data


def main() -> None:
    """Orchestrate all five autonomous agent phases."""
    now_et = datetime.now(ET)
    print(f"{'=' * 50}")
    print("Q-ALPHA AUTONOMOUS AGENT")
    print(now_et.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print(f"{'=' * 50}")

    if not is_trading_day():
        print("Market closed today. Exiting.")
        send_telegram("💤 Q-ALPHA: Market closed today.")
        return

    ib = IB()
    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID)
        accounts = getattr(ib.wrapper, "accounts", None) or ib.managedAccounts()
        print("✅ Connected to TWS")
        print(f"   Account: {accounts}")
    except Exception as exc:
        msg = f"⚠️ Q-ALPHA: TWS connection failed — {exc}\nOpen TWS and try again."
        print(msg)
        send_telegram(msg)
        return

    try:
        regime_data = fetch_regime_from_tws(ib)
        if regime_data is None:
            print("Falling back to Polygon for SPY regime...")
            regime_data = fetch_regime_from_polygon()

        if regime_data is None:
            msg = ("⚠️ Q-ALPHA: SPY regime data unavailable — "
                   "trading halted for safety, manual check needed")
            print(msg)
            send_telegram(msg)
            return

        regime = regime_data["spy_regime"]
        vix = regime_data.get("vix_regime", "NORMAL")
        print(
            f"Regime: {regime} | SPY: ${regime_data['spy_price']:.2f} "
            f"vs SMA50: ${regime_data['spy_sma50']:.2f} | "
            f"VIX: {vix} | source: {regime_data['source']}"
        )

        print(f"\n{'─' * 40}")
        print("PHASE 1: PRE-MARKET SCAN")
        print(f"{'─' * 40}")
        candidates = scan_premarket(ib)

        if not candidates:
            send_telegram(f"💤 Q-ALPHA: No gap candidates today.\nRegime: {regime}")
            return

        send_premarket_summary(candidates, regime, vix)

        # Subscribe BEFORE the pre-open wait, so the 9:30:00 bar is already in
        # the list when watch_and_enter selects the opening candle. ib.sleep()
        # below runs the event loop, which is what lets those bars accumulate.
        rt_bars = subscribe_realtime_bars(ib, candidates)

        while datetime.now(ET).time() < ENTRY_OPEN:
            remaining = session_open_dt() - datetime.now(ET)
            print(
                f"Market opens in {int(remaining.total_seconds() // 60)}m "
                f"{int(remaining.total_seconds() % 60)}s..."
            )
            ib.sleep(10)

        print(f"\n{'─' * 40}")
        print("PHASE 3: MARKET OPEN WATCHER")
        print(f"{'─' * 40}")
        result = watch_and_enter(ib, candidates, rt_bars)

        print(f"\n{'─' * 40}")
        print("PHASE 4: SESSION RECAP")
        print(f"{'─' * 40}")
        send_session_recap(result, candidates)

        print(f"\n{'─' * 40}")
        print("PHASE 5: SYNC TO MODAL")
        print(f"{'─' * 40}")
        sync_to_modal()

    except Exception as exc:
        msg = f"⚠️ Q-ALPHA AGENT ERROR: {exc}"
        print(msg)
        send_telegram(msg)
        traceback.print_exc()

    finally:
        try:
            ib.disconnect()
            print("Disconnected from TWS")
        except Exception:
            pass


if __name__ == "__main__":
    main()
