"""
Peak Hour — tradable popularity / recent leaderboard evidence.

A first 1H print rarely puts a name on *today's* movers board. We ask:
  "Is this a popular stock to trade?" using multi-day history + live scanners.

Sources (StockTwits is optional supporting only):
  1. Polygon grouped daily — recent sessions top % gainers + top $volume
  2. Polygon live snapshot gainers (today, if available)
  3. TWS scanners MOST_ACTIVE / TOP_PERC_GAIN / HOT_BY_VOLUME (when paper up)
  4. News velocity already on the row (Polygon/TWS) as weak corroboration
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import requests

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
PIPELINE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PIPELINE_DIR / "results" / "popularity_cache"

LOOKBACK_SESSIONS = 10
TOP_GAINERS_PER_DAY = 40
TOP_DOLLAR_VOL_PER_DAY = 40
MIN_DAY_PCT = 0.05  # 5% day move to count as a gainer session
MIN_PRICE = 5.0
MIN_DAY_DOLLAR_VOL = 5_000_000.0

TWS_SCAN_CODES = (
    "TOP_PERC_GAIN",
    "MOST_ACTIVE",
    "HOT_BY_VOLUME",
)


def _trading_dates_back(n: int, *, as_of: datetime | None = None) -> list[str]:
    """Calendar backwalk skipping weekends (holidays still tried; empty days ignored)."""
    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    else:
        now = now.astimezone(ET)
    d = now.date()
    out: list[str] = []
    guard = 0
    while len(out) < n and guard < n * 4:
        guard += 1
        d = d - timedelta(days=1)
        if d.weekday() >= 5:
            continue
        out.append(d.isoformat())
    return out


def _day_movers_from_grouped(
    day: str,
    *,
    api_key: str,
) -> tuple[set[str], set[str]]:
    """
    From one grouped-daily file: top % gainers and top dollar-volume names.
    """
    url = f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{day}"
    try:
        data = polygon_get(url, {"adjusted": "true"}, api_key, timeout=90)
    except Exception as exc:
        print(f"  popularity grouped {day} warn: {exc}")
        return set(), set()
    results = data.get("results") or []
    gain_rows: list[tuple[float, str]] = []
    dvol_rows: list[tuple[float, str]] = []
    for r in results:
        sym = str(r.get("T") or "").upper()
        if not sym or not sym.isalpha():
            continue
        try:
            o = float(r.get("o") or 0)
            c = float(r.get("c") or 0)
            v = float(r.get("v") or 0)
            vw = float(r.get("vw") or c or 0)
        except (TypeError, ValueError):
            continue
        if c < MIN_PRICE or o <= 0:
            continue
        pct = (c - o) / o
        dvol = v * vw if vw > 0 else v * c
        if dvol < MIN_DAY_DOLLAR_VOL:
            continue
        if pct >= MIN_DAY_PCT:
            gain_rows.append((pct, sym))
        dvol_rows.append((dvol, sym))
    gain_rows.sort(reverse=True)
    dvol_rows.sort(reverse=True)
    gainers = {s for _, s in gain_rows[:TOP_GAINERS_PER_DAY]}
    active = {s for _, s in dvol_rows[:TOP_DOLLAR_VOL_PER_DAY]}
    return gainers, active


def build_recent_leaderboard(
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    sessions: int = LOOKBACK_SESSIONS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Multi-day leaderboard membership from Polygon grouped daily.

    Cached per calendar day under results/popularity_cache/.
    """
    key = api_key or load_polygon_key()
    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    cache_day = now.astimezone(ET).strftime("%Y%m%d")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"recent_leaderboard_{cache_day}.json"
    if cache_path.exists() and not force_refresh:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    dates = _trading_dates_back(sessions, as_of=now)
    by_day: dict[str, dict[str, list[str]]] = {}
    recent_gainers: set[str] = set()
    recent_active: set[str] = set()
    days_hit = 0
    for d in dates:
        g, a = _day_movers_from_grouped(d, api_key=key)
        if not g and not a:
            time.sleep(0.12)
            continue
        days_hit += 1
        by_day[d] = {"gainers": sorted(g), "most_active_dvol": sorted(a)}
        recent_gainers |= g
        recent_active |= a
        time.sleep(0.12)

    doc = {
        "built_at": datetime.now(ET).isoformat(),
        "as_of": now.isoformat(),
        "sessions_requested": sessions,
        "sessions_loaded": days_hit,
        "recent_gainer_symbols": sorted(recent_gainers),
        "recent_active_symbols": sorted(recent_active),
        "popular_symbols": sorted(recent_gainers | recent_active),
        "by_day": by_day,
    }
    cache_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(
        f"  Popularity board: {days_hit} sessions · "
        f"gainers={len(recent_gainers)} active$={len(recent_active)} "
        f"union={len(doc['popular_symbols'])}"
    )
    return doc


