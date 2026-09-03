"""
Q-ALPHA UTS v2.6 — hourly 1H LAUNCH scan.

HTF-pass names → last completed 1H launch eval → rank by HTF+launch →
queue / enter at most 2 NEW names if slots free.

Bar source: Polygon 1H aggs (see tsd_1h_signal.BAR_SOURCE).
Hours: 07 / 11 / 12 / 13 ET (scan at :15 after bar close for delayed Polygon).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_1h_signal import (
    ALLOWED_HOURS,
    BAR_SOURCE,
    evaluate_1h_buy_signal,
    is_launch_hour_window,
)
from tsd_scan_pipeline.tsd_capacity import (
    MAX_NEW_ENTRIES_PER_SCAN,
    load_state,
    reset_scan_counter,
    save_state,
)
from tsd_scan_pipeline.tsd_htf_gates import compute_combined_rank_score
from tsd_scan_pipeline.tsd_htf_universe import build_htf_universe, htf_pass_symbols
from tsd_scan_pipeline.tsd_launch_score import enrich_launch_fields
from tsd_scan_pipeline.tsd_watch_queue import add_to_watch_queue, execute_live_entries
from tsd_scan_pipeline.universe_tsd import load_polygon_key

ET = pytz.timezone("America/New_York")


def rank_1h_launches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("pass")]
    for row in passed:
        row["combined_rank_score"] = compute_combined_rank_score(
            enrich_launch_fields(row),
        )
    passed.sort(
        key=lambda r: (-(r.get("combined_rank_score") or 0), r.get("scan_score") or 99),
    )
    return passed


def evaluate_1h_symbol(
    symbol: str,
    *,
    htf_row: dict[str, Any] | None = None,
    polygon_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """1H LAUNCH trigger. 3H buy_signal is NOT required."""
    base = {"symbol": symbol.upper(), "pass": False, "reject_reason": None}
    if htf_row:
        base.update({k: v for k, v in htf_row.items() if k.startswith("htf_") or k == "close"})
    ok, launch_row = evaluate_1h_buy_signal(base, polygon_key=polygon_key, now=now)
    out = {**base, **launch_row, "symbol": symbol.upper()}
    phase_3h = out.get("phase_3h") or out.get("phase")
    if phase_3h == "EXTENSION":
        out["pass"] = False
        out["reject_reason"] = "extension_phase"
        return out
    if not ok:
        out["pass"] = False
        out["reject_reason"] = out.get("source") or "not_1h_launch"
        if out.get("hour_allowed") is False:
            out["reject_reason"] = f"hour_not_allowed:{out.get('htf_1h_bar_hour')}"
        return out
    out["pass"] = True
    out["signal"] = "1H_LAUNCH"
    out["structure_mode"] = "KILL ONLY until +1R"
    return out


def run_1h_launch_scan(
    *,
    live: bool = False,
    max_symbols: int | None = None,
    now: datetime | None = None,
) -> int:
    """Hourly 1H LAUNCH scan on today's HTF-pass universe."""
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = ET.localize(now_et)
    else:
        now_et = now_et.astimezone(ET)

    print("=" * 64)
    print("1H LAUNCH v2.6")
    print(f"ET={now_et.strftime('%Y-%m-%d %H:%M:%S')} hours={sorted(ALLOWED_HOURS)}")
    print(f"Bar source: {BAR_SOURCE}")
    print("Structure: KILL ONLY until +1R")
    print("=" * 64)

    if not is_launch_hour_window(now_et):
        print(f"  SKIP: hour {now_et.hour} not in {sorted(ALLOWED_HOURS)}")
        return 0

    key = load_polygon_key()
    htf_doc = build_htf_universe(refresh=False, polygon_key=key)
    htf_rows = {str(r["symbol"]).upper(): r for r in htf_doc.get("rows") or []}
    symbols = htf_pass_symbols()
    if max_symbols:
        symbols = symbols[:max_symbols]
    print(f"HTF-pass universe: {len(symbols)}")

    book = load_state()
    reset_scan_counter(book)
    save_state(book)

    rows: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols, 1):
        row = evaluate_1h_symbol(
            sym, htf_row=htf_rows.get(sym), polygon_key=key, now=now_et,
        )
        rows.append(row)
        if row.get("pass") or i % 25 == 0 or i <= 5:
            print(
                f"  [{i:>3}/{len(symbols)}] {sym:<6} "
                f"1H_buy={row.get('buy_signal')} hour={row.get('htf_1h_bar_hour')} "
                f"phase3h={row.get('phase_3h')} -> "
                f"{'LAUNCH' if row.get('pass') else row.get('reject_reason')}"
            )

    ranked = rank_1h_launches(rows)
    take = ranked[:MAX_NEW_ENTRIES_PER_SCAN]
    print(f"\n1H launches: {len(ranked)}  taking top {len(take)} (cap {MAX_NEW_ENTRIES_PER_SCAN}/hour)")
    for r in take:
        print(
            f"  {r['symbol']:<6} 1H_close={r.get('htf_1h_close')} "
            f"hour={r.get('htf_1h_bar_hour')} bar={r.get('htf_1h_bar_time')} "
            f"HTF={r.get('htf_score')} launch={r.get('launch_score')} "
            f"rank={r.get('combined_rank_score')} "
            f"mode={r.get('structure_mode')}"
        )

    if live and take:
        print("\n--- WATCH QUEUE / ENTER (1H close = entry ref) ---")
        add_to_watch_queue(take, scan_at=now_et.isoformat(), polygon_key=key)
        from ib_insync import IB, util

        util.startLoop()
        ib = IB()
        try:
            ib.connect("127.0.0.1", 7497, clientId=93, timeout=12)
        except Exception as exc:
            print(f"CONNECT FAILED: {exc}")
            return 1
        book = load_state()
        reset_scan_counter(book)
        results = execute_live_entries(ib, take, book)
        save_state(book)
        try:
            ib.disconnect()
        except Exception:
            pass
        for fill in results:
            print(f"  {fill.get('symbol')} {fill.get('status')} {fill.get('reason', '')}")

    print("=" * 64)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="UTS v2.6 1H LAUNCH hourly scan")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()
    return run_1h_launch_scan(live=args.live, max_symbols=args.max_symbols)


if __name__ == "__main__":
    sys.exit(main())
