#!/usr/bin/env python3
"""
Blind Peak Hour week sim with LIVE 4-tranche keep-profit / ratchet trail.

Same no-peek protocol as php_wf_week_cash_test.py:
  - Blind week draw (seed 20260911 → 2026-08-17..21 unless overridden)
  - Rank hours with live continuation_score v1.6 on corpus features only
  - Take top `--slots` (default 3) admitted names / hour
  - Size from $3k slot_ladder + SHARE_LOT=4
  - Advance positions with init_trail_state + evaluate_trail_tick (php_keep_profit_v1)
  - Overnight carry; T4-only frees a slot; COST_PER_TRADE=0.0015 RT on entry notional
  - Mark leftovers at Friday EOD

Usage:
  py -3 candidates/tsd_scan_pipeline/php_wf_week_trail_sim.py
  py -3 candidates/tsd_scan_pipeline/php_wf_week_trail_sim.py --slots 3 --cash 3000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from tsd_scan_pipeline.php_range_replay import (  # noqa: E402
    COST_PER_TRADE,
    ET,
    _bar_ohlc_as_of,
    _book_partial_exits,
    _close_position,
    _full_slot_count,
    _last_px,
    _mark_equity,
    trading_days,
)
from tsd_scan_pipeline.php_wf_week_cash_test import (  # noqa: E402
    CORPUS,
    pick_blind_week,
    score_week,
)
from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon  # noqa: E402
from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    SHARE_LOT,
    shares_for_budget,
    slot_ladder,
)
from tsd_scan_pipeline.tsd_kill import resolve_kill_pct  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import CONTINUATION_SCORE_VERSION  # noqa: E402
from tsd_scan_pipeline.tsd_trail import (  # noqa: E402
    evaluate_trail_tick,
    init_trail_state,
    is_t4_only,
    load_tsd_profile,
    maybe_roll_trading_day,
    remaining_shares,
)
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


def _hour_candidates(week: pd.DataFrame, *, slots: int) -> list[dict[str, Any]]:
    """Ordered list of intended takes: date, hour, symbol, entry, score (top-N/hour)."""
    takes: list[dict[str, Any]] = []
    for day in sorted(week["signal_date"].astype(str).unique()):
        day_df = week[week["signal_date"].astype(str) == day]
        for hour in sorted(int(h) for h in day_df["hour"].unique()):
            pool = (
                day_df[day_df["hour"].astype(int) == hour]
                .query("admit_v16 == 1")
                .sort_values("score_v16", ascending=False)
                .head(slots)
            )
            for rank, (_, r) in enumerate(pool.iterrows(), start=1):
                takes.append({
                    "date": day,
                    "hour": int(hour),
                    "symbol": str(r["symbol"]).upper(),
                    "entry": float(r["close"]),
                    "score_v16": float(r["score_v16"]),
                    "rank_in_hour": rank,
                })
    return takes


def run_trail_week(
    *,
    start: str,
    end: str,
    starting_cash: float = 3000.0,
    slots: int = 3,
) -> dict[str, Any]:
    t0 = time.time()
    key = load_polygon_key()
    if not CORPUS.is_file():
        raise SystemExit(f"Missing corpus {CORPUS}")

    df = pd.read_csv(CORPUS)
    week = df[
        (df["signal_date"].astype(str) >= start)
        & (df["signal_date"].astype(str) <= end)
    ].copy()
    if week.empty:
        raise SystemExit(f"No corpus rows for {start}..{end}")

    print("=" * 72)
    print(f"PHP WF TRAIL SIM  {start} -> {end}")
    print(
        f"score={CONTINUATION_SCORE_VERSION}  cash=${starting_cash:.0f}  "
        f"slots/hour={slots}  4-tranche keep-profit ratchet  NO TWS"
    )
    print("=" * 72)

    print(f"Corpus week rows: {len(week)} — scoring v1.6...")
    week = score_week(week)
    planned = _hour_candidates(week, slots=slots)
    symbols = sorted({t["symbol"] for t in planned})
    print(f"Planned takes: {len(planned)} across {len(symbols)} symbols")

    print(f"Prefetching 1H bars ({len(symbols)})...")
    bar_cache: dict[str, Any] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            bar_cache[sym] = _bars_1h_polygon(sym, api_key=key)
            time.sleep(0.12)
        except Exception as exc:
            print(f"  bar miss {sym}: {exc}")
            bar_cache[sym] = pd.DataFrame()
        if i % 25 == 0 or i == len(symbols):
            print(f"  bars {i}/{len(symbols)}")

    days = trading_days(
        datetime.strptime(start, "%Y-%m-%d").date(),
        datetime.strptime(end, "%Y-%m-%d").date(),
    )
    # Index planned takes by (date, hour)
    by_hour: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for t in planned:
        by_hour.setdefault((t["date"], t["hour"]), []).append(t)

    cash = float(starting_cash)
    open_pos: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    hour_logs: list[dict[str, Any]] = []
    skipped: dict[str, int] = {
        "slots_full": 0,
        "already_open": 0,
        "expensive": 0,
        "no_cash": 0,
    }

    for date_str in days:
        for pos in open_pos.values():
            pos["trail"] = maybe_roll_trading_day(
                pos["trail"],
                today=datetime.strptime(date_str, "%Y-%m-%d").date(),
            )

        for hour in range(5, 16):
            # --- Advance open trails on this completed hour ---
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                ohlc = _bar_ohlc_as_of(bar_cache.get(sym), date_str, hour)
                if not ohlc:
                    continue
                if date_str == pos.get("entry_date") and hour <= int(pos["entry_hour"]):
                    continue
                high, low, close = ohlc
                when = f"{date_str}T{hour:02d}:00:00"
                trail, exits = evaluate_trail_tick(
                    pos["trail"], high=high, low=low, close=close, when=when,
                )
                pos["trail"] = trail
                if exits:
                    cash += _book_partial_exits(pos, [
                        {
                            "tranche_id": e["tranche_id"],
                            "shares": e["shares"],
                            "exit_price": e["exit_price"],
                            "reason": e["reason"],
                            "hour": hour,
                            "date": date_str,
                        }
                        for e in exits
                    ])
                if remaining_shares(pos["trail"]) <= 0:
                    closed.append(_close_position(
                        pos, status="CLOSED", exit_reason="trail_flat",
                    ))
                    del open_pos[sym]

            # --- New entries (v1.6 top-N for this hour) ---
            equity = _mark_equity(cash, open_pos, bar_cache, date_str, hour)
            n_cap, unit_s = slot_ladder(equity)
            # Book can hold up to ladder N; hourly NEW cap = slots
            slots_used = _full_slot_count(open_pos)
            slots_left_book = max(0, n_cap - slots_used)
            take_n = min(slots, slots_left_book)
            cands = by_hour.get((date_str, hour), [])
            taken_syms: list[str] = []

            if take_n <= 0 and cands:
                skipped["slots_full"] += len(cands)
                hour_logs.append({
                    "date": date_str, "hour": hour, "taken": [],
                    "skipped": "slots_full", "equity": round(equity, 2),
                })
                continue

            for cand in cands:
                if len(taken_syms) >= take_n:
                    break
                sym = cand["symbol"]
                if sym in open_pos:
                    skipped["already_open"] += 1
                    continue
                px = float(cand["entry"] or 0)
                if px <= 0:
                    continue
                equity = _mark_equity(cash, open_pos, bar_cache, date_str, hour)
                _, unit_s = slot_ladder(equity)
                budget = min(unit_s, cash)
                shares = shares_for_budget(budget, px)
                if shares <= 0:
                    skipped["expensive"] += 1
                    continue
                if cash < px * shares:
                    skipped["no_cash"] += 1
                    continue

                profile = load_tsd_profile(sym) or {}
                kill_pct, kill_src = resolve_kill_pct(
                    profile.get("kill_pct"), profile=profile,
                )
                trail = init_trail_state(
                    entry_price=px, n_shares=shares, profile=profile,
                )
                cash -= px * shares
                open_pos[sym] = {
                    "symbol": sym,
                    "entry_date": date_str,
                    "entry_hour": hour,
                    "entry_price": round(px, 4),
                    "shares": shares,
                    "kill_pct": kill_pct,
                    "kill_source": kill_src,
                    "continuation_score": cand["score_v16"],
                    "rank_in_hour": cand["rank_in_hour"],
                    "notional": round(px * shares, 2),
                    "trail": trail,
                    "realized_gross": 0.0,
                    "tranche_exits": [],
                    "tranche_init": [
                        {
                            "id": t.get("id"),
                            "shares": t.get("shares"),
                            "trigger_pct": t.get("trigger_pct"),
                            "trigger_price": round(float(t.get("trigger_price") or 0), 4),
                        }
                        for t in (trail.get("tranches") or [])
                    ],
                }
                taken_syms.append(sym)
                print(
                    f"  ENTER {date_str} h{hour:02d} {sym} "
                    f"px={px:.2f} sh={shares} score={cand['score_v16']:.1f} "
                    f"kill={kill_pct:.1%}({kill_src})"
                )

            hour_logs.append({
                "date": date_str,
                "hour": hour,
                "taken": taken_syms,
                "slots_used": _full_slot_count(open_pos),
                "equity": round(
                    _mark_equity(cash, open_pos, bar_cache, date_str, hour), 2
                ),
            })

        # EOD mark log
        eod = _mark_equity(cash, open_pos, bar_cache, date_str, 16)
        print(
            f"  EOD {date_str}: equity=${eod:,.2f}  "
            f"open={len(open_pos)}  cash=${cash:,.2f}"
        )

    # Flatten leftovers at last day 16:00 mark
    last = days[-1]
    for sym in list(open_pos.keys()):
        pos = open_pos[sym]
        rem = remaining_shares(pos["trail"])
        if rem <= 0:
            closed.append(_close_position(pos, status="CLOSED", exit_reason="trail_flat"))
            del open_pos[sym]
            continue
        px = _last_px(bar_cache.get(sym), last, 16, float(pos["entry_price"]))
        pos["realized_gross"] = float(pos.get("realized_gross") or 0.0) + (
            px - float(pos["entry_price"])
        ) * rem
        cash += px * rem
        closed.append(_close_position(pos, status="MARKED", exit_reason="range_eod_mark"))
        del open_pos[sym]

    realized = sum(float(c.get("pnl") or 0) for c in closed)
    end_equity = starting_cash + realized
    # Sanity: cash should ≈ end_equity when flat
    wins = [c for c in closed if float(c.get("pnl") or 0) > 0]
    report = {
        "protocol": {
            "week": {"start": start, "end": end},
            "score_version": CONTINUATION_SCORE_VERSION,
            "slots_per_hour": slots,
            "starting_cash": starting_cash,
            "trail": "php_keep_profit_v1 (4-tranche + ratchet / T1 bank + kill tighten)",
            "cost_per_trade": COST_PER_TRADE,
            "share_lot": SHARE_LOT,
            "ranking": "v1.6 on corpus signal features; trail on Polygon 1H bars",
        },
        "summary": {
            "n_entries": len(closed),
            "n_wins": len(wins),
            "win_rate": round(len(wins) / len(closed), 4) if closed else None,
            "realized_pnl": round(realized, 2),
            "ending_equity": round(end_equity, 2),
            "return_pct": round(100.0 * realized / starting_cash, 2),
            "ending_cash": round(cash, 2),
            "skipped": skipped,
            "runtime_sec": round(time.time() - t0, 1),
        },
        "closed": closed,
        "hour_logs": hour_logs,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"php_wf_trail_{start.replace('-', '')}_{end.replace('-', '')}_s{slots}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"ENTRIES: {len(closed)}  wins={len(wins)}  "
          f"win%={report['summary']['win_rate']}")
    print(f"P&L:     ${realized:+,.2f}")
    print(f"Equity:  ${starting_cash:,.2f} -> ${end_equity:,.2f} "
          f"({report['summary']['return_pct']:+.2f}%)")
    print(f"Skipped: {skipped}")
    print(f"Wrote {out}")
    print(f"Runtime {report['summary']['runtime_sec']}s")
    print("=" * 72)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--cash", type=float, default=3000.0)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    pick = pick_blind_week(seed=args.seed)
    start = args.start or pick["start"]
    end = args.end or pick["end"]
    print(
        f"Blind week seed={pick['seed']}: {start}..{end} "
        f"(from {pick['n_candidates']} candidates)"
    )
    run_trail_week(
        start=start,
        end=end,
        starting_cash=args.cash,
        slots=args.slots,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