def fetch_polygon_live_movers(
    *,
    api_key: str | None = None,
) -> dict[str, set[str]]:
    """Live snapshot gainers (+ losers unused). Fail-open to empty sets."""
    key = api_key or load_polygon_key()
    out: dict[str, set[str]] = {"gainers": set(), "losers": set()}
    for direction in ("gainers", "losers"):
        url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/{direction}"
        try:
            resp = requests.get(url, params={"apiKey": key}, timeout=20)
            time.sleep(0.12)
            if resp.status_code != 200:
                continue
            for t in resp.json().get("tickers") or []:
                sym = str(t.get("ticker") or "").upper().strip()
                if sym:
                    out[direction].add(sym)
        except Exception as exc:
            print(f"  live {direction} warn: {exc}")
    return out


def fetch_tws_popularity_scanners(
    *,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 98,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """
    IBKR paper scanners: TOP_PERC_GAIN, MOST_ACTIVE, HOT_BY_VOLUME.

    Returns {ok, symbols, by_code, error}. Empty/ok=False if TWS down.
    """
    try:
        from ib_insync import IB, ScannerSubscription
    except Exception as exc:
        return {"ok": False, "symbols": [], "by_code": {}, "error": f"ib_insync:{exc}"}

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout)
    except Exception as exc:
        try:
            ib.disconnect()
        except Exception:
            pass
        return {"ok": False, "symbols": [], "by_code": {}, "error": str(exc)}

    by_code: dict[str, list[str]] = {}
    all_syms: set[str] = set()
    try:
        for code in TWS_SCAN_CODES:
            try:
                sub = ScannerSubscription(
                    instrument="STK",
                    locationCode="STK.US.MAJOR",
                    scanCode=code,
                    numberOfRows=25,
                )
                rows = ib.reqScannerData(sub)
                ib.sleep(0.45)
                syms: list[str] = []
                for sd in rows or []:
                    cd = getattr(sd, "contractDetails", None)
                    c = getattr(cd, "contract", None) if cd else None
                    sym = str(getattr(c, "symbol", "") or "").upper()
                    if sym:
                        syms.append(sym)
                        all_syms.add(sym)
                by_code[code] = syms
            except Exception as exc:
                by_code[code] = []
                print(f"  TWS scanner {code} warn: {exc}")
        return {
            "ok": True,
            "symbols": sorted(all_syms),
            "by_code": by_code,
            "error": None,
        }
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def build_popularity_context(
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    include_tws: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Full popularity pack for Attention / Case.

    popular_symbols = recent multi-day leaders ∪ live gainers ∪ TWS scanner hits.
    """
    key = api_key or load_polygon_key()
    hist = build_recent_leaderboard(
        api_key=key, as_of=as_of, force_refresh=force_refresh,
    )
    live = fetch_polygon_live_movers(api_key=key)
    tws = (
        fetch_tws_popularity_scanners()
        if include_tws
        else {"ok": False, "symbols": [], "by_code": {}, "error": "skipped"}
    )

    recent = set(hist.get("popular_symbols") or [])
    live_g = set(live.get("gainers") or [])
    tws_syms = set(tws.get("symbols") or [])
    popular = recent | live_g | tws_syms

    ctx = {
        "recent_gainer_symbols": set(hist.get("recent_gainer_symbols") or []),
        "recent_active_symbols": set(hist.get("recent_active_symbols") or []),
        "live_gainers": live_g,
        "tws_symbols": tws_syms,
        "tws_ok": bool(tws.get("ok")),
        "tws_by_code": tws.get("by_code") or {},
        "popular_symbols": popular,
        "sessions_loaded": int(hist.get("sessions_loaded") or 0),
    }
    print(
        f"  Popularity context: recent={len(recent)} live_gainers={len(live_g)} "
        f"tws={len(tws_syms)} ok={ctx['tws_ok']} union={len(popular)}"
    )
    return ctx


def annotate_popularity(
    row: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Attach popularity flags used by momentum_context / attention."""
    out = dict(row)
    sym = str(out.get("symbol") or "").upper()
    recent_g = sym in (ctx.get("recent_gainer_symbols") or set())
    recent_a = sym in (ctx.get("recent_active_symbols") or set())
    live_g = sym in (ctx.get("live_gainers") or set())
    tws_hit = sym in (ctx.get("tws_symbols") or set())
    popular = sym in (ctx.get("popular_symbols") or set())
    # Days hit among cached by_day (optional detail)
    days = 0
    # Not always present as sets of day membership — approximate from flags
    if recent_g or recent_a:
        days = 1
    out["on_gainers"] = live_g  # keep name = *today* snapshot
    out["recent_leaderboard"] = bool(recent_g or recent_a)
    out["recent_gainer"] = recent_g
    out["recent_most_active"] = recent_a
    out["tws_popular"] = tws_hit
    out["tradable_popular"] = popular
    out["popularity_days_hint"] = days
    return out
