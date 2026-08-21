"""
Q-ALPHA | Full-market gap scanner (READ-ONLY preview + candidate source).

Replaces the 300-name static universe as the source of daily gap candidates.
Pulls the ENTIRE US market from Polygon and applies the full Q-ALPHA filter
stack, ranks, and returns the top N. Read-only: it fetches data and writes a
candidate/log file. It NEVER places orders, touches pool_state, or connects to
IBKR.

Three data sources (all Polygon, all proven on this account):
  1. /v2/snapshot/.../tickers   — whole-market live-ish OHLCV (gap, price, today vol)
  2. /v3/reference/tickers?type=CS — common-stock UNIVERSE with real security NAMES
                                     (so the name-based ban filter actually works)
  3. /v2/aggs/grouped/.../{date}  — whole-market daily bars, one call per day, used
                                     to build an N-DAY AVERAGE VOLUME baseline

Filter stack (all must pass):
  1. Symbol is Polygon type == "CS"  AND  name is not a fund/derivative
     (closes the SOXS / NEBX leveraged-ETF hole — snapshot alone has no name)
  2. Opening gap in [MIN_GAP_PCT, MAX_GAP_PCT]
  3. Reference price in [MIN_PRICE, MAX_PRICE]
  4. PRIOR-DAY dollar volume >= MIN_DOLLAR_VOL             (liquidity)
     Uses yesterday's tape, NOT today's accumulating volume. At 9:20 AM
     today_vol is near-zero, so a today-$vol floor always returns 0 names.
  5. Pre-market relative volume >= MIN_PM_VOL_RATIO, where
        PM_RVOL = volume_so_far / (N-day_avg_vol * EXPECTED_PM_VOL_PCT)
     Same model as autonomous_agent / pre_market_scanner. Full-day RVOL
     (today / N-day avg) is impossible before the open and was the silent
     "no candidates" failure mode for many mornings.

Empirical-tuning note: MIN_PM_VOL_RATIO and MIN_DOLLAR_VOL are the thresholds
whose "right" value is unknown and MUST be set from data. Every passing
candidate is logged with raw gap_pct, dollar_vol and pm_vol_ratio so
entry_study.py can correlate them against realized R.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytz

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from universe_filter import EXCLUDE_SYMBOLS, is_leveraged_or_fund  # exact reuse

ET = pytz.timezone("America/New_York")

# ── Filter constants (mirror autonomous_agent.py; tune empirically) ─────────
MIN_GAP_PCT = 0.03
MAX_GAP_PCT = 0.50
MIN_PRICE = 5.00
MAX_PRICE = 50.00
# Liquidity = PRIOR day dollar volume (agent / pre_market_scanner use $2M).
# Never use today's accumulating $vol at 9:20 — that filter always zeros out.
MIN_DOLLAR_VOL = 2_000_000
# Pre-market volume model (NOT full-day RVOL). Expected PM share of a normal
# day is ~10%; require 1.5x that as confirmation the gap has real interest.
EXPECTED_PM_VOL_PCT = 0.10
MIN_PM_VOL_RATIO = 1.5
RVOL_LOOKBACK_DAYS = 5          # N-day average volume baseline (smooths quiet/heavy days)
TOP_N_CANDIDATES = 10
# Kept as an alias so older logs / callers reading MIN_RVOL still make sense.
MIN_RVOL = MIN_PM_VOL_RATIO

POLYGON_BASE = "https://api.polygon.io"
OUTPUT_DIR = CANDIDATES_DIR / "full_scan"
UNIVERSE_CACHE = OUTPUT_DIR / "cs_universe_cache.json"
UNIVERSE_CACHE_MAX_AGE_H = 20   # rebuild the CS universe once per trading day


def _load_polygon_key() -> str:
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    env_path = CANDIDATES_DIR.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found in environment or .env")


def _http_get_json(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 1. Common-stock universe with real names (for the ban filter) ───────────
def build_cs_universe(api_key: str) -> dict[str, dict]:
    """
    {symbol: {name, exchange}} for every ACTIVE type==CS ticker on NYSE/NASDAQ,
    with the leveraged/fund names removed via is_leveraged_or_fund(). Cached to
    disk so we pay the ~5-6k-name pagination once per trading day.
    """
    if UNIVERSE_CACHE.exists():
        age_h = (time.time() - UNIVERSE_CACHE.stat().st_mtime) / 3600
        if age_h < UNIVERSE_CACHE_MAX_AGE_H:
            cached = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
            print(f"  CS universe: {len(cached)} names (cached {age_h:.1f}h ago)")
            return cached

    print("  Building CS universe from Polygon /v3/reference/tickers ...")
    universe: dict[str, dict] = {}
    url = (f"{POLYGON_BASE}/v3/reference/tickers?type=CS&market=stocks"
           f"&active=true&limit=1000&apiKey={api_key}")
    dropped_fund = 0
    while url:
        data = _http_get_json(url)
        for r in data.get("results", []):
            sym = (r.get("ticker") or "").upper()
            name = r.get("name") or ""
            exch = r.get("primary_exchange") or ""
            if not sym:
                continue
            # Only NYSE / NASDAQ common stock.
            if exch not in ("XNYS", "XNAS", "ARCX", "BATS"):
                continue
            if sym in EXCLUDE_SYMBOLS or is_leveraged_or_fund(name):
                dropped_fund += 1
                continue
            universe[sym] = {"name": name, "exchange": exch}
        nxt = data.get("next_url")
        url = f"{nxt}&apiKey={api_key}" if nxt else None

    OUTPUT_DIR.mkdir(exist_ok=True)
    UNIVERSE_CACHE.write_text(json.dumps(universe), encoding="utf-8")
    print(f"  CS universe: {len(universe)} names (dropped {dropped_fund} fund/leveraged)")
    return universe


# ── 3. N-day average volume from grouped daily bars ─────────────────────────
def build_avg_volume(api_key: str, days: int) -> dict[str, float]:
    """
    {symbol: avg daily volume over the last `days` trading days}. Uses grouped
    daily bars: ONE call per date returns the whole market, so N calls total.
    Skips weekends/holidays automatically (empty results just contribute nothing).
    """
    print(f"  Building {days}-day avg volume baseline ...")
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    collected = 0
    probe = datetime.now(ET).date() - timedelta(days=1)  # start from yesterday
    tries = 0
    while collected < days and tries < days * 3:  # allow for weekends/holidays
        d = probe.strftime("%Y-%m-%d")
        try:
            data = _http_get_json(
                f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{d}"
                f"?adjusted=true&apiKey={api_key}")
        except Exception:
            data = {}
        results = data.get("results") or []
        if results:
            for r in results:
                sym = r.get("T")
                v = r.get("v")
                if sym and v:
                    totals[sym] = totals.get(sym, 0.0) + float(v)
                    counts[sym] = counts.get(sym, 0) + 1
            collected += 1
        probe -= timedelta(days=1)
        tries += 1
    avg = {s: totals[s] / counts[s] for s in totals if counts.get(s)}
    print(f"  Avg-volume baseline built for {len(avg)} symbols over {collected} sessions")
    return avg


def fetch_full_market_snapshot(api_key: str) -> list[dict]:
    url = (f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers"
           f"?apiKey={api_key}")
    data = _http_get_json(url)
    if data.get("status") not in ("OK", "DELAYED"):
        raise RuntimeError(f"Polygon snapshot status={data.get('status')}")
    return data.get("tickers", [])


def _reference_price(t: dict) -> float | None:
    """
    Best live/reference price for gap + filters.

    Pre-market (9:20) the regular-session day bar is often empty, so lastTrade
    and the latest minute bar must come before day.close. Falling back to
    prevDay.close would fabricate a 0% gap and silently drop real gappers.
    """
    day = t.get("day") or {}
    minute = t.get("min") or {}
    last_trade = t.get("lastTrade") or {}
    for v in (last_trade.get("p"), day.get("o"), minute.get("c"), day.get("c")):
        if v:
            try:
                price = float(v)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
    return None


def _opening_gap(t: dict) -> float | None:
    """(ref_price - prev_close) / prev_close using pre-market-aware ref price."""
    prev = (t.get("prevDay") or {}).get("c")
    if not prev:
        return None
    ref = _reference_price(t)
    if not ref:
        return None
    return (float(ref) - float(prev)) / float(prev)


def _volume_so_far(t: dict) -> float:
    """
    Shares traded so far today (pre-market + session).

    Prefer day.v; fall back to min.av (Polygon's accumulated daily volume on
    the latest minute bar), which is often the only non-zero field at 9:20.
    """
    day = t.get("day") or {}
    minute = t.get("min") or {}
    for v in (day.get("v"), minute.get("av"), minute.get("v")):
        if v:
            try:
                vol = float(v)
            except (TypeError, ValueError):
                continue
            if vol > 0:
                return vol
    return 0.0


def scan_market(api_key: str) -> tuple[list[dict], dict]:
    """
    Canonical full-market gap scan. Module constants (MIN_GAP_PCT, MIN_PRICE,
    MIN_DOLLAR_VOL, MIN_PM_VOL_RATIO, TOP_N_CANDIDATES, ...) are the single
    source of truth for thresholds. Standalone CLI and the agent both go
    through this function — never fork the filter stack.
    """
    cs_universe = build_cs_universe(api_key)
    avg_vol = build_avg_volume(api_key, RVOL_LOOKBACK_DAYS)
    snapshot = fetch_full_market_snapshot(api_key)

    stats = {
        "total_tickers": len(snapshot), "cs_universe_size": len(cs_universe),
        "dropped_no_data": 0, "dropped_not_cs": 0, "dropped_gap": 0,
        "dropped_price": 0, "dropped_dollar_vol": 0, "dropped_no_avgvol": 0,
        "dropped_rvol": 0, "passed": 0,
    }
    candidates: list[dict] = []

    for t in snapshot:
        ticker = (t.get("ticker") or "").upper()
        prev = t.get("prevDay") or {}
        prev_close = prev.get("c")
        prev_volume = float(prev.get("v") or 0)
        ref_price = _reference_price(t)
        gap = _opening_gap(t)
        if not ticker or not prev_close or ref_price is None or gap is None:
            stats["dropped_no_data"] += 1
            continue

        # CS + name-based ban filter (the SOXS/NEBX hole, now closed).
        if ticker not in cs_universe:
            stats["dropped_not_cs"] += 1
            continue

        if not (MIN_GAP_PCT <= gap <= MAX_GAP_PCT):
            stats["dropped_gap"] += 1
            continue
        if not (MIN_PRICE <= ref_price <= MAX_PRICE):
            stats["dropped_price"] += 1
            continue

        # Liquidity = PRIOR day. Today's $vol at 9:20 is near zero by definition.
        prev_dollar_vol = float(prev_close) * prev_volume
        if prev_dollar_vol < MIN_DOLLAR_VOL:
            stats["dropped_dollar_vol"] += 1
            continue

        base = avg_vol.get(ticker)
        if not base:
            stats["dropped_no_avgvol"] += 1
            continue

        today_vol = _volume_so_far(t)
        expected_pm_vol = base * EXPECTED_PM_VOL_PCT
        pm_vol_ratio = today_vol / expected_pm_vol if expected_pm_vol > 0 else 0.0
        if pm_vol_ratio < MIN_PM_VOL_RATIO:
            stats["dropped_rvol"] += 1
            continue

        candidates.append({
            "ticker": ticker,
            "name": cs_universe[ticker]["name"],
            "exchange": cs_universe[ticker]["exchange"],
            "prev_close": round(float(prev_close), 4),
            "ref_price": round(ref_price, 4),
            "gap_pct": round(gap * 100, 3),
            "today_vol": int(today_vol),
            "avg_vol_ndays": int(base),
            "dollar_vol": int(prev_dollar_vol),
            "rvol": round(pm_vol_ratio, 2),   # pre-market vol ratio (legacy key)
            "pm_vol_ratio": round(pm_vol_ratio, 2),
            "todays_change_pct": round(float(t.get("todaysChangePerc") or 0), 3),
        })
        stats["passed"] += 1

    # Rank: composite of gap and pm_vol_ratio (both normalized, capped so one
    # runaway value cannot dominate). PM ratio capped at 20x for ranking sanity.
    if candidates:
        max_gap = max(c["gap_pct"] for c in candidates) or 1
        for c in candidates:
            capped_rvol = min(c["rvol"], 20.0)
            c["rank_score"] = round(
                0.5 * (c["gap_pct"] / max_gap) + 0.5 * (capped_rvol / 20.0), 4)
        candidates.sort(key=lambda c: c["rank_score"], reverse=True)
        for i, c in enumerate(candidates, 1):
            c["rank"] = i

    return candidates, stats


# Back-compat alias — prefer scan_market() in new code.
scan = scan_market


def scan_for_agent(top_n: int = TOP_N_CANDIDATES) -> list[dict]:
    """
    Thin wrapper over scan_market(). Identical filter stack and module-constant
    defaults as the standalone CLI (main). ONLY reshapes returned candidates
    into the dict shape watch_and_enter / send_premarket_summary expect.

    Does NOT apply any additional gap / rvol / dollar-vol / news filtering.
    News catalyst is deferred: news_required effectively False (news_catalyst
    always False here). Read-only: no orders, no IBKR, no state mutation.
    """
    api_key = _load_polygon_key()
    # Same call as main() — no threshold overrides.
    candidates, stats = scan_market(api_key)
    top = candidates[:top_n]

    # Persist the raw scan so entry_study.py can correlate rvol / dollar_vol /
    # gap against realized R later (empirical threshold tuning).
    OUTPUT_DIR.mkdir(exist_ok=True)
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    (OUTPUT_DIR / f"scan_{date_str}.json").write_text(json.dumps({
        "scan_date": date_str,
        "scan_time_et": datetime.now(ET).strftime("%H:%M:%S"),
        "generated_by": "full_market_scan.scan_for_agent()",
        "source": "full_market_scan.scan_market()",
        "stats": stats, "candidates_all": candidates, "top_n": top,
    }, indent=2), encoding="utf-8")

    # Reshape only — no re-filtering.
    agent_candidates: list[dict] = []
    for c in top:
        agent_candidates.append({
            "ticker": c["ticker"],
            "last_price": c["ref_price"],
            "prev_close": c["prev_close"],
            "gap_pct": round(c["gap_pct"] / 100.0, 4),   # agent expects a fraction
            "pm_vol_ratio": c.get("pm_vol_ratio", c["rvol"]),
            "avg_volume": c["avg_vol_ndays"],
            "dollar_volume": c["dollar_vol"],
            "news_catalyst": False,        # news deferred; never filter on this
            "news_summary": "",
            "quality_score": round(c["rank_score"] * 100.0, 1),
        })

    print(
        f"  full_market_scan.scan_for_agent -> {len(agent_candidates)} candidates "
        f"(from {stats['passed']} passing / {stats['total_tickers']} tickers)"
    )
    return agent_candidates


def main() -> None:
    api_key = _load_polygon_key()
    print("Polygon key loaded (not printed).")
    now = datetime.now(ET)
    date_str = now.strftime("%Y-%m-%d")

    t0 = time.time()
    candidates, stats = scan_market(api_key)
    elapsed = time.time() - t0

    OUTPUT_DIR.mkdir(exist_ok=True)
    top = candidates[:TOP_N_CANDIDATES]
    log_path = OUTPUT_DIR / f"scan_{date_str}.json"
    log_path.write_text(json.dumps({
        "scan_date": date_str, "scan_time_et": now.strftime("%H:%M:%S"),
        "generated_by": "full_market_scan.scan_market()",
        "source": "full_market_scan.scan_market()",
        "constants": {
            "MIN_GAP_PCT": MIN_GAP_PCT, "MAX_GAP_PCT": MAX_GAP_PCT,
            "MIN_PRICE": MIN_PRICE, "MAX_PRICE": MAX_PRICE,
            "MIN_DOLLAR_VOL": MIN_DOLLAR_VOL,
            "MIN_PM_VOL_RATIO": MIN_PM_VOL_RATIO,
            "EXPECTED_PM_VOL_PCT": EXPECTED_PM_VOL_PCT,
            "RVOL_LOOKBACK_DAYS": RVOL_LOOKBACK_DAYS,
            "liquidity": "prior_day_dollar_vol",
            "volume_model": "pm_vol / (nday_avg * EXPECTED_PM_VOL_PCT)",
        },
        "stats": stats, "candidates_all": candidates, "top_n": top,
    }, indent=2), encoding="utf-8")

    print(f"\n============ FULL-MARKET SCAN: {date_str} ============")
    print(f"  Scanned {stats['total_tickers']} tickers in {elapsed:.1f}s")
    print(f"  CS universe {stats['cs_universe_size']} | not-CS drop {stats['dropped_not_cs']}"
          f" | gap {stats['dropped_gap']} | price {stats['dropped_price']}"
          f" | $vol {stats['dropped_dollar_vol']} | rvol {stats['dropped_rvol']}"
          f" | no-avgvol {stats['dropped_no_avgvol']}")
    print(f"  PASSED: {stats['passed']}  ->  top {len(top)}")
    print(f"  Full log -> full_scan/{log_path.name}\n")

    if not top:
        print("  No candidates passed the full filter stack.")
        return

    print("  Rk Ticker    Gap%   Ref$   RVOL  $Vol(M)  Chg%   Score  Name")
    for c in top:
        print(
            f"  {c['rank']:>2} {c['ticker']:<8} {c['gap_pct']:>5.1f}% "
            f"${c['ref_price']:>6.2f} {c['rvol']:>5.1f} "
            f"{c['dollar_vol']/1e6:>7.1f} {c['todays_change_pct']:>5.1f}% "
            f"{c['rank_score']:>6.3f}  {c['name'][:30]}"
        )
    print("\nDone. Read-only: no orders, no state changes.")


if __name__ == "__main__":
    main()
