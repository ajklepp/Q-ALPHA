#!/usr/bin/env python3
"""
EXP-0026 Modal runner — Peak Hour signals × Track 100 filter+C on $5k/10 seats.

Usage (laptop, after vendoring Track 100 frozen modules):

  py -3 experiments\\EXP-0026\\vendor_from_track100.py
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0026/study_php_t100_wf_5k_modal.py

Study only. No IBKR. No invented P&L if secrets or Track 100 files are missing.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import modal

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
CANDIDATES = REPO / "candidates"
PIPELINE = CANDIDATES / "tsd_scan_pipeline"
VENDOR = EXP_DIR / "vendor_track100"

APP_NAME = "q-alpha-exp026-php-t100-wf-5k"
POLYGON = "https://api.polygon.io"
SLEEP = 0.12
DAILY_WARMUP_START = "2025-10-01"
H1_WARMUP_START = "2025-10-01"

app = modal.App(APP_NAME)

_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(["numpy", "pandas", "requests", "pytz", "tzdata"])
)


def _add_if_exists(img: modal.Image, path: Path, remote: str) -> modal.Image:
    if path.is_file():
        return img.add_local_file(str(path), remote_path=remote)
    return img


for py in sorted(PIPELINE.glob("*.py")):
    _image = _add_if_exists(_image, py, f"/pkg/candidates/tsd_scan_pipeline/{py.name}")
_image = _add_if_exists(_image, CANDIDATES / "universe_filter.py", "/pkg/candidates/universe_filter.py")
_image = _add_if_exists(_image, CANDIDATES / "state_paths.py", "/pkg/candidates/state_paths.py")
_image = _add_if_exists(
    _image, EXP_DIR / "php_t100_wf_engine.py", "/pkg/exp0026/php_t100_wf_engine.py",
)
_image = _add_if_exists(
    _image, EXP_DIR / "track100_adapter.py", "/pkg/exp0026/track100_adapter.py",
)
for vend in sorted(VENDOR.glob("*.py")):
    _image = _add_if_exists(_image, vend, f"/pkg/exp0026/vendor_track100/{vend.name}")
_image = _add_if_exists(
    _image, VENDOR / "MANIFEST.json", "/pkg/exp0026/vendor_track100/MANIFEST.json",
)

image = _image
polygon_secret = modal.Secret.from_name("polygon-api-key")


def _polygon_get(url: str, params: dict[str, Any], retries: int = 4) -> dict[str, Any]:
    import requests

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


@app.function(image=image, secrets=[polygon_secret], timeout=180)
def fetch_1h_symbol(symbol: str, start: str, end: str) -> dict[str, Any]:
    """One ticker of Polygon 1H bars (warmup → window end)."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    if not key:
        return {"symbol": symbol, "ok": 0, "bars": [], "error": "no_key"}
    bars = _fetch_aggs(symbol, start=start, end=end, timespan="hour", key=key)
    return {"symbol": symbol.upper(), "ok": 1 if len(bars) >= 80 else 0, "bars": bars}


@app.function(image=image, secrets=[polygon_secret], timeout=900)
def fetch_grouped_dailies(start: str, end: str) -> dict[str, Any]:
    """
    Polygon grouped dailies for causal HTF / dollar-vol membership.

    Returns {date: [{T,o,h,l,c,v}, ...]} for trading days in [start, end].
    """
    from datetime import datetime as dt

    key = os.environ.get("POLYGON_API_KEY") or ""
    if not key:
        return {"ok": 0, "error": "no_key", "days": {}}
    start_d = dt.strptime(start, "%Y-%m-%d").date()
    end_d = dt.strptime(end, "%Y-%m-%d").date()
    days: dict[str, list[dict[str, Any]]] = {}
    d = start_d
    n_ok = 0
    while d <= end_d:
        if d.weekday() < 5:
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
        d += timedelta(days=1)
    return {"ok": 1 if n_ok else 0, "n_days": n_ok, "days": days}


