# SCHEDULE: 8:30 AM ET weekdays
# modal run candidates/pre_market_scanner.py
"""
Q-Alpha pre-market scanner.
Finds gapping stocks pre-market, saves JSON, sends Telegram alert.
Scanner output only — no order placement.
"""
import importlib.util
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import modal

from state_paths import CANDIDATES_DIR, is_trading_day, state_path

app = modal.App("q-alpha-premarket-scanner")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(["requests", "python-dotenv", "tzdata"])
    .add_local_file(
        local_path=str(CANDIDATES_DIR / "position_sizer.py"),
        remote_path="/root/candidates/position_sizer.py",
    )
    .add_local_file(
        local_path=str(CANDIDATES_DIR / "paper_trader.py"),
        remote_path="/root/candidates/paper_trader.py",
    )
)

polygon_secret = modal.Secret.from_name("polygon-api-key")


def now_et() -> datetime:
    """Current time in US/Eastern (works locally on Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=-4)))

POLYGON_BASE = "https://api.polygon.io"
GAP_MIN = 0.03
VOL_RATIO_MIN = 1.0
PRICE_MIN = 3.0
PRICE_MAX = 500.0
MIN_PREV_VOLUME = 100_000
MAX_CANDIDATES = 20
MAX_TICKER_LEN = 5
SNAPSHOT_PAGE_LIMIT = 250
SNAPSHOT_TIMEOUT = 60
REQUEST_TIMEOUT = 10
POLYGON_PAGE_SLEEP = 0.12


def load_dotenv_if_available():
    """Load repo .env for local runs (Modal secrets used on cloud)."""
    env_path = CANDIDATES_DIR.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_position_sizer():
    """Load position_sizer from local path or Modal mount."""
    for path in (
        CANDIDATES_DIR / "position_sizer.py",
        Path("/root/candidates/position_sizer.py"),
    ):
        if path.exists():
            spec = importlib.util.spec_from_file_location("position_sizer", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["position_sizer"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("position_sizer.py not found")


def load_paper_trader_module():
    """Load paper_trader from local path or Modal mount."""
    for path in (
        CANDIDATES_DIR / "paper_trader.py",
        Path("/root/candidates/paper_trader.py"),
    ):
        if path.exists():
            spec = importlib.util.spec_from_file_location("paper_trader", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["paper_trader"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("paper_trader.py not found")


def log_scan_error(message: str):
    """Append scanner errors to scan_errors.log (Modal volume or local candidates/)."""
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    log_path = state_path("scan_errors.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {message}\n")


def polygon_get(path: str, params: dict, api_key: str, timeout=REQUEST_TIMEOUT):
    """Polygon GET with timeout and basic error handling."""
    import requests
    params = dict(params)
    params["apiKey"] = api_key
    url = f"{POLYGON_BASE}{path}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_all_market_snapshots(api_key: str) -> list[dict]:
    """
    Pull paginated full US market snapshot from Polygon.
    Returns raw ticker snapshot dicts (~8,000 NYSE + NASDAQ names).
    """
    all_snapshots: list[dict] = []
    path = "/v2/snapshot/locale/us/markets/stocks/tickers"
    params = {"include_otc": "false", "limit": SNAPSHOT_PAGE_LIMIT}
    page = 0

    while path:
        data = polygon_get(path, params, api_key, timeout=SNAPSHOT_TIMEOUT)
        batch = data.get("tickers") or data.get("results") or []
        all_snapshots.extend(batch)
        page += 1
        if page % 5 == 0:
            print(f"  Snapshot pages: {page} | tickers so far: {len(all_snapshots)}")

        next_url = data.get("next_url")
        if not next_url:
            break
        path = next_url.replace(POLYGON_BASE, "")
        params = {}
        time.sleep(POLYGON_PAGE_SLEEP)

    return all_snapshots


def _snapshot_premarket_price(tdata: dict) -> float | None:
    """Best available pre-market price from snapshot fields."""
    last_trade = tdata.get("lastTrade") or {}
    day = tdata.get("day") or {}
    price = last_trade.get("p") or day.get("o") or day.get("c")
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _parse_snapshot_row(tdata: dict) -> dict | None:
    """Extract gap/volume fields from one Polygon snapshot ticker."""
    ticker = (tdata.get("ticker") or tdata.get("T") or "").upper().strip()
    if not ticker:
        return None

    prev_day = tdata.get("prevDay") or {}
    prev_close = float(prev_day.get("c") or prev_day.get("close") or 0)
    prev_volume = float(prev_day.get("v") or prev_day.get("volume") or 0)
    if prev_close <= 0:
        return None

    premarket_price = _snapshot_premarket_price(tdata)
    if premarket_price is None:
        return None

    day = tdata.get("day") or {}
    min_data = tdata.get("min") or {}
    pm_volume = float(day.get("v") or min_data.get("v") or 0)
    avg_volume = float(min_data.get("av") or 0)
    gap_pct = (premarket_price - prev_close) / prev_close

    return {
        "ticker": ticker,
        "prev_close": prev_close,
        "premarket_price": premarket_price,
        "gap_pct": gap_pct,
        "prev_volume": prev_volume,
        "pm_volume": pm_volume,
        "avg_volume": avg_volume,
    }


def _passes_price_volume_ticker_filters(row: dict) -> bool:
    """Hard filters after gap detection: price, liquidity, ticker hygiene."""
    ticker = row["ticker"]
    prev_close = row["prev_close"]
    if not (PRICE_MIN <= prev_close <= PRICE_MAX):
        return False
    if row["prev_volume"] < MIN_PREV_VOLUME:
        return False
    if "." in ticker:
        return False
    if len(ticker) > MAX_TICKER_LEN:
        return False
    return True


def get_all_gap_candidates(api_key: str, today_news_gte: str) -> tuple[list[dict], dict]:
    """
    Scan full NYSE + NASDAQ via Polygon snapshot; post-filter for gaps.
    Returns (top candidates, funnel stats for logging/JSON).
    """
    snapshots = fetch_all_market_snapshots(api_key)
    snapshot_count = len(snapshots)
    print(f"  Snapshot: {snapshot_count:,} tickers returned")

    parsed_rows: list[dict] = []
    for tdata in snapshots:
        row = _parse_snapshot_row(tdata)
        if row is not None:
            parsed_rows.append(row)

    gap_rows = [r for r in parsed_rows if r["gap_pct"] >= GAP_MIN]
    after_gap = len(gap_rows)
    print(f"  After gap filter (≥{GAP_MIN:.0%}): {after_gap} candidates")

    price_vol_rows = [r for r in gap_rows if _passes_price_volume_ticker_filters(r)]
    after_price_vol = len(price_vol_rows)
    print(f"  After price/volume filter: {after_price_vol} candidates")

    vol_rows: list[dict] = []
    for row in price_vol_rows:
        avg_vol = row["avg_volume"]
        today_vol = row["pm_volume"]
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0.0
        if vol_ratio >= VOL_RATIO_MIN:
            row["pm_vol_ratio"] = vol_ratio
            vol_rows.append(row)

    after_vol = len(vol_rows)
    print(f"  After vol ratio filter (≥{VOL_RATIO_MIN:.1f}x): {after_vol} candidates")

    candidates: list[dict] = []
    try:
        from candidates.catalyst_ai import summarize_catalyst, get_ticker_headlines
    except ImportError:
        from catalyst_ai import summarize_catalyst, get_ticker_headlines

    for row in vol_rows:
        ticker = row["ticker"]
        headlines = get_ticker_headlines(ticker, api_key)
        ai_catalyst = summarize_catalyst(ticker, headlines)
        candidates.append({
            "ticker": ticker,
            "gap_estimate": round(row["gap_pct"], 6),
            "pm_vol_ratio": round(row["pm_vol_ratio"], 2),
            "catalyst_summary": ai_catalyst,
            "news_catalyst": len(headlines) > 0,
            "news_headline": headlines[0] if headlines else None,
            "prev_close": round(row["prev_close"], 4),
            "premarket_price": round(row["premarket_price"], 4),
        })
        time.sleep(POLYGON_PAGE_SLEEP)

    candidates.sort(key=lambda x: x["gap_estimate"], reverse=True)
    final_candidates = candidates[:MAX_CANDIDATES]
    for i, candidate in enumerate(final_candidates, 1):
        candidate["rank"] = i

    print(f"  Final candidates: {len(final_candidates)}")

    funnel = {
        "snapshot_count": snapshot_count,
        "after_gap_filter": after_gap,
        "after_price_volume_filter": after_price_vol,
        "after_vol_ratio_filter": after_vol,
        "final_candidates": len(final_candidates),
    }
    return final_candidates, funnel


def fetch_news(ticker: str, today_str: str, api_key: str):
    """Return (has_catalyst, headline or None)."""
    try:
        data = polygon_get(
            "/v2/reference/news",
            {
                "ticker": ticker,
                "published_utc.gte": today_str,
                "limit": 1,
                "sort": "published_utc",
                "order": "desc",
            },
            api_key,
        )
        results = data.get("results", [])
        if not results:
            return False, None
        headline = results[0].get("title") or results[0].get("description")
        return True, headline
    except Exception:
        return False, None


def fetch_spy_regime(api_key: str) -> dict:
    """SPY 60-day history → SMA50 regime + VIX proxy."""
    end = now_et().date()
    start = end - timedelta(days=90)
    data = polygon_get(
        f"/v2/aggs/ticker/SPY/range/1/day/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
        api_key,
        timeout=30,
    )
    bars = data.get("results", [])
    if len(bars) < 50:
        raise RuntimeError("Insufficient SPY history for SMA50")

    closes = [float(b["c"]) for b in bars]
    spy_price = closes[-1]
    spy_sma50 = sum(closes[-50:]) / 50
    spy_regime = "BULL" if spy_price >= spy_sma50 else "BEAR"

    returns = []
    for i in range(1, min(21, len(closes))):
        if closes[-i - 1] > 0:
            returns.append((closes[-i] - closes[-i - 1]) / closes[-i - 1])
    if len(returns) < 5:
        vix_proxy = 20.0
    else:
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std = var ** 0.5
        vix_proxy = std * 16 * 100

    vix_regime = "ELEVATED" if vix_proxy >= 25 else "NORMAL"
    return {
        "spy_regime": spy_regime,
        "vix_regime": vix_regime,
        "spy_price": round(spy_price, 4),
        "spy_sma50": round(spy_sma50, 4),
        "vix_proxy": round(vix_proxy, 2),
    }


def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Send Telegram message; no-op if credentials missing. Returns success."""
    import requests
    if not bot_token or not chat_id:
        print("  Telegram skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")
        print(f"   Token: {os.environ.get('TELEGRAM_BOT_TOKEN', 'NOT FOUND')[:20]}...")
        print(f"   Chat ID: {os.environ.get('TELEGRAM_CHAT_ID', 'NOT FOUND')}")
        return False
    if bot_token == "your_bot_token_here" or chat_id == "your_chat_id_here":
        print("  Telegram skipped (placeholder credentials in .env)")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        print("✅ Telegram sent successfully")
        return True
    except Exception as e:
        print(f"❌ Telegram failed: {e}")
        print(f"   Token: {os.environ.get('TELEGRAM_BOT_TOKEN', 'NOT FOUND')[:20]}...")
        print(f"   Chat ID: {os.environ.get('TELEGRAM_CHAT_ID', 'NOT FOUND')}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   Response: {e.response.text[:200]}")
        return False


