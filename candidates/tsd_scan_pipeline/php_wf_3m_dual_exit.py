#!/usr/bin/env python3
"""
3-month Peak Hour walk-forward — dual exit bakeoff (same entries).

Protocol
--------
1. Blind-pick a start Monday in the last year such that a ~3 calendar-month
   window ends before as-of (seed documented). Outcomes not used in the draw.
2. Rank with live continuation_score v1.6 on EXP-0021 corpus features only
   (signal-time). Take top `--slots` (default 3) admits / hour.
3. Run TWO independent $3k books on the *same* entry sequence:
     A) HARD 1R — full exit at −5% kill or +5% target (path-first on 1H bars)
     B) 4T RATCHET — php_keep_profit_v1 (T1 bank + kill tighten + T2–T4 trail)
4. COST_PER_TRADE=0.0015 RT on entry notional; SHARE_LOT=4; slot_ladder sizing.
5. Leftovers marked at window EOD.

Note: ranked signal corpus currently spans ~2026-06-08..2026-09-04 (63 sessions).
If the blind last-year draw falls outside that span, we snap to the sole
corpus-covered 3-month window and record the snap (no outcome peeking).

Usage:
  py -3 candidates/tsd_scan_pipeline/php_wf_3m_dual_exit.py
  py -3 candidates/tsd_scan_pipeline/php_wf_3m_dual_exit.py --slots 3 --cash 3000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
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
    score_week,
)
from tsd_scan_pipeline.php_wf_week_trail_sim import _hour_candidates  # noqa: E402
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
    load_tsd_profile,
    maybe_roll_trading_day,
    remaining_shares,
)
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
BLIND_SEED = 20260911
ASOF = date(2026, 9, 11)
KILL_PCT = 0.05
TARGET_PCT = 0.05
WINDOW_CALENDAR_DAYS = 90  # ~3 months


def _corpus_date_bounds() -> tuple[date, date]:
    df = pd.read_csv(CORPUS, usecols=["signal_date"])
    lo = datetime.strptime(str(df["signal_date"].min())[:10], "%Y-%m-%d").date()
    hi = datetime.strptime(str(df["signal_date"].max())[:10], "%Y-%m-%d").date()
    return lo, hi


def pick_blind_3m_window(
    *,
    seed: int = BLIND_SEED,
    asof: date = ASOF,
) -> dict[str, Any]:
    """
    Draw a random Monday start in the last year with end = start+90d < asof-7d.
    If that window is not covered by the signal corpus, snap to the corpus
    3-month span (sole available ranked window) without reading outcomes.
    """
    corpus_lo, corpus_hi = _corpus_date_bounds()
    end_lim = asof - timedelta(days=7)
    starts: list[date] = []
    d = asof - timedelta(days=365)
    while d <= end_lim - timedelta(days=WINDOW_CALENDAR_DAYS):
        if d.weekday() == 0:
            starts.append(d)
        d += timedelta(days=1)
    rng = random.Random(seed)
    raw_start = rng.choice(starts)
    raw_end = raw_start + timedelta(days=WINDOW_CALENDAR_DAYS)

    snapped = False
    start, end = raw_start, raw_end
    # Need corpus coverage for ranking features
    if start < corpus_lo or end > corpus_hi:
        start, end = corpus_lo, corpus_hi
        snapped = True

    return {
        "seed": seed,
        "asof": asof.isoformat(),
        "n_start_candidates": len(starts),
        "raw_draw_start": raw_start.isoformat(),
        "raw_draw_end": raw_end.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "snapped_to_corpus": snapped,
        "corpus_span": [corpus_lo.isoformat(), corpus_hi.isoformat()],
        "reason": (
            "Blind last-year draw fell outside ranked signal corpus; "
            "snapped to corpus Jun–Sep window for feature-complete ranking."
            if snapped
            else "Blind draw fully inside ranked signal corpus."
        ),
    }


def _open_1r(
    *,
    sym: str,
    date_str: str,
    hour: int,
    px: float,
    shares: int,
    score: float,
    rank: int,
) -> dict[str, Any]:
    kill_px = px * (1.0 - KILL_PCT)
    target_px = px * (1.0 + TARGET_PCT)
    return {
        "symbol": sym,
        "entry_date": date_str,
        "entry_hour": hour,
        "entry_price": round(px, 4),
        "shares": shares,
        "kill_pct": KILL_PCT,
        "target_pct": TARGET_PCT,
        "kill_price": round(kill_px, 4),
        "target_price": round(target_px, 4),
        "continuation_score": score,
        "rank_in_hour": rank,
        "notional": round(px * shares, 2),
        "remaining": shares,
        "realized_gross": 0.0,
        "tranche_exits": [],
        "mode": "hard_1r",
    }


def _full_slot_count_1r(open_pos: dict[str, dict[str, Any]]) -> int:
    return sum(1 for p in open_pos.values() if int(p.get("remaining") or 0) > 0)


def _mark_equity_1r(
    cash: float,
    open_pos: dict[str, dict[str, Any]],
    bar_cache: dict[str, Any],
    date_str: str,
    hour: int,
) -> float:
    mtm = 0.0
    for sym, pos in open_pos.items():
        rem = int(pos.get("remaining") or 0)
        if rem <= 0:
            continue
        px = _last_px(bar_cache.get(sym), date_str, hour, float(pos["entry_price"]))
        mtm += rem * px
    return cash + mtm


def _close_1r(pos: dict[str, Any], *, status: str, exit_reason: str) -> dict[str, Any]:
    entry = float(pos["entry_price"])
    shares = int(pos["shares"])
    cost = entry * shares * COST_PER_TRADE
    pnl = float(pos.get("realized_gross") or 0.0) - cost
    return {
        **{k: v for k, v in pos.items() if k not in ("remaining",)},
        "status": status,
        "exit_reason": exit_reason,
        "pnl": round(pnl, 2),
    }


def simulate_dual(
    *,
    start: str,
    end: str,
    starting_cash: float,
    slots: int,
    planned: list[dict[str, Any]],
    bar_cache: dict[str, Any],
) -> dict[str, Any]:
    """Advance both books hour-by-hour on the same planned take list."""
    days = trading_days(
        datetime.strptime(start, "%Y-%m-%d").date(),
        datetime.strptime(end, "%Y-%m-%d").date(),
    )
    by_hour: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for t in planned:
        by_hour.setdefault((t["date"], t["hour"]), []).append(t)

    books: dict[str, dict[str, Any]] = {
        "hard_1r": {
            "cash": float(starting_cash),
            "open": {},
            "closed": [],
            "skipped": {"slots_full": 0, "already_open": 0, "expensive": 0},
            "peak": float(starting_cash),
            "max_dd": 0.0,
        },
        "ratchet_4t": {
            "cash": float(starting_cash),
            "open": {},
            "closed": [],
            "skipped": {"slots_full": 0, "already_open": 0, "expensive": 0},
            "peak": float(starting_cash),
            "max_dd": 0.0,
        },
    }

    def _update_dd(book: dict[str, Any], equity: float) -> None:
        book["peak"] = max(float(book["peak"]), equity)
        peak = float(book["peak"])
        if peak > 0:
            book["max_dd"] = max(float(book["max_dd"]), (peak - equity) / peak)

    for date_str in days:
        # Roll 4T trading-day counters
        for pos in books["ratchet_4t"]["open"].values():
            pos["trail"] = maybe_roll_trading_day(
                pos["trail"],
                today=datetime.strptime(date_str, "%Y-%m-%d").date(),
            )

        for hour in range(5, 16):
            # ---- exits / trail advance ----
            # hard 1R
            b1 = books["hard_1r"]
            for sym in list(b1["open"].keys()):
                pos = b1["open"][sym]
                if date_str == pos["entry_date"] and hour <= int(pos["entry_hour"]):
                    continue
                ohlc = _bar_ohlc_as_of(bar_cache.get(sym), date_str, hour)
                if not ohlc:
                    continue
                high, low, _close = ohlc
                entry = float(pos["entry_price"])
                rem = int(pos["remaining"])
                kill_px = float(pos["kill_price"])
                target_px = float(pos["target_price"])
                if low <= kill_px:
                    px = kill_px
                    pos["realized_gross"] += (px - entry) * rem
                    pos["remaining"] = 0
                    pos["tranche_exits"].append({
                        "reason": "kill", "shares": rem, "exit_price": px,
                        "date": date_str, "hour": hour,
                    })
                    b1["cash"] += px * rem
                    b1["closed"].append(_close_1r(pos, status="CLOSED", exit_reason="kill"))
                    del b1["open"][sym]
                elif high >= target_px:
                    px = target_px
                    pos["realized_gross"] += (px - entry) * rem
                    pos["remaining"] = 0
                    pos["tranche_exits"].append({
                        "reason": "target", "shares": rem, "exit_price": px,
                        "date": date_str, "hour": hour,
                    })
                    b1["cash"] += px * rem
                    b1["closed"].append(_close_1r(pos, status="CLOSED", exit_reason="target"))
                    del b1["open"][sym]

            # 4T ratchet
            b4 = books["ratchet_4t"]
            for sym in list(b4["open"].keys()):
                pos = b4["open"][sym]
                if date_str == pos["entry_date"] and hour <= int(pos["entry_hour"]):
                    continue
                ohlc = _bar_ohlc_as_of(bar_cache.get(sym), date_str, hour)
                if not ohlc:
                    continue
                high, low, close = ohlc
                when = f"{date_str}T{hour:02d}:00:00"
                trail, exits = evaluate_trail_tick(
                    pos["trail"], high=high, low=low, close=close, when=when,
                )
                pos["trail"] = trail
                if exits:
                    b4["cash"] += _book_partial_exits(pos, [
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
                    b4["closed"].append(
                        _close_position(pos, status="CLOSED", exit_reason="trail_flat")
                    )
                    del b4["open"][sym]

            # ---- shared candidate list; enter into each book independently ----
            cands = by_hour.get((date_str, hour), [])

            for book_name, slot_fn, mark_fn in (
                ("hard_1r", _full_slot_count_1r, _mark_equity_1r),
                ("ratchet_4t", _full_slot_count, _mark_equity),
            ):
                book = books[book_name]
                equity = mark_fn(book["cash"], book["open"], bar_cache, date_str, hour)
                _update_dd(book, equity)
                n_cap, _ = slot_ladder(equity)
                slots_left = max(0, n_cap - slot_fn(book["open"]))
                take_n = min(slots, slots_left)
                if take_n <= 0 and cands:
                    book["skipped"]["slots_full"] += len(cands)
                    continue
                taken = 0
                for cand in cands:
                    if taken >= take_n:
                        break
                    sym = cand["symbol"]
                    if sym in book["open"]:
                        book["skipped"]["already_open"] += 1
                        continue
                    px = float(cand["entry"] or 0)
                    if px <= 0:
                        continue
                    equity = mark_fn(book["cash"], book["open"], bar_cache, date_str, hour)
                    _, unit_s = slot_ladder(equity)
                    budget = min(unit_s, book["cash"])
                    shares = shares_for_budget(budget, px)
                    if shares <= 0:
                        book["skipped"]["expensive"] += 1
                        continue
                    if book["cash"] < px * shares:
                        continue
                    book["cash"] -= px * shares
                    if book_name == "hard_1r":
                        book["open"][sym] = _open_1r(
                            sym=sym, date_str=date_str, hour=hour, px=px,
                            shares=shares, score=cand["score_v16"],
                            rank=cand["rank_in_hour"],
                        )
                    else:
                        profile = load_tsd_profile(sym) or {}
                        kill_pct, kill_src = resolve_kill_pct(
                            profile.get("kill_pct"), profile=profile,
                        )
                        trail = init_trail_state(
                            entry_price=px, n_shares=shares, profile=profile,
                        )
                        book["open"][sym] = {
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
                                }
                                for t in (trail.get("tranches") or [])
                            ],
                        }
                    taken += 1

            for book_name, mark_fn in (
                ("hard_1r", _mark_equity_1r),
                ("ratchet_4t", _mark_equity),
            ):
                book = books[book_name]
                eq = mark_fn(book["cash"], book["open"], bar_cache, date_str, hour)
                _update_dd(book, eq)

        # EOD marks for DD
        for book_name, mark_fn in (
            ("hard_1r", _mark_equity_1r),
            ("ratchet_4t", _mark_equity),
        ):
            book = books[book_name]
            eq = mark_fn(book["cash"], book["open"], bar_cache, date_str, 16)
            _update_dd(book, eq)
            if date_str == days[-1] or days.index(date_str) % 10 == 0:
                print(
                    f"  {date_str}  1R_eq=${_mark_equity_1r(books['hard_1r']['cash'], books['hard_1r']['open'], bar_cache, date_str, 16):,.0f}  "
                    f"4T_eq=${_mark_equity(books['ratchet_4t']['cash'], books['ratchet_4t']['open'], bar_cache, date_str, 16):,.0f}"
                )

    # Flatten leftovers at final EOD
    last = days[-1]
    # 1R
    b1 = books["hard_1r"]
    for sym in list(b1["open"].keys()):
        pos = b1["open"][sym]
        rem = int(pos["remaining"])
        px = _last_px(bar_cache.get(sym), last, 16, float(pos["entry_price"]))
        pos["realized_gross"] += (px - float(pos["entry_price"])) * rem
        pos["remaining"] = 0
        pos["tranche_exits"].append({
            "reason": "eod_mark", "shares": rem, "exit_price": px,
            "date": last, "hour": 16,
        })
        b1["cash"] += px * rem
        b1["closed"].append(_close_1r(pos, status="MARKED", exit_reason="range_eod_mark"))
        del b1["open"][sym]
    # 4T
    b4 = books["ratchet_4t"]
    for sym in list(b4["open"].keys()):
        pos = b4["open"][sym]
        rem = remaining_shares(pos["trail"])
        if rem <= 0:
            b4["closed"].append(
                _close_position(pos, status="CLOSED", exit_reason="trail_flat")
            )
            del b4["open"][sym]
            continue
        px = _last_px(bar_cache.get(sym), last, 16, float(pos["entry_price"]))
        pos["realized_gross"] = float(pos.get("realized_gross") or 0.0) + (
            px - float(pos["entry_price"])
        ) * rem
        b4["cash"] += px * rem
        b4["closed"].append(
            _close_position(pos, status="MARKED", exit_reason="range_eod_mark")
        )
        del b4["open"][sym]

    out_books = {}
    for name, book in books.items():
        closed = book["closed"]
        realized = sum(float(c.get("pnl") or 0) for c in closed)
        wins = [c for c in closed if float(c.get("pnl") or 0) > 0]
        out_books[name] = {
            "n_entries": len(closed),
            "n_wins": len(wins),
            "win_rate": round(len(wins) / len(closed), 4) if closed else None,
            "realized_pnl": round(realized, 2),
            "ending_equity": round(starting_cash + realized, 2),
            "return_pct": round(100.0 * realized / starting_cash, 2),
            "max_drawdown_pct": round(100.0 * float(book["max_dd"]), 2),
            "skipped": book["skipped"],
            "closed": closed,
        }
    return out_books


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--cash", type=float, default=3000.0)
    ap.add_argument("--seed", type=int, default=BLIND_SEED)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    t0 = time.time()
    pick = pick_blind_3m_window(seed=args.seed)
    start = args.start or pick["start"]
    end = args.end or pick["end"]

    print("=" * 72)
    print("PHP 3M DUAL-EXIT WALK-FORWARD")
    print(f"score={CONTINUATION_SCORE_VERSION}  cash=${args.cash:.0f}  slots/hour={args.slots}")
    print(f"blind seed={pick['seed']}  raw={pick['raw_draw_start']}..{pick['raw_draw_end']}")
    print(f"window used={start}..{end}  snapped={pick['snapped_to_corpus']}")
    print(f"  ({pick['reason']})")
    print("books: hard_1r (−5%/+5%)  vs  ratchet_4t (keep-profit)")
    print("=" * 72)

    if not CORPUS.is_file():
        raise SystemExit(f"Missing {CORPUS}")
    df = pd.read_csv(CORPUS)
    week = df[
        (df["signal_date"].astype(str) >= start)
        & (df["signal_date"].astype(str) <= end)
    ].copy()
    if week.empty:
        raise SystemExit(f"No corpus rows in {start}..{end}")

    print(f"Corpus rows: {len(week)}  days={week['signal_date'].nunique()}  "
          f"symbols={week['symbol'].nunique()}")
    print("Scoring v1.6...")
    week = score_week(week)
    planned = _hour_candidates(week, slots=args.slots)
    symbols = sorted({t["symbol"] for t in planned})
    print(f"Planned takes: {len(planned)} across {len(symbols)} symbols")

    key = load_polygon_key()
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

    print("Simulating dual books...")
    books = simulate_dual(
        start=start,
        end=end,
        starting_cash=args.cash,
        slots=args.slots,
        planned=planned,
        bar_cache=bar_cache,
    )

    a = books["hard_1r"]
    b = books["ratchet_4t"]
    print("\n" + "=" * 72)
    print(f"{'BOOK':<14} {'N':>5} {'Win%':>7} {'PnL':>10} {'Equity':>12} {'Ret%':>8} {'MaxDD%':>8}")
    for label, s in (("hard_1r", a), ("ratchet_4t", b)):
        print(
            f"{label:<14} {s['n_entries']:>5} "
            f"{(100*(s['win_rate'] or 0)):>6.1f}% "
            f"${s['realized_pnl']:>+9.2f} "
            f"${s['ending_equity']:>10.2f} "
            f"{s['return_pct']:>+7.2f}% "
            f"{s['max_drawdown_pct']:>7.2f}%"
        )
    delta = b["realized_pnl"] - a["realized_pnl"]
    winner = (
        "ratchet_4t" if delta > 0 else ("hard_1r" if delta < 0 else "tie")
    )
    print(f"\n4T − 1R PnL delta: ${delta:+.2f}  → winner: {winner}")
    print("=" * 72)

    report = {
        "protocol": {
            "blind": pick,
            "window": {"start": start, "end": end},
            "score_version": CONTINUATION_SCORE_VERSION,
            "slots_per_hour": args.slots,
            "starting_cash": args.cash,
            "hard_1r": {"kill_pct": KILL_PCT, "target_pct": TARGET_PCT},
            "ratchet_4t": "php_keep_profit_v1",
            "cost_per_trade": COST_PER_TRADE,
            "share_lot": SHARE_LOT,
            "same_entries": True,
        },
        "planned_takes": len(planned),
        "symbols": len(symbols),
        "books": {
            k: {kk: vv for kk, vv in v.items() if kk != "closed"}
            for k, v in books.items()
        },
        "closed_by_book": {k: v["closed"] for k, v in books.items()},
        "comparison": {
            "pnl_delta_4t_minus_1r": round(delta, 2),
            "winner": winner,
        },
        "runtime_sec": round(time.time() - t0, 1),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / (
        f"php_wf_3m_dual_{start.replace('-', '')}_{end.replace('-', '')}_s{args.slots}.json"
    )
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}  runtime={report['runtime_sec']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