@app.function(image=image, secrets=[polygon_secret], timeout=21600)
def run_walkforward() -> dict[str, Any]:
    """Full causal study on Modal. Returns results payload (no invented P&L)."""
    t0 = time.time()
    sys.path.insert(0, "/pkg/candidates")
    sys.path.insert(0, "/pkg/exp0026")
    os.environ.setdefault("PHP_EQUAL_SIGNAL", "1")

    import pandas as pd
    import pytz

    from php_t100_wf_engine import (
        IS_CUT_DEFAULT,
        WINDOW_END,
        WINDOW_START_TARGET,
        build_results_payload,
        choose_window,
        is_htf_pass_from_dailies,
        run_variants,
        scan_equal_signals,
    )
    from track100_adapter import (
        Track100Missing,
        adapter_selftest,
        frozen_is_cut,
        track100_available,
    )
    from universe_filter import EXCLUDE_SYMBOLS

    ET = pytz.timezone("America/New_York")
    key = os.environ.get("POLYGON_API_KEY") or ""
    avail = track100_available()
    try:
        is_cut_s = frozen_is_cut()
        is_cut = date.fromisoformat(is_cut_s[:10])
    except Exception:
        is_cut = IS_CUT_DEFAULT

    window_meta = choose_window(None, None)
    extra: dict[str, Any] = {
        "track100": adapter_selftest(),
        "runtime_sec": 0.0,
    }

    if not key:
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason="polygon_api_key_missing",
            extra=extra,
        )
        payload["extra"]["runtime_sec"] = round(time.time() - t0, 2)
        return payload
    if not avail.get("ready"):
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason="track100_modules_missing",
            extra=extra,
        )
        payload["extra"]["runtime_sec"] = round(time.time() - t0, 2)
        return payload

    print("EXP-0026 grouped dailies (causal HTF)...", flush=True)
    grouped = fetch_grouped_dailies.local(DAILY_WARMUP_START, WINDOW_END.isoformat())
    if not grouped.get("ok"):
        payload = build_results_payload(
            variants=None,
            window=window_meta,
            is_cut=is_cut,
            status="blocked",
            blocked_reason=f"grouped_dailies_failed:{grouped.get('error')}",
            extra=extra,
        )
        payload["extra"]["runtime_sec"] = round(time.time() - t0, 2)
        return payload

    # Pivot grouped → per-symbol daily frames (oldest→newest).
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for ds, rows in grouped.get("days") or {}.items():
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
        daily.index = pd.to_datetime(daily["date"]).tz_localize(ET)
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
        payload["extra"]["runtime_sec"] = round(time.time() - t0, 2)
        return payload

    print(f"Fetching 1H bars ({len(symbols)})...", flush=True)
    h1_end = WINDOW_END.isoformat()
    fetched = list(
        fetch_1h_symbol.map(
            symbols,
            kwargs={"start": H1_WARMUP_START, "end": h1_end},
        )
    )
    bars_by_sym: dict[str, pd.DataFrame] = {}
    min_d: date | None = None
    max_d: date | None = None
    for i, pack in enumerate(fetched, 1):
        if i % 25 == 0 or i == len(fetched):
            print(f"  1H packed {i}/{len(fetched)}", flush=True)
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
        payload["extra"]["runtime_sec"] = round(time.time() - t0, 2)
        return payload

    extra["runtime_sec"] = round(time.time() - t0, 2)
    payload = build_results_payload(
        variants=variants,
        window=window_meta,
        is_cut=is_cut,
        status="ok",
        extra=extra,
    )
    # Modal worker cannot write the laptop repo; local_entrypoint persists files.
    payload["_variants_primary"] = {
        k: {
            "final_equity": v.final_equity,
            "total_pnl": v.total_pnl,
            "is_end_equity": v.is_end_equity,
            "oos_equity": v.oos_equity,
            "oos_pnl": v.oos_pnl,
            "win_pct": v.win_pct,
            "max_dd": v.max_dd,
            "n_signals": v.n_signals,
            "n_fills": v.n_fills,
            "n_filter_skip": v.n_filter_skip,
            "n_filter_pass": v.n_filter_pass,
            "filter_skip_reasons": v.filter_skip_reasons,
            "occupancy_avg": v.occupancy_avg,
            "extra": v.extra,
        }
        for k, v in variants.items()
    }
    print(f"DONE runtime={extra['runtime_sec']}s final=${variants['php_t100_filter_c'].final_equity}", flush=True)
    return payload