def format_telegram_message(scan_date: str, regime: dict, candidates: list) -> str:
    """Build Telegram alert per spec."""
    lines = []
    if regime["spy_regime"] == "BEAR":
        lines.append("⚠️ BEAR REGIME — reduced size mode")

    n = len(candidates)
    if n == 0:
        return "💤 Q-ALPHA: No gap signals today."

    lines.append(f"🔍 Q-ALPHA MORNING SCAN — {scan_date}")
    lines.append(
        f"Universe: Full NYSE+NASDAQ | Regime: {regime['spy_regime']} | "
        f"VIX: {regime['vix_regime']}"
    )
    lines.append("─────────────────────")

    display = candidates[:5]
    for c in display:
        headline = c.get("catalyst_summary") or c.get("news_headline") or "No news found"
        plan = c.get("order_plan") or {}
        lines.append(
            f"📈 {c['ticker']} +{c['gap_estimate']:.1%} gap | "
            f"Vol: {c['pm_vol_ratio']:.1f}x"
        )
        if plan.get("valid"):
            lines.append(
                f"   Entry ~${plan['entry_price']:.2f} | "
                f"Stop ${plan['stop_price']:.2f} | "
                f"Target ${plan['target_2r']:.2f}"
            )
            lines.append(
                f"   Shares: {plan['shares']} | "
                f"Risk: ${plan['risk_dollars']:.0f} | "
                f"R/R: {plan['rr_ratio']:.1f}x"
            )
        elif plan:
            lines.append(f"   SKIP: {plan.get('skip_reason', 'invalid plan')}")
        lines.append(f"   {headline}")

    lines.append("─────────────────────")
    if n > 5:
        lines.append(f"Top 5 shown | {n} candidates total (all saved to scan JSON)")
    else:
        lines.append(f"{n} candidates total")
    return "\n".join(lines)


