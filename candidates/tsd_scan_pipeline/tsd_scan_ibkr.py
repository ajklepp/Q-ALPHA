"""
Q-ALPHA TSD pipeline — PASS 2 IBKR live scan.

Loads hunt list -> TWS 3H bars -> TSD BUY -> rank -> watch 10 / trade 3.
Default dry-run; use --live to admit profiler-pass picks to the watch queue (no direct entries).

Usage (TWS paper open, port 7497):
  py -3 candidates/tsd_scan_pipeline/tsd_scan_ibkr.py
  py -3 candidates/tsd_scan_pipeline/tsd_scan_ibkr.py --live
  py -3 candidates/tsd_scan_pipeline/tsd_scan_ibkr.py --symbols TSLA,NVDA,AMD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz
from ib_insync import IB, ScannerSubscription, Stock, util

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.build_3h_bars import bar_count_for_lookback, bars_from_ibkr
from tsd_scan_pipeline.tsd_capacity import (
    load_state,
    open_symbols,
    reset_scan_counter,
)
from tsd_scan_pipeline.tsd_entry_gates import occupied_symbols
from tsd_scan_pipeline.tsd_watch_queue import add_to_watch_queue
from tsd_scan_pipeline.tsd_profiler import profile_watchlist
from tsd_scan_pipeline.tsd_htf_gates import compute_combined_rank_score, evaluate_htf_daily_gates
from tsd_scan_pipeline.tsd_launch_score import (
    LAUNCH_SCAN_MAX,
    enrich_launch_fields,
    is_launch_candidate,
    signal_bar_red,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd, last_bar_summary
from tws_scan_pipeline.pipeline import fetch_polygon_mcap  # noqa: E402
from universe_filter import passes_instrument_safety  # noqa: E402

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 93

MCAP_MIN = 300_000_000
WATCH_TOP_N = 10
TRADE_TOP_N = 2
NEAR_CROSS_THRESHOLD = 15.0
PACING_SEC = 2.5
HIST_DURATION = bar_count_for_lookback(60)
BAR_SIZE = "3 hours"
SCAN_CODES = ("TOP_PERC_GAIN", "MOST_ACTIVE", "HOT_BY_VOLUME")
SCAN_ROWS = 50
ET = pytz.timezone("America/New_York")
HUNT_LIST_STALE_HOURS = 4

HUNT_LIST_PATH = PIPELINE_DIR / "polygon_hunt_list.json"
NEAR_CROSS_CACHE_PATH = PIPELINE_DIR / "results" / "near_cross_cache.json"
WATCHLIST_CACHE_PATH = PIPELINE_DIR / "results" / "last_watchlist.json"
RESULTS_DIR = PIPELINE_DIR / "results"

DEFAULT_SYMBOLS = (
    "TSLA", "NVDA", "AAPL", "AMD", "META", "MSFT", "GOOGL", "AMZN",
    "SMCI", "PLTR", "COIN", "ARM", "CRWD", "PANW", "SNOW", "UBER",
    "NFLX", "AVGO", "LLY", "JPM",
)

LIVE_BLOCK_MSG = (
    "Phase 4 trail monitor required. Run tsd_trail_monitor.py on schedule "
    "or set TSD_ALLOW_LIVE_WITHOUT_TRAIL=1 to override."
)
TRAIL_MONITOR_PATH = PIPELINE_DIR / "tsd_trail_monitor.py"


def _trail_monitor_ready() -> bool:
    return TRAIL_MONITOR_PATH.exists()


def _live_allowed() -> bool:
    if os.environ.get("TSD_ALLOW_LIVE_WITHOUT_TRAIL") == "1":
        return True
    return _trail_monitor_ready()


def _guard_live(live: bool) -> None:
    if live and not _live_allowed():
        print(f"ERROR: {LIVE_BLOCK_MSG}")
        sys.exit(1)


def _load_polygon_key() -> str | None:
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _read_json_list(path: Path, key: str = "tickers") -> list[str]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = doc.get(key) or doc.get("symbols") or []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item.upper())
        elif isinstance(item, dict) and item.get("symbol"):
            out.append(str(item["symbol"]).upper())
    return out


def _validate_hunt_list(path: Path) -> dict[str, Any]:
    """Warn if polygon hunt list is stale vs scheduled TWS scan slot."""
    meta: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        print("WARN: polygon_hunt_list.json missing — using defaults + always-include buckets")
        return meta

    doc = json.loads(path.read_text(encoding="utf-8"))
    generated_at = doc.get("generated_at")
    for_tws = doc.get("for_tws_scan_at")
    count = len(doc.get("tickers") or [])
    meta.update({"generated_at": generated_at, "for_tws_scan_at": for_tws, "count": count})
    print(
        f"Hunt list meta: generated_at={generated_at} "
        f"for_tws_scan_at={for_tws} count={count}"
    )

    now = datetime.now(ET)
    stale = False
    if for_tws:
        try:
            slot = datetime.fromisoformat(str(for_tws))
            if slot.tzinfo is None:
                slot = ET.localize(slot)
            else:
                slot = slot.astimezone(ET)
            age_h = abs((now - slot).total_seconds()) / 3600.0
            if age_h > HUNT_LIST_STALE_HOURS:
                stale = True
                print(
                    f"WARN: hunt list for_tws_scan_at is {age_h:.1f}h from now "
                    f"(>{HUNT_LIST_STALE_HOURS}h) — still using file + always-include buckets"
                )
        except Exception as exc:
            print(f"WARN: could not parse for_tws_scan_at={for_tws!r}: {exc}")
    meta["stale"] = stale
    return meta


def _union_symbols(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for sym in lst:
            s = sym.upper().strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def build_hunt_list(
    *,
    hunt_list_file: Path | None,
    extra_symbols: list[str],
    open_positions: list[str],
    use_scanners: bool,
    ib: IB | None,
) -> tuple[list[str], dict[str, Any]]:
    meta: dict[str, Any] = {"sources": {}}
    hunt_path = hunt_list_file or HUNT_LIST_PATH
    meta["hunt_list"] = _validate_hunt_list(hunt_path)

    poly_syms = _read_json_list(hunt_path)
    meta["sources"]["polygon_hunt_list"] = len(poly_syms)

    watch_syms = _read_json_list(WATCHLIST_CACHE_PATH, key="watch")
    if not watch_syms:
        watch_syms = _read_json_list(WATCHLIST_CACHE_PATH)
    meta["sources"]["last_watchlist"] = len(watch_syms)

    near_syms = _read_json_list(NEAR_CROSS_CACHE_PATH)
    meta["sources"]["near_cross_cache"] = len(near_syms)

    scanner_syms: list[str] = []
    if use_scanners and ib is not None and ib.isConnected():
        for code in SCAN_CODES:
            try:
                sub = ScannerSubscription(
                    instrument="STK",
                    locationCode="STK.US.MAJOR",
                    scanCode=code,
                    numberOfRows=SCAN_ROWS,
                )
                rows = ib.reqScannerData(sub)
                for sd in rows:
                    cd = getattr(sd, "contractDetails", None)
                    c = getattr(cd, "contract", None) if cd else None
                    sym = str(getattr(c, "symbol", "") or "").upper()
                    if sym:
                        scanner_syms.append(sym)
                ib.sleep(0.5)
            except Exception as exc:
                meta.setdefault("scanner_errors", []).append(f"{code}: {exc}")
        meta["sources"]["tws_scanners"] = len(set(scanner_syms))

    base = list(DEFAULT_SYMBOLS) if not any([poly_syms, extra_symbols, open_positions]) else []
    merged = _union_symbols(base, poly_syms, extra_symbols, open_positions, watch_syms, near_syms, scanner_syms)
    meta["total_unique"] = len(merged)
    return merged, meta


def fetch_3h_bars(ib: IB, symbol: str):
    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(f"no contract for {symbol}")
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=HIST_DURATION,
        barSizeSetting=BAR_SIZE,
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )
    ib.sleep(0.3)
    return bars


def evaluate_symbol(
    ib: IB,
    symbol: str,
    polygon_key: str | None,
) -> dict[str, Any]:
    """Signal detection only — profiler runs on watch-10 after rank."""
    row: dict[str, Any] = {"symbol": symbol, "pass": False, "reject_reason": None}

    if not passes_instrument_safety(symbol, require_cs_cache=False):
        row["reject_reason"] = "instrument_safety"
        return row

    try:
        bars = fetch_3h_bars(ib, symbol)
    except Exception as exc:
        row["reject_reason"] = f"ibkr_bars:{exc}"
        return row

    df = bars_from_ibkr(bars)
    if len(df) < 80:
        row["reject_reason"] = f"insufficient_bars:{len(df)}"
        return row

    enriched = enrich_tsd(df)
    summary = last_bar_summary(enriched)
    row.update(summary)
    row.update(enrich_launch_fields(row))

    wt1 = summary.get("wt1")
    wt2 = summary.get("wt2")
    if wt1 is not None and wt2 is not None:
        row["wt_gap"] = abs(wt1 - wt2)
        row["near_cross"] = abs(wt1 - wt2) < NEAR_CROSS_THRESHOLD

    if not summary.get("buy_signal"):
        row["reject_reason"] = "no_buy_signal"
        return row

    if not signal_bar_red(row):
        row["reject_reason"] = "signal_bar_not_red"
        return row

    if not is_launch_candidate(row):
        row["reject_reason"] = "not_launch_candidate"
        return row

    if row.get("phase") == "EXTENSION":
        row["reject_reason"] = "extension_phase"
        return row

    score = summary.get("scan_score") or 0
    trend = summary.get("trend_strength") or -999
    if score > LAUNCH_SCAN_MAX and not is_launch_candidate(row):
        row["reject_reason"] = f"scan_score>{LAUNCH_SCAN_MAX}"
        return row
    if trend <= 0 and not summary.get("early_bull"):
        row["reject_reason"] = "trend_strength<=0"
        return row

    if polygon_key:
        poly = fetch_polygon_mcap(symbol, polygon_key)
        mcap = poly.get("market_cap")
        row["market_cap"] = mcap
        row["name"] = poly.get("name") or ""
        time.sleep(0.12)
        if mcap is not None and mcap < MCAP_MIN:
            row["reject_reason"] = f"mcap<{MCAP_MIN}"
            return row
        if mcap is None:
            row["reject_reason"] = "mcap_unknown"
            return row

    row["pass"] = True
    row["signal"] = "TSD_BUY"
    return row


def rank_candidates(rows: list[dict[str, Any]], *, polygon_key: str | None = None) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("pass")]
    for row in passed:
        _, _, _, htf_score = evaluate_htf_daily_gates(row, polygon_key=polygon_key)
        row["htf_score"] = htf_score
        row["combined_rank_score"] = compute_combined_rank_score(
            enrich_launch_fields({**row, "htf_score": htf_score}),
        )
    passed.sort(
        key=lambda r: (-(r.get("combined_rank_score") or 0), r.get("scan_score") or 99),
    )
    return passed


def save_scan_snapshot(payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y%m%d_%H%M")
    path = RESULTS_DIR / f"scan_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def update_near_cross_cache(rows: list[dict[str, Any]]) -> None:
    near = [r["symbol"] for r in rows if r.get("near_cross")]
    NEAR_CROSS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEAR_CROSS_CACHE_PATH.write_text(
        json.dumps({"updated_at": datetime.now(ET).isoformat(), "tickers": near}, indent=2),
        encoding="utf-8",
    )


def update_watchlist_cache(watch: list[dict[str, Any]]) -> None:
    WATCHLIST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_CACHE_PATH.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(ET).isoformat(),
                "watch": [w["symbol"] for w in watch],
                "rows": watch,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def run_scan(
    *,
    symbols: list[str] | None,
    hunt_list_file: Path | None,
    use_scanners: bool,
    max_symbols: int | None,
    skip_profiler: bool,
    open_positions: list[str] | None = None,
    live: bool = False,
) -> int:
    _guard_live(live)
    util.startLoop()
    ib = IB()
    now_et = datetime.now(ET)
    mode = "LIVE" if live else "DRY_RUN"

    print("=" * 64)
    print(f"Q-ALPHA TSD SCAN - {mode}")
    print(f"ET={now_et.strftime('%Y-%m-%d %H:%M:%S')} clientId={TWS_CLIENT_ID}")
    print("=" * 64)

    book_state = load_state()
    reset_scan_counter(book_state)
    book_opens = open_symbols(book_state)
    cross_book = sorted(occupied_symbols())
    merged_opens = _union_symbols(open_positions or [], cross_book)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    extra = symbols or []
    hunt, hunt_meta = build_hunt_list(
        hunt_list_file=hunt_list_file,
        extra_symbols=extra,
        open_positions=merged_opens,
        use_scanners=use_scanners,
        ib=ib,
    )
    if max_symbols:
        hunt = hunt[:max_symbols]

    polygon_key = _load_polygon_key()
    if not polygon_key:
        print("WARN: POLYGON_API_KEY missing - mcap gate degraded")

    print(f"Hunt list: {len(hunt)} symbols  sources={hunt_meta.get('sources')}")
    print(f"Open book (TSD): {book_opens}")
    if cross_book != book_opens:
        print(f"Cross-book occupied: {cross_book}")
    print(f"Filters: buy_signal + red bar + LAUNCH | scan<={LAUNCH_SCAN_MAX} | HTF daily | mcap>=${MCAP_MIN/1e6:.0f}M")
    if not skip_profiler:
        print("Profiler: watch-10 gate, MIN 30 TSD analogs required")
    else:
        print("Profiler: SKIPPED (use --enforce-profiler or --live)")
    print("")

    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for i, sym in enumerate(hunt, 1):
        row = evaluate_symbol(ib, sym, polygon_key)
        rows.append(row)
        status = "SIGNAL" if row.get("pass") else row.get("reject_reason", "?")
        if len(hunt) <= 25 or row.get("buy_signal") or row.get("pass") or i % 25 == 0:
            print(
                f"  [{i:>3}/{len(hunt)}] {sym:<6} "
                f"score={row.get('scan_score', 'n/a')} "
                f"trend={row.get('trend_strength', 'n/a')} "
                f"buy={row.get('buy_signal', False)} -> {status}"
            )
        if i < len(hunt):
            time.sleep(PACING_SEC)

    elapsed = time.perf_counter() - t0
    signal_candidates = rank_candidates(rows, polygon_key=polygon_key)
    watch_candidates = signal_candidates[:WATCH_TOP_N]

    print(f"\n--- Profiler on watch-{len(watch_candidates)} ---")
    watch = profile_watchlist(watch_candidates, ib, polygon_key, skip=skip_profiler)
    trade = [w for w in watch if w.get("profiler_pass")][:TRADE_TOP_N]

    queue_results: list[dict[str, Any]] = []
    if live and trade:
        print("\n--- WATCH QUEUE (top 2 profiler-pass, gate-filtered — no direct entries) ---")
        queue_results = add_to_watch_queue(
            trade, scan_at=now_et.isoformat(), polygon_key=polygon_key,
        )

    update_near_cross_cache(rows)
    update_watchlist_cache(watch)

    try:
        from tsd_supabase_sync import sync_tsd_watchlist_to_supabase

        sync_tsd_watchlist_to_supabase(
            watch,
            scan_at=now_et.isoformat(),
            open_symbols_set=set(open_symbols(book_state)),
            trade_symbols={str(r.get("symbol") or "").upper() for r in trade},
        )
    except Exception as exc:
        print(f"  TSD watchlist Supabase warn: {exc}")

    payload = {
        "mode": mode,
        "scanned_at": now_et.isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "hunt_meta": hunt_meta,
        "hunt_count": len(hunt),
        "signal_candidates": signal_candidates,
        "watch_top_10": watch,
        "trade_top_3": trade,
        "queue_results": queue_results,
        "book_state": book_state,
        "all_rows": rows,
    }
    out_path = save_scan_snapshot(payload)

    print("")
    print("=" * 64)
    print(f"Done in {elapsed:.1f}s - {len(signal_candidates)} signal(s) from {len(hunt)} symbols")
    print(f"Snapshot: {out_path}")
    if watch:
        print("\nWATCH top 10 (profiler applied):")
        for r in watch:
            prof = r.get("profiler") or {}
            print(
                f"  {r['symbol']:<6} score={r.get('scan_score')} "
                f"analogs={prof.get('analog_count', 'skip')} "
                f"profiler={'PASS' if r.get('profiler_pass') else 'FAIL'}"
            )
    else:
        print("\nWATCH top 10: (none - no fresh BUY passed filters on last 3H bar)")
    if trade and not live:
        print("\nTRADE top 2 (dry-run - use --live to add to watch queue):")
        for r in trade:
            print(f"  {r['symbol']:<6} score={r.get('scan_score')} kill={r.get('kill_pct', 'n/a')}")
    print("=" * 64)

    try:
        ib.disconnect()
    except Exception:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD 3H IBKR live scan")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated extra symbols")
    parser.add_argument("--hunt-list", type=Path, default=None, help="polygon_hunt_list.json path")
    parser.add_argument("--use-scanners", action="store_true", help="Union TWS scanners (~150)")
    parser.add_argument("--max-symbols", type=int, default=None, help="Cap hunt list size")
    parser.add_argument("--open", type=str, default="", help="Comma-separated extra open positions")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Admit trade picks to tsd_watch_queue.json (TWS required; no direct entries)",
    )
    parser.add_argument(
        "--enforce-profiler",
        action="store_true",
        help="Require profiler v2 on watch-10 (MIN 30 analogs)",
    )
    args = parser.parse_args()

    _guard_live(args.live)

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    opens = [s.strip().upper() for s in args.open.split(",") if s.strip()]
    skip_profiler = not (args.live or args.enforce_profiler)

    book_opens = open_symbols(load_state())
    merged_opens = _union_symbols(opens, book_opens)

    return run_scan(
        symbols=syms if syms else None,
        hunt_list_file=args.hunt_list,
        use_scanners=args.use_scanners,
        max_symbols=args.max_symbols,
        skip_profiler=skip_profiler,
        open_positions=merged_opens,
        live=args.live,
    )


if __name__ == "__main__":
    sys.exit(main())