@app.local_entrypoint()
def main() -> None:
    """Run the study on Modal and write experiments/EXP-0026/results.* locally."""
    sys.path.insert(0, str(EXP_DIR))
    from php_t100_wf_engine import (
        BookResult,
        IS_CUT_DEFAULT,
        build_results_payload,
        choose_window,
        write_results,
    )
    from track100_adapter import adapter_selftest, track100_available

    # Fail closed before paying for a Modal container if vendor snapshot is incomplete.
    avail = track100_available()
    print("EXP-0026 — Peak Hour × Track100 $5k WF", flush=True)
    print(f"track100 visible: {avail}", flush=True)
    if not avail.get("ready"):
        window = choose_window(None, None)
        payload = build_results_payload(
            variants=None,
            window=window,
            is_cut=IS_CUT_DEFAULT,
            status="blocked",
            blocked_reason="track100_modules_missing_run_vendor_from_track100",
            extra={"track100": avail},
        )
        write_results(payload, None)
        print("BLOCKED track100_modules_missing_run_vendor_from_track100", flush=True)
        print("Wrote experiments/EXP-0026/results.md (no invented P&L)", flush=True)
        return

    # Import self-test: catches the Modal path bug (missing features/playbook/...)
    # on the laptop before .remote().
    selftest = adapter_selftest()
    if not selftest.get("imported"):
        window = choose_window(None, None)
        payload = build_results_payload(
            variants=None,
            window=window,
            is_cut=IS_CUT_DEFAULT,
            status="blocked",
            blocked_reason=f"track100_import_path_bug:{selftest.get('import_error')}",
            extra={"track100": selftest},
        )
        write_results(payload, None)
        print(f"BLOCKED track100_import_path_bug:{selftest.get('import_error')}", flush=True)
        print("Wrote experiments/EXP-0026/results.md (no invented P&L)", flush=True)
        return

    try:
        payload = run_walkforward.remote()
    except Exception as exc:
        window = choose_window(None, None)
        reason = str(exc)
        if "polygon-api-key" in reason.lower() or "secret" in reason.lower():
            blocked = "modal_secret_polygon-api-key_missing"
        else:
            blocked = f"modal_run_failed:{reason[:240]}"
        payload = build_results_payload(
            variants=None,
            window=window,
            is_cut=IS_CUT_DEFAULT,
            status="blocked",
            blocked_reason=blocked,
            extra={"track100": track100_available()},
        )
        write_results(payload, None)
        print(f"BLOCKED {blocked}", flush=True)
        print("Wrote experiments/EXP-0026/results.md (no invented P&L)", flush=True)
        return

    variants = None
    raw_v = payload.pop("_variants_primary", None)
    if payload.get("status") == "ok" and isinstance(raw_v, dict):
        variants = {}
        for name, d in raw_v.items():
            variants[name] = BookResult(
                starting_cash=5000.0,
                final_equity=d["final_equity"],
                total_pnl=d["total_pnl"],
                is_end_equity=d["is_end_equity"],
                oos_equity=d["oos_equity"],
                oos_pnl=d["oos_pnl"],
                win_pct=d["win_pct"],
                max_dd=d["max_dd"],
                n_signals=d["n_signals"],
                n_filter_pass=d["n_filter_pass"],
                n_filter_skip=d["n_filter_skip"],
                n_fills=d["n_fills"],
                n_closed=d.get("n_closed") or 0,
                n_open_end=0,
                filter_skip_reasons=d.get("filter_skip_reasons") or {},
                occupancy_avg=d.get("occupancy_avg") or 0.0,
                trades=[],
                extra=d.get("extra") or {},
            )

    write_results(payload, variants)
    print(json.dumps({
        "status": payload.get("status"),
        "blocked_reason": payload.get("blocked_reason"),
        "primary": payload.get("primary"),
    }, indent=2, default=str))
    print("Wrote experiments/EXP-0026/results.md + results.json", flush=True)