def run_scan_core(
    api_key: str,
    bot_token: str,
    chat_id: str,
    pool_state: dict | None = None,
) -> dict:
    """Execute full scan pipeline; returns output JSON payload."""
    t0 = time.time()
    now_et_dt = now_et()
    scan_date = now_et_dt.strftime("%Y-%m-%d")
    scan_time = now_et_dt.strftime("%H:%M:%S")
    today_news_gte = scan_date

    print("Scanning full NYSE + NASDAQ (~8,000 tickers)")
    print("STEP 1: Fetching full market snapshot...")
    candidates, funnel = get_all_gap_candidates(api_key, today_news_gte)

    print("STEP 2: SPY regime...")
    regime = fetch_spy_regime(api_key)

    print("STEP 3: Position sizing...")
    ps = load_position_sizer()
    pool_mgr = ps.PoolManager(
        state_path=state_path("pool_state.json"),
        initial_state=pool_state,
    )
    sizer = ps.PositionSizer()
    if pool_mgr.halt_check():
        print("  Pool halt active — sizing only, no new trades recommended")
    for candidate in candidates:
        atr = ps.get_atr14(
            candidate["ticker"],
            api_key,
            prev_close=candidate["prev_close"],
        )
        signal = ps.SignalInput(
            ticker=candidate["ticker"],
            prev_close=candidate["prev_close"],
            premarket_price=candidate["premarket_price"],
            atr_14=atr,
            gap_pct=candidate["gap_estimate"],
            vix_regime=regime["vix_regime"],
            spy_regime=regime["spy_regime"],
        )
        plan = sizer.calculate(signal, pool_mgr)
        candidate["order_plan"] = ps.order_plan_to_dict(plan)
        candidate["atr_14"] = atr
        if plan.valid:
            print(f"  {candidate['ticker']}: {plan.shares} shares @ "
                  f"${plan.entry_price:.2f}, risk ${plan.risk_dollars:.2f}")
        else:
            print(f"  {candidate['ticker']}: SKIP — {plan.skip_reason}")

    print(f"STEP 4: {len(candidates)} final candidates")

    payload = {
        "scan_date": scan_date,
        "scan_time": scan_time,
        "scan_mode": "full_market_snapshot",
        "funnel": funnel,
        "regime": {
            "spy_regime": regime["spy_regime"],
            "vix_regime": regime["vix_regime"],
            "spy_price": regime["spy_price"],
            "spy_sma50": regime["spy_sma50"],
        },
        "candidates": candidates,
        "total_candidates": len(candidates),
        "scan_duration_seconds": round(time.time() - t0, 2),
    }
    payload["pool_state"] = pool_mgr.state

    print("STEP 5: Scan complete (approvals queued separately)")
    print(f"  Date: {scan_date} {scan_time} ET")
    print(f"  Regime: {regime['spy_regime']} | VIX proxy: {regime['vix_proxy']} "
          f"({regime['vix_regime']})")
    print(f"  SPY: {regime['spy_price']:.2f} vs SMA50 {regime['spy_sma50']:.2f}")
    print(f"  Candidates: {len(candidates)}")
    for c in candidates[:10]:
        print(f"    #{c['rank']} {c['ticker']} gap={c['gap_estimate']:.1%} "
              f"vol={c['pm_vol_ratio']:.1f}x "
              f"news={'Y' if c['news_catalyst'] else 'N'}")
    print(f"  Duration: {payload['scan_duration_seconds']:.1f}s")

    return payload


