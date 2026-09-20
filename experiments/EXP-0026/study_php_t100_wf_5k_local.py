#!/usr/bin/env python3
"""
EXP-0026 local Polygon runner — same study as study_php_t100_wf_5k_modal.py.

Used when Modal CLI auth (MODAL_TOKEN_*) is unavailable but POLYGON_API_KEY is set.
Produces real P&L only; never invents numbers. No IBKR / Peak Hour live changes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz
import requests

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
CANDIDATES = REPO / "candidates"
PIPELINE = CANDIDATES / "tsd_scan_pipeline"
VENDOR = EXP_DIR / "vendor_track100"

POLYGON = "https://api.polygon.io"
SLEEP = 0.12
DAILY_WARMUP_START = "2025-10-01"
H1_WARMUP_START = "2025-10-01"
# Modest parallelism; each worker still sleeps SLEEP between pages.
H1_WORKERS = 4


def _polygon_get(url: str, params: dict[str, Any], retries: int = 4) -> dict[str, Any]:
    """GET with retry — mirrors Modal study helper."""
    last: Exception | None = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(0.8 * (i + 1))
                continue
            if r.status_code in (403, 404):
                return {"_status": r.status_code}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    return {"_error": str(last)[:200] if last else "unknown"}


def _fetch_aggs(
    symbol: str,
    *,
    start: str,
    end: str,
    timespan: str,
    key: str,
) -> list[dict[str, Any]]:
    """Paginated Polygon aggregates for one ticker."""
    url = f"{POLYGON}/v2/aggs/ticker/{symbol}/range/1/{timespan}/{start}/{end}"
    params: dict[str, Any] = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": key,
    }
    rows: list[dict[str, Any]] = []
    pages = 0
    while url and pages < 12:
        data = _polygon_get(url, params)
        time.sleep(SLEEP)
        for b in data.get("results") or []:
            try:
                rows.append({
                    "t": int(b["t"]),
                    "o": float(b["o"]),
                    "h": float(b["h"]),
                    "l": float(b["l"]),
                    "c": float(b["c"]),
                    "v": float(b.get("v") or 0),
                })
            except Exception:
                continue
        nxt = data.get("next_url")
        url = str(nxt) if nxt else ""
        params = {"apiKey": key} if url else {}
        pages += 1
        if not data.get("results"):
            break
    return rows


def fetch_1h_symbol(symbol: str, start: str, end: str, key: str) -> dict[str, Any]:
    """One ticker of Polygon 1H bars (warmup → window end)."""
    bars = _fetch_aggs(symbol, start=start, end=end, timespan="hour", key=key)
    return {"symbol": symbol.upper(), "ok": 1 if len(bars) >= 80 else 0, "bars": bars}


def fetch_grouped_dailies(start: str, end: str, key: str) -> dict[str, Any]:
    """Polygon grouped dailies for causal HTF / dollar-vol membership."""
    start_d = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    days: dict[str, list[dict[str, Any]]] = {}
    d = start_d
    n_ok = 0
    n_days_total = 0
    while d <= end_d:
        if d.weekday() < 5:
            n_days_total += 1
            ds = d.isoformat()
            data = _polygon_get(
                f"{POLYGON}/v2/aggs/grouped/locale/us/market/stocks/{ds}",
                {"adjusted": "true", "apiKey": key},
            )
            time.sleep(SLEEP)
            rows = []
            for b in data.get("results") or []:
                sym = str(b.get("T") or "").upper()
                if not sym or len(sym) > 5 or "." in sym:
                    continue
                try:
                    rows.append({
                        "T": sym,
                        "o": float(b.get("o") or 0),
                        "h": float(b.get("h") or 0),
                        "l": float(b.get("l") or 0),
                        "c": float(b.get("c") or 0),
                        "v": float(b.get("v") or 0),
                    })
                except Exception:
                    continue
            if rows:
                days[ds] = rows
                n_ok += 1
            if n_days_total % 25 == 0:
                print(f"  grouped dailies {ds} ok={n_ok}/{n_days_total}", flush=True)
        d += timedelta(days=1)
    return {"ok": 1 if n_ok else 0, "n_days": n_ok, "days": days}


def main() -> int:
    """Run the Aaron-locked mini WF locally and write results.md / results.json."""
    sys.path.insert(0, str(CANDIDATES))
    sys.path.insert(0, str(PIPELINE))
    sys.path.insert(0, str(EXP_DIR))
    sys.path.insert(0, str(VENDOR))
    os.environ.setdefault("PHP_EQUAL_SIGNAL", "1")

    from php_t100_wf_engine import (
        BookResult,
        IS_CUT_DEFAULT,
        WINDOW_END,
        WINDOW_START_TARGET,
        build_results_payload,
        choose_window,
        is_htf_pass_from_dailies,
        run_variants,
        scan_equal_signals,
        write_results,
    )
    from track100_adapter import Track100Missing, adapter_selftest, track100_available
    from universe_filter import EXCLUDE_SYMBOLS

    t0 = time.time()
    ET = pytz.timezone("America/New_York")
    key = (os.environ.get("POLYGON_API_KEY") or "").strip()
    avail = track100_available()
    vend_py = sorted(p.name for p in VENDOR.glob("*.py"))
    is_cut = IS_CUT_DEFAULT

    print("EXP-0026 LOCAL — Peak Hour × Track100 $5k WF", flush=True)
    print(f"window {WINDOW_START_TARGET} → {WINDOW_END}; IS cut {is_cut}", flush=True)
    print(f"vendor_track100 py: {vend_py}", flush=True)
    print(f"track100 visible: ready={avail.get('ready')}", flush=True)

    window_meta = choose_window(None, None)
    extra: dict[str, Any] = {
        "track100": adapter_selftest(),
        "runtime_sec": 0.0,
        "runner": "local_polygon",
    }

    if "features.py" not in vend_py or not avail.get("ready"):
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason="track100_modules_missing_run_vendor_from_track100",
            extra={**extra, "vendor_py": vend_py},
        )
        write_results(payload, None)
        print("BLOCKED track100_modules_missing", flush=True)
        return 2

    selftest = adapter_selftest()
    if not selftest.get("imported"):
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason=f"track100_import_path_bug:{selftest.get('import_error')}",
            extra={**extra, "track100": selftest},
        )
        write_results(payload, None)
        print(f"BLOCKED import:{selftest.get('import_error')}", flush=True)
        return 2

    if not key:
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason="polygon_api_key_missing",
            extra=extra,
        )
        write_results(payload, None)
        print("BLOCKED polygon_api_key_missing", flush=True)
        return 2

    print("EXP-0026 grouped dailies (causal HTF)...", flush=True)
    grouped = fetch_grouped_dailies(DAILY_WARMUP_START, WINDOW_END.isoformat(), key)
    if not grouped.get("ok"):
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason=f"grouped_dailies_failed:{grouped.get('error')}",
            extra=extra,
        )
        write_results(payload, None)
        print("BLOCKED grouped_dailies_failed", flush=True)
        return 2

    by_sym: dict[str, list[dict[str, Any]]] = {}
    for ds, rows in (grouped.get("days") or {}).items():
        for r in rows:
            sym = r["T"]
            if sym in EXCLUDE_SYMBOLS:
                continue
            by_sym.setdefault(sym, []).append({
                "date": ds,
                "open": r["o"],
                "high": r["h"],
                "low": r["l"],
                "close": r["c"],
                "volume": r["v"],
            })

    print(f"  symbols in grouped={len(by_sym)} days={grouped.get('n_days')}", flush=True)

    daily_frames: dict[str, pd.DataFrame] = {}
    for sym, recs in by_sym.items():
        if len(recs) < 60:
            continue
        daily = pd.DataFrame(recs)
        daily.index = pd.to_datetime(daily["date"], utc=False).dt.tz_localize(ET)
        if float(daily["close"].max()) < 5:
            continue
        daily_frames[sym] = daily

    htf_dates: dict[str, set[str]] = {}
    union: set[str] = set()
    session_days: list[date] = []
    d = WINDOW_START_TARGET
    while d <= WINDOW_END:
        if d.weekday() < 5:
            session_days.append(d)
        d += timedelta(days=1)
    for i, session in enumerate(session_days, 1):
        if i % 25 == 0 or i == len(session_days):
            print(
                f"  HTF membership {session.isoformat()} "
                f"{i}/{len(session_days)} union={len(union)}",
                flush=True,
            )
        for sym, daily in daily_frames.items():
            ok, _meta = is_htf_pass_from_dailies(daily, asof=session)
            if ok:
                htf_dates.setdefault(sym, set()).add(session.isoformat())
                union.add(sym)

    symbols = sorted(union)
    print(f"HTF union symbols={len(symbols)}", flush=True)
    extra["n_htf_union"] = len(symbols)

    if not symbols:
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason="htf_union_empty",
            extra=extra,
        )
        write_results(payload, None)
        print("BLOCKED htf_union_empty", flush=True)
        return 2

    print(f"Fetching 1H bars ({len(symbols)}) with {H1_WORKERS} workers...", flush=True)
    h1_end = WINDOW_END.isoformat()
    fetched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=H1_WORKERS) as pool:
        futs = {
            pool.submit(fetch_1h_symbol, sym, H1_WARMUP_START, h1_end, key): sym
            for sym in symbols
        }
        for i, fut in enumerate(as_completed(futs), 1):
            fetched.append(fut.result())
            if i % 25 == 0 or i == len(futs):
                print(f"  1H packed {i}/{len(futs)}", flush=True)

    bars_by_sym: dict[str, pd.DataFrame] = {}
    min_d: date | None = None
    max_d: date | None = None
    for pack in fetched:
        if not pack.get("ok"):
            continue
        rows = []
        for b in pack.get("bars") or []:
            ts = pd.Timestamp(int(b["t"]), unit="ms", tz="UTC").tz_convert(ET)
            rows.append({
                "time": ts,
                "open": b["o"],
                "high": b["h"],
                "low": b["l"],
                "close": b["c"],
                "volume": b["v"],
            })
        if not rows:
            continue
        df = pd.DataFrame(rows).set_index("time").sort_index()
        bars_by_sym[str(pack["symbol"]).upper()] = df
        lo = df.index.min().date()
        hi = df.index.max().date()
        min_d = lo if min_d is None else min(min_d, lo)
        max_d = hi if max_d is None else max(max_d, hi)

    window_meta = choose_window(min_d, max_d)
    win_start = date.fromisoformat(window_meta["start"])
    win_end = date.fromisoformat(window_meta["end"])
    print(
        f"Scanning equal-signals {win_start} → {win_end} on {len(bars_by_sym)} names...",
        flush=True,
    )

    signals = []
    for i, (sym, df) in enumerate(sorted(bars_by_sym.items()), 1):
        sigs = scan_equal_signals(
            sym,
            df,
            window_start=win_start,
            window_end=win_end,
            htf_ok_dates=htf_dates.get(sym),
        )
        signals.extend(sigs)
        if i % 25 == 0 or i == len(bars_by_sym):
            print(f"  scanned {i}/{len(bars_by_sym)} signals={len(signals)}", flush=True)

    extra["n_symbols_1h"] = len(bars_by_sym)
    extra["n_signals"] = len(signals)

    try:
        variants = run_variants(
            signals,
            bars_by_sym,
            is_cut=is_cut,
            window_end=win_end,
            include_unfiltered=True,
            daily_by_sym=daily_frames,
        )
    except Track100Missing as exc:
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason=f"track100_import:{exc}",
            extra=extra,
        )
        write_results(payload, None)
        print(f"BLOCKED track100_import:{exc}", flush=True)
        return 2

    extra["runtime_sec"] = round(time.time() - t0, 2)
    payload = build_results_payload(
        variants=variants,
        window=window_meta,
        is_cut=is_cut,
        status="ok",
        extra=extra,
    )
    write_results(payload, variants)
    primary = variants.get("php_t100_filter_c")
    print(json.dumps({
        "status": payload.get("status"),
        "primary_final": None if primary is None else primary.final_equity,
        "primary_pnl": None if primary is None else primary.total_pnl,
        "runtime_sec": extra["runtime_sec"],
        "n_signals": len(signals),
        "window": window_meta,
        "is_cut": is_cut.isoformat(),
    }, indent=2, default=str), flush=True)
    print("Wrote experiments/EXP-0026/results.md + results.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
