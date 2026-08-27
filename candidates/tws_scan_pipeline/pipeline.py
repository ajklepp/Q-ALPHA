"""
TWS morning scan pipeline (Phase 2) — list → lanes → PM score → TRADE shortlist.

Polygon remains SoT for market_cap + ticker_profiler history.
TWS is SoT for the morning mover list + preferred PM quotes/bars.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PKG = Path(__file__).resolve().parent
CANDIDATES = PKG.parent
ROOT = CANDIDATES.parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from position_sizer import load_pool_value, max_affordable_price  # noqa: E402
from universe_filter import (  # noqa: E402
    is_leveraged_or_fund,
    passes_instrument_safety,
)

ET = pytz.timezone("America/New_York")
POLYGON_BASE = "https://api.polygon.io"

# Aaron locked lanes (2026-08-25) — do not renegotiate in code comments alone.
MCAP_TRADE_MIN = 150_000_000
MCAP_LEARN_MIN = 50_000_000

SCAN_CODES = ("TOP_PERC_GAIN", "MOST_ACTIVE", "HOT_BY_VOLUME")
LOCATION = "STK.US.MAJOR"
INSTRUMENT = "STK"
# API-side ETF strip (spike 2026-08-26): stockTypeFilter=CORP removes SOXL/TQQQ/NVDL.
# CORP,ADR re-admitted NVDL — do not use. marketCapAbove/Below on ScannerSubscription
# returns 0 rows on paper (error 165). TagValue AVGVOLUME/CHANGEPERC disabled on paper.
# Keep Polygon mcap lanes + passes_instrument_safety post-filter.
SCANNER_STOCK_TYPE_FILTER = "CORP"
# Production funnel (Aaron 2026-08-25 addendum) — not the spike's 25-row cap.
SCAN_ROWS_PER_CODE = 50  # IB max per scanner is typically ~50
SCAN_ROWS = SCAN_ROWS_PER_CODE  # alias
TARGET_UNIVERSE = 100  # hunting-set target after union+dedupe (~3×50)
WATCH_TOP_N = 10  # Telegram / dashboard / profiles / session tracker
TRADE_TOP_N = 3  # MAX_TRADES_DAY entries from TRADE lane only

# Hard TRADE gap floor (not a soft AND with pm_vol): drop RHI-style ~0% names.
MIN_GAP_FRAC_TRADE = 0.03
MIN_PM_VOL_TRADE = 1.5  # retained for diagnostics; not required for TRADE hard drop

RESULTS_DIR = PKG / "results"
LEARN_DIR = CANDIDATES / "full_scan"

REFILL_0940_ENABLED = False  # stub — stay OFF until explicit approve


def _load_polygon_key() -> str:
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found in environment or .env")


def _http_get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _finite(val: Any) -> float | None:
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _bar_open(b) -> float | None:
    return _finite(getattr(b, "open_", None) if hasattr(b, "open_") else getattr(b, "open", None))


def assign_lane(market_cap: float | None) -> str:
    """TRADE / LEARN / IGNORE from Aaron mcap bands. Missing mcap → IGNORE."""
    if market_cap is None or market_cap < 0:
        return "IGNORE"
    if market_cap >= MCAP_TRADE_MIN:
        return "TRADE"
    if market_cap >= MCAP_LEARN_MIN:
        return "LEARN"
    return "IGNORE"


def fetch_polygon_mcap(ticker: str, api_key: str) -> dict[str, Any]:
    """Polygon /v3/reference/tickers/{t} — market_cap + name + type."""
    url = f"{POLYGON_BASE}/v3/reference/tickers/{ticker.upper()}?apiKey={api_key}"
    try:
        doc = _http_get_json(url, timeout=20)
        r = doc.get("results") or {}
        mcap = r.get("market_cap")
        mcap_f = float(mcap) if mcap is not None else None
        return {
            "market_cap": mcap_f,
            "name": str(r.get("name") or ""),
            "type": str(r.get("type") or ""),
            "primary_exchange": str(r.get("primary_exchange") or ""),
            "ok": True,
        }
    except Exception as exc:
        return {
            "market_cap": None,
            "name": "",
            "type": "",
            "primary_exchange": "",
            "ok": False,
            "error": str(exc),
        }


def _scan_row_symbol(sd) -> str:
    cd = getattr(sd, "contractDetails", None)
    c = getattr(cd, "contract", None) if cd is not None else None
    return str(getattr(c, "symbol", "") or "").upper()


def _scan_row_meta(sd) -> dict[str, Any]:
    cd = getattr(sd, "contractDetails", None)
    c = getattr(cd, "contract", None) if cd is not None else None
    return {
        "symbol": str(getattr(c, "symbol", "") or "").upper(),
        "rank": getattr(sd, "rank", None),
        "conId": getattr(c, "conId", None) if c else None,
        "longName": getattr(cd, "longName", None) if cd else None,
        "stockType": getattr(cd, "stockType", None) if cd else None,
        "primaryExchange": (
            getattr(c, "primaryExchange", None) or getattr(c, "exchange", None)
        ) if c else None,
    }


def fetch_tws_scanner_union(ib, *, rows: int = SCAN_ROWS_PER_CODE) -> list[dict[str, Any]]:
    """
    Union+dedupe TOP_PERC_GAIN / MOST_ACTIVE / HOT_BY_VOLUME (~IB max rows each).

    Applies SCANNER_STOCK_TYPE_FILTER at request time (CORP). Does NOT set
    marketCapAbove/Below — paper returns empty (spike). Post-filter still required.
    """
    from ib_insync import ScannerSubscription

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    print(
        f"  [tws_scan] API stockTypeFilter={SCANNER_STOCK_TYPE_FILTER!r} "
        f"(mcap TagValues not applied — paper disabled / empty)"
    )
    for code in SCAN_CODES:
        sub = ScannerSubscription(
            instrument=INSTRUMENT,
            locationCode=LOCATION,
            scanCode=code,
            numberOfRows=rows,
            stockTypeFilter=SCANNER_STOCK_TYPE_FILTER,
        )
        try:
            data = ib.reqScannerData(sub) or []
        except Exception as exc:
            print(f"  [tws_scan] scanner {code} FAIL: {exc}")
            counts[code] = 0
            continue
        counts[code] = len(data)
        print(f"  [tws_scan] scanner {code}: {len(data)} rows")
        for sd in data:
            meta = _scan_row_meta(sd)
            sym = meta["symbol"]
            if not sym or sym in seen:
                continue
            seen.add(sym)
            meta["scan_codes"] = [code]
            out.append(meta)
        time.sleep(0.35)
    print(f"  [tws_scan] union unique={len(out)}  counts={counts}")
    return out


def ib_contract_enrich(ib, symbol: str) -> dict[str, Any]:
    """Qualify + contractDetails for stockType / longName."""
    from ib_insync import Stock

    out: dict[str, Any] = {
        "symbol": symbol,
        "stock_type": "",
        "name": "",
        "ok": False,
    }
    try:
        contract = Stock(symbol, "SMART", "USD")
        q = ib.qualifyContracts(contract)
        if not q:
            return out
        contract = q[0]
        details = ib.reqContractDetails(contract) or []
        if details:
            d0 = details[0]
            out["stock_type"] = str(getattr(d0, "stockType", "") or "")
            out["name"] = str(getattr(d0, "longName", "") or "")
            out["ok"] = True
        out["contract"] = contract
    except Exception as exc:
        out["error"] = str(exc)
    return out


def pm_features_tws(ib, symbol: str) -> dict[str, Any] | None:
    """
    Premarket features from TWS extended-hours hist + snapshot.
    Returns None if no usable quote (caller falls back to Polygon).
    """
    from ib_insync import Stock

    try:
        contract = Stock(symbol, "SMART", "USD")
        q = ib.qualifyContracts(contract)
        if not q:
            return None
        contract = q[0]

        # Prior close from daily RTH bars.
        daily = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="5 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        if not daily or len(daily) < 1:
            return None
        prev_close = _finite(daily[-1].close)
        # If last daily bar is today and session open, prefer prior day.
        now = datetime.now(ET)
        if len(daily) >= 2 and now.hour >= 9:
            # Prefer second-to-last as prev close when last may be today.
            try:
                last_d = daily[-1].date
                if hasattr(last_d, "date"):
                    last_day = last_d.date()
                else:
                    last_day = last_d
                if str(last_day)[:10] == now.strftime("%Y-%m-%d") and len(daily) >= 2:
                    prev_close = _finite(daily[-2].close)
            except Exception:
                pass
        if prev_close is None or prev_close <= 0:
            return None

        ticker = ib.reqMktData(contract, "", True, False)
        ib.sleep(1.5)
        last = _finite(ticker.last)
        close = _finite(ticker.close)
        bid = _finite(ticker.bid)
        ask = _finite(ticker.ask)
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
        ref = last or ((bid + ask) / 2 if bid and ask else None) or close
        if ref is None or ref <= 0:
            return None

        # Extended 5m bars for PM volume estimate.
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
        ) or []
        pm_vol = 0.0
        session_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        for b in bars:
            try:
                bt = b.date
                if hasattr(bt, "tzinfo") and bt.tzinfo is None:
                    bt = ET.localize(bt)
                if bt < session_open:
                    pm_vol += float(b.volume or 0)
            except Exception:
                continue

        # Avg daily volume from daily bars (shares).
        vols = [float(b.volume or 0) for b in daily[:-1] if _finite(b.volume)]
        avg_vol = sum(vols) / len(vols) if vols else 0.0
        expected_pm = avg_vol * 0.10 if avg_vol > 0 else 0.0
        pm_vol_ratio = (pm_vol / expected_pm) if expected_pm > 0 else 0.0
        gap_pct = (ref / prev_close) - 1.0
        dollar_vol = prev_close * avg_vol if avg_vol else 0.0

        return {
            "last_price": float(ref),
            "prev_close": float(prev_close),
            "gap_pct": float(gap_pct),
            "pm_vol_ratio": float(pm_vol_ratio),
            "avg_volume": float(avg_vol),
            "dollar_volume": float(dollar_vol),
            "pm_source": "tws",
        }
    except Exception as exc:
        print(f"  [tws_scan] TWS PM fail {symbol}: {exc}")
        return None


def pm_features_polygon(ticker: str, api_key: str) -> dict[str, Any] | None:
    """Polygon snapshot fallback when TWS has no quote."""
    url = (
        f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/"
        f"{ticker.upper()}?apiKey={api_key}"
    )
    try:
        doc = _http_get_json(url, timeout=20)
        t = doc.get("ticker") or {}
        day = t.get("day") or {}
        prev = t.get("prevDay") or {}
        last_q = (t.get("lastTrade") or {}).get("p") or (t.get("lastQuote") or {}).get("P")
        ref = _finite(last_q) or _finite(day.get("c")) or _finite(day.get("o"))
        prev_close = _finite(prev.get("c"))
        if ref is None or prev_close is None or prev_close <= 0:
            return None
        today_vol = float(day.get("v") or 0)
        prev_vol = float(prev.get("v") or 0)
        expected_pm = prev_vol * 0.10 if prev_vol > 0 else 0.0
        pm_vol_ratio = (today_vol / expected_pm) if expected_pm > 0 else 0.0
        return {
            "last_price": float(ref),
            "prev_close": float(prev_close),
            "gap_pct": float((ref / prev_close) - 1.0),
            "pm_vol_ratio": float(pm_vol_ratio),
            "avg_volume": float(prev_vol),
            "dollar_volume": float(prev_close * prev_vol),
            "pm_source": "polygon",
        }
    except Exception as exc:
        print(f"  [tws_scan] Polygon PM fail {ticker}: {exc}")
        return None


def score_row(c: dict) -> float:
    """Mirror autonomous_agent.score_candidate (0–100). No $5 hard reject."""
    score = 0.0
    gap = float(c.get("gap_pct") or 0)
    vol_ratio = float(c.get("pm_vol_ratio") or 0)
    price = float(c.get("prev_close") or c.get("last_price") or 0)
    has_news = bool(c.get("news_catalyst"))
    dollar_vol = float(c.get("dollar_volume") or 0)

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
    elif 0 < price < 5:
        score += 10  # sub-$5 allowed when mcap lane says TRADE
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


def _agent_shaped(row: dict) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "last_price": row["last_price"],
        "prev_close": row["prev_close"],
        "gap_pct": row["gap_pct"],
        "pm_vol_ratio": row["pm_vol_ratio"],
        "avg_volume": row.get("avg_volume", 0.0),
        "dollar_volume": row.get("dollar_volume", 0.0),
        "news_catalyst": False,
        "news_summary": "",
        "quality_score": row["quality_score"],
        "source": "tws_scan",
        "skip_cs_cache": True,
        "name": row.get("name") or "",
        "stock_type": row.get("stock_type") or "",
        "market_cap": row.get("market_cap"),
        "lane": row.get("lane"),
        "pm_source": row.get("pm_source"),
    }


def persist_learn_file(learn_rows: list[dict], *, day: str | None = None) -> Path:
    """Write LEARN lane scores for the day (never ordered)."""
    day = day or datetime.now(ET).strftime("%Y-%m-%d")
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day,
        "generated_at": datetime.now(ET).isoformat(),
        "lane": "LEARN",
        "mcap_band": f"[{MCAP_LEARN_MIN}, {MCAP_TRADE_MIN})",
        "n": len(learn_rows),
        "rows": learn_rows,
    }
    primary = LEARN_DIR / f"learn_{day}.json"
    primary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    mirror = RESULTS_DIR / f"learn_{day}.json"
    mirror.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  [tws_scan] LEARN file → {primary} ({len(learn_rows)} names)")
    return primary


def persist_scan_snapshot(
    stats: dict,
    watch: list,
    trade: list,
    learn: list,
    ignore: list,
    *,
    scanner_union_raw: list | None = None,
    scored_all: list | None = None,
    filter_stats: dict | None = None,
) -> Path:
    day = datetime.now(ET).strftime("%Y-%m-%d")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"scan_{day}.json"
    path.write_text(json.dumps({
        "date": day,
        "generated_at": datetime.now(ET).isoformat(),
        "funnel": {
            "SCAN_ROWS_PER_CODE": SCAN_ROWS_PER_CODE,
            "TARGET_UNIVERSE": TARGET_UNIVERSE,
            "WATCH_TOP_N": WATCH_TOP_N,
            "TRADE_TOP_N": TRADE_TOP_N,
            "SCANNER_STOCK_TYPE_FILTER": SCANNER_STOCK_TYPE_FILTER,
            "MIN_GAP_FRAC_TRADE": MIN_GAP_FRAC_TRADE,
        },
        "stats": stats,
        "filter_stats": filter_stats or {},
        "scanner_union_raw": scanner_union_raw or [],
        "scored_all": scored_all or [],
        "watch": watch,
        "trade": trade,
        "learn": learn,
        "ignore_sample": ignore[:40],
        "refill_0940": REFILL_0940_ENABLED,
    }, indent=2), encoding="utf-8")
    return path


def run_morning_pipeline(
    ib,
    *,
    watch_n: int = WATCH_TOP_N,
    trade_n: int = TRADE_TOP_N,
    api_key: str | None = None,
    enrich_limit: int | None = None,
) -> dict[str, Any]:
    """
    Production funnel:
      LIST (~50×3 union) → gates → score full shortlist →
      watch top watch_n (TRADE+LEARN) → trade top trade_n (TRADE only).

    No orders. LEARN never enters the trade list.
    """
    api_key = api_key or _load_polygon_key()
    pool = load_pool_value()
    max_px = max_affordable_price(pool)

    print(
        f"  [tws_scan] funnel rows/scanner={SCAN_ROWS_PER_CODE}  "
        f"target_universe~{TARGET_UNIVERSE}  "
        f"watch={watch_n}  trade={trade_n}"
    )
    print(
        f"  [tws_scan] lanes TRADE>=${MCAP_TRADE_MIN/1e6:.0f}M  "
        f"LEARN>=${MCAP_LEARN_MIN/1e6:.0f}M  "
        f"pool=${pool:.0f} max_affordable=${max_px:.2f}  "
        f"(no $5 floor)"
    )
    if REFILL_0940_ENABLED:
        print("  [tws_scan] WARNING: 09:40 refill flag ON (unexpected)")
    else:
        print("  [tws_scan] 09:40 refill: OFF (stub)")

    raw = fetch_tws_scanner_union(ib, rows=SCAN_ROWS_PER_CODE)
    if enrich_limit is not None:
        raw = raw[: int(enrich_limit)]
        print(f"  [tws_scan] enrich_limit={enrich_limit} (dry/debug only)")

    trade_lane: list[dict] = []
    learn_rows: list[dict] = []
    ignore_rows: list[dict] = []
    scored_all: list[dict] = []
    stats = {
        "scanner_union": len(raw),
        "scored_shortlist": 0,
        "trade_lane": 0,
        "trade_entry_eligible": 0,
        "learn": 0,
        "ignore": 0,
        "no_quote": 0,
        "blocked_safety": 0,
        "blocked_affordability": 0,
        "blocked_gap_quality": 0,
    }
    filter_stats = {
        "api_stock_type_filter": SCANNER_STOCK_TYPE_FILTER,
        "api_mcap_filter_applied": False,  # paper: subscription mcap → empty
        "api_tagvalue_avgvol_change": False,  # paper: filters disabled
        "post_filter_etf_or_safety": 0,
        "trade_hard_dropped_gap_lt_3pct": 0,
        "scanner_union_raw_n": len(raw),
    }

    for i, meta in enumerate(raw, start=1):
        sym = meta["symbol"]
        print(f"  [tws_scan] ({i}/{len(raw)}) enrich {sym}", flush=True)

        ib_meta = ib_contract_enrich(ib, sym)
        poly = fetch_polygon_mcap(sym, api_key)
        time.sleep(0.12)

        name = poly.get("name") or ib_meta.get("name") or meta.get("longName") or ""
        stock_type = ib_meta.get("stock_type") or meta.get("stockType") or ""
        mcap = poly.get("market_cap")
        lane = assign_lane(mcap)

        if not passes_instrument_safety(
            sym, name=name, stock_type=stock_type, require_cs_cache=False,
        ):
            stats["blocked_safety"] += 1
            filter_stats["post_filter_etf_or_safety"] += 1
            ignore_rows.append({
                "ticker": sym, "lane": "IGNORE", "reason": "safety",
                "market_cap": mcap, "stock_type": stock_type, "name": name,
            })
            continue

        poly_type = str(poly.get("type") or "").upper()
        if poly_type in {"ETF", "ETN", "ETS", "FUND"} or is_leveraged_or_fund(name):
            stats["blocked_safety"] += 1
            filter_stats["post_filter_etf_or_safety"] += 1
            ignore_rows.append({
                "ticker": sym, "lane": "IGNORE", "reason": "fund/etf",
                "market_cap": mcap, "type": poly_type, "name": name,
            })
            continue

        if lane == "IGNORE":
            # Still optional: skip PM work for IGNORE to save time.
            stats["ignore"] += 1
            ignore_rows.append({
                "ticker": sym, "lane": "IGNORE", "reason": "mcap_below_50m",
                "market_cap": mcap, "name": name, "stock_type": stock_type,
            })
            continue

        pm = pm_features_tws(ib, sym)
        if pm is None:
            pm = pm_features_polygon(sym, api_key)
        if pm is None:
            stats["no_quote"] += 1
            ignore_rows.append({
                "ticker": sym, "lane": lane, "reason": "no_quote",
                "market_cap": mcap,
            })
            continue

        row = {
            "ticker": sym,
            "name": name,
            "stock_type": stock_type,
            "market_cap": mcap,
            "lane": lane,
            **pm,
        }
        row["quality_score"] = score_row(row)
        shaped = _agent_shaped(row)
        stats["scored_shortlist"] += 1
        scored_all.append(shaped)

        if lane == "LEARN":
            stats["learn"] += 1
            learn_rows.append(shaped)
            continue

        # TRADE lane — hard drop gap < 3% (RHI-style 0% names must not clog).
        gap_abs = abs(float(row.get("gap_pct") or 0.0))
        if gap_abs < MIN_GAP_FRAC_TRADE:
            stats["blocked_gap_quality"] += 1
            filter_stats["trade_hard_dropped_gap_lt_3pct"] += 1
            shaped["entry_eligible"] = False
            shaped["entry_block"] = "hard_gap_lt_3pct"
            ignore_rows.append({
                "ticker": sym, "lane": "TRADE", "reason": "hard_gap_lt_3pct",
                "market_cap": mcap, "gap_pct": row.get("gap_pct"),
            })
            continue

        stats["trade_lane"] += 1
        shaped["entry_eligible"] = True
        if row["last_price"] > max_px:
            shaped["entry_eligible"] = False
            shaped["entry_block"] = "above_max_affordable"
            stats["blocked_affordability"] += 1
        else:
            stats["trade_entry_eligible"] += 1
        trade_lane.append(shaped)

    trade_lane.sort(key=lambda x: x["quality_score"], reverse=True)
    learn_rows.sort(key=lambda x: x["quality_score"], reverse=True)

    # Watch = top N across TRADE + LEARN (LEARN may appear; never bracketed).
    watch_pool = list(trade_lane) + list(learn_rows)
    watch_pool.sort(key=lambda x: x["quality_score"], reverse=True)
    watch = watch_pool[: int(watch_n)]

    # Trade entries = top N from TRADE lane that are entry-eligible only.
    trade_eligible = [c for c in trade_lane if c.get("entry_eligible")]
    trade = trade_eligible[: int(trade_n)]

    persist_learn_file(learn_rows)
    persist_scan_snapshot(
        stats, watch, trade, learn_rows, ignore_rows,
        scanner_union_raw=raw,
        scored_all=scored_all,
        filter_stats=filter_stats,
    )

    print(
        f"  [tws_scan] DONE scored={stats['scored_shortlist']} "
        f"trade_lane={stats['trade_lane']} "
        f"entry_eligible={stats['trade_entry_eligible']} "
        f"learn={stats['learn']} ignore={stats['ignore']} "
        f"safety_block={stats['blocked_safety']} "
        f"gap_hard_drop={filter_stats['trade_hard_dropped_gap_lt_3pct']} "
        f"post_etf_safety={filter_stats['post_filter_etf_or_safety']} "
        f"→ watch={len(watch)} trade={len(trade)}"
    )
    for idx, c in enumerate(watch, start=1):
        print(
            f"    WATCH #{idx} [{c.get('lane')}] {c['ticker']}  "
            f"mcap=${(c.get('market_cap') or 0)/1e6:.1f}M  "
            f"px=${c['last_price']:.2f}  score={c['quality_score']:.0f}"
        )
    for idx, c in enumerate(trade, start=1):
        print(
            f"    TRADE #{idx} {c['ticker']}  "
            f"mcap=${(c.get('market_cap') or 0)/1e6:.1f}M  "
            f"px=${c['last_price']:.2f}  gap={c['gap_pct']:+.1%}  "
            f"score={c['quality_score']:.0f}  pm={c.get('pm_source')}"
        )

    return {
        "watch": watch,
        "trade": trade,
        "trade_all": trade_lane,
        "learn": learn_rows,
        "ignore": ignore_rows,
        "stats": stats,
        "filter_stats": filter_stats,
        "scanner_union_raw": raw,
        "scored_all": scored_all,
        "pool": pool,
        "max_affordable_price": max_px,
    }