def _save_scan_result(result: dict) -> dict | None:
    """Persist scan output and pool state to volume/local paths."""
    out_path = state_path(f"daily_scan_{result['scan_date']}.json")
    saved_pool = result.pop("pool_state", None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")

    pool_state_path = state_path("pool_state.json")
    if saved_pool:
        pool_state_path.write_text(
            json.dumps(saved_pool, indent=2), encoding="utf-8")
        print(f"Saved: {pool_state_path}")

    return saved_pool


def _queue_pending_approvals(result: dict, saved_pool: dict | None) -> None:
    """Save pending approvals and send Telegram alerts — exit immediately."""
    load_dotenv_if_available()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    pt = load_paper_trader_module()
    trader = pt.load_paper_trader(saved_pool)

    print("\nSTEP 12: Queue pending approvals...")
    if not trader.pool.can_open_trade():
        send_telegram("💤 Q-ALPHA: Pool at capacity. Monitoring only.",
                      bot_token, chat_id)
        print("  Pool at capacity — no new trades today")
    elif result["total_candidates"] == 0:
        print("  No candidates to approve")
    else:
        candidates = result["candidates"]
        open_tickers = trader.get_open_full_positions()
        before_count = len(candidates)
        candidates = [
            c for c in candidates
            if c["ticker"] not in open_tickers
        ]
        blocked = before_count - len(candidates)
        print(f"  Filtered {blocked} tickers already in portfolio")
        print(f"  {len(candidates)} candidates after portfolio filter")
        tradable = trader.filter_candidates_for_trading(candidates)
        if not tradable:
            send_telegram(
                "💤 Q-ALPHA: Signals found but no slots available today.",
                bot_token, chat_id,
            )
            print("  No tradable slots after filters")
        else:
            sent = trader.queue_pending_approvals(
                tradable, result["scan_date"], bot_token, chat_id,
            )
            print(f"  Queued {sent} alert(s) — replies at 9:25 AM, expire 9:45 AM ET")


def run_scan() -> dict | None:
    """
    Full morning scan pipeline: snapshot → gaps → sizing → approvals.
    Called by scheduler on Modal or manually via __main__.
    """
    if not is_trading_day():
        today = now_et().date()
        print(f"Market closed today ({today}). Skipping.")
        return None

    load_dotenv_if_available()
    print("=" * 55)
    print("  Q-ALPHA | Pre-Market Scanner")
    print("=" * 55)

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        print("ERROR: POLYGON_API_KEY not set")
        return None

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    pool_state_path = state_path("pool_state.json")
    pool_state = None
    if pool_state_path.exists():
        pool_state = json.loads(pool_state_path.read_text(encoding="utf-8"))
        print(f"  Pool: ${pool_state.get('pool', 0):,.2f} available")

    try:
        result = run_scan_core(api_key, bot_token, chat_id, pool_state=pool_state)
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        log_scan_error(err_msg + "\n" + traceback.format_exc())
        send_telegram(f"Q-ALPHA SCANNER ERROR: {err_msg}", bot_token, chat_id)
        print(f"SCAN FAILED: {err_msg}")
        return None

    saved_pool = _save_scan_result(result)
    send_telegram(
        format_telegram_message(
            result["scan_date"],
            result["regime"],
            result["candidates"],
        ),
        bot_token,
        chat_id,
    )
    _queue_pending_approvals(result, saved_pool)

    try:
        from candidates.supabase_sync import SupabaseSync

        sync = SupabaseSync()
        sync.upsert_scan(result)
        sync.log_health(
            "morning_scan",
            "OK",
            f"{len(result['candidates'])} candidates",
        )
        print("✅ Supabase sync successful")
    except Exception as e:
        print(f"❌ Supabase sync FAILED: {e}")
        import traceback

        traceback.print_exc()

    print(f"\nScan complete: {result['total_candidates']} candidates")
    return result


@app.function(
    image=image,
    timeout=900,
    memory=2048,
    secrets=[polygon_secret],
)
def run_premarket_scan(bot_token: str = "", chat_id: str = "",
                       pool_state: dict | None = None):
    """Modal entrypoint for daily pre-market scan."""
    load_dotenv_if_available()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not set")

    try:
        return run_scan_core(api_key, bot_token, chat_id, pool_state=pool_state)
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        send_telegram(f"Q-ALPHA SCANNER ERROR: {err_msg}", bot_token, chat_id)
        raise RuntimeError(err_msg) from exc


@app.local_entrypoint()
def main():
    run_scan()


if __name__ == "__main__":
    run_scan()
