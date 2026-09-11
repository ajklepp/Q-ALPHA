#!/usr/bin/env python3
"""
Bar-path walk-forward: bake off the two study-winning multi-target ladders.

Same entries as 3m dual-exit (v1.6 top-3/hour, $3k, corpus Jun–Sep window):
  A) 2-target 0.40R/0.50R (2.0%/2.5%) weights 50/50
  B) 3-target 0.35R/0.50R/0.90R (1.75%/2.5%/4.5%) weights 50/25/25

Kill on residual = 5% (1R). Path-first on Polygon 1H bars.
COST_PER_TRADE=0.0015 RT; SHARE_LOT=4; leftovers EOD-marked.

Usage:
  py -3 candidates/tsd_scan_pipeline/php_wf_multi_target_bars.py
  py -3 candidates/tsd_scan_pipeline/php_wf_multi_target_bars.py --start 2026-06-08 --end 2026-09-04
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pickle
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
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_range_replay import (  # noqa: E402
    COST_PER_TRADE,
    _bar_ohlc_as_of,
    _last_px,
    trading_days,
)
from tsd_scan_pipeline.php_wf_3m_dual_exit import pick_blind_3m_window  # noqa: E402
from tsd_scan_pipeline.php_wf_week_cash_test import CORPUS, score_week  # noqa: E402
from tsd_scan_pipeline.php_wf_week_trail_sim import _hour_candidates  # noqa: E402
from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon  # noqa: E402
from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    SHARE_LOT,
    shares_for_budget,
    slot_ladder,
)
from tsd_scan_pipeline.tsd_launch_score import CONTINUATION_SCORE_VERSION  # noqa: E402
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
KILL_PCT = 0.05

LADDERS: dict[str, dict[str, Any]] = {
    "mt2_040_050": {
        "label": "Best overall 2-target",
        "targets_r": (0.40, 0.50),
        "targets_pct": (0.02, 0.025),
        "weights": (0.50, 0.50),
    },
    "mt3_035_050_090": {
        "label": "Best 3-target practical",
        "targets_r": (0.35, 0.50, 0.90),
        "targets_pct": (0.0175, 0.025, 0.045),
        "weights": (0.50, 0.25, 0.25),
    },
}


def alloc_shares(n: int, weights: tuple[float, ...]) -> list[int]:
    """Distribute share lots by weight; remainder to last slice."""
    if n < SHARE_LOT:
        return [n] + [0] * (len(weights) - 1)
    lots = n // SHARE_LOT
    rem_shares = n - lots * SHARE_LOT
    raw = [w * lots for w in weights]
    base = [int(x) for x in raw]
    # largest remainder for leftover lots
    left = lots - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (raw[i] - base[i], -i), reverse=True)
    for i in order:
        if left <= 0:
            break
        base[i] += 1
        left -= 1
    out = [b * SHARE_LOT for b in base]
    out[-1] += rem_shares
    # fix if somehow short
    diff = n - sum(out)
    out[-1] += diff
    return out


def _full_slots(open_pos: dict[str, dict[str, Any]]) -> int:
    return sum(1 for p in open_pos.values() if int(p.get("remaining") or 0) > 0)


def _mark_eq(
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


def _open_mt(
    *,
    sym: str,
    date_str: str,
    hour: int,
    px: float,
    shares: int,
    score: float,
    rank: int,
    ladder: dict[str, Any],
) -> dict[str, Any]:
    weights = tuple(ladder["weights"])
    targets = tuple(ladder["targets_pct"])
    slices = alloc_shares(shares, weights)
    slice_docs = []
    for i, (sh, t_pct) in enumerate(zip(slices, targets)):
        if sh <= 0:
            continue
        slice_docs.append({
            "id": f"S{i+1}",
            "shares": sh,
            "target_pct": t_pct,
            "target_price": round(px * (1.0 + t_pct), 4),
            "closed": False,
            "exit_price": None,
            "exit_reason": None,
        })
    return {
        "symbol": sym,
        "entry_date": date_str,
        "entry_hour": hour,
        "entry_price": round(px, 4),
        "shares": shares,
        "remaining": shares,
        "kill_pct": KILL_PCT,
        "kill_price": round(px * (1.0 - KILL_PCT), 4),
        "continuation_score": score,
        "rank_in_hour": rank,
        "notional": round(px * shares, 2),
        "slices": slice_docs,
        "realized_gross": 0.0,
        "tranche_exits": [],
        "ladder": ladder["label"],
    }


def _close_mt(pos: dict[str, Any], *, status: str, exit_reason: str) -> dict[str, Any]:
    entry = float(pos["entry_price"])
    shares = int(pos["shares"])
    cost = entry * shares * COST_PER_TRADE
    pnl = float(pos.get("realized_gross") or 0.0) - cost
    return {
        **{k: v for k, v in pos.items() if k not in ("slices",)},
        "slices": pos.get("slices"),
        "status": status,
        "exit_reason": exit_reason,
        "pnl": round(pnl, 2),
    }


def _advance_mt(
    pos: dict[str, Any],
    *,
    high: float,
    low: float,
    date_str: str,
    hour: int,
) -> tuple[float, bool]:
    """
    Apply kill then targets. Returns (cash_proceeds, fully_flat).
    """
    entry = float(pos["entry_price"])
    kill_px = float(pos["kill_price"])
    proceeds = 0.0

    if low <= kill_px:
        for sl in pos["slices"]:
            if sl["closed"]:
                continue
            sh = int(sl["shares"])
            sl["closed"] = True
            sl["exit_price"] = kill_px
            sl["exit_reason"] = "kill"
            proceeds += kill_px * sh
            pos["realized_gross"] += (kill_px - entry) * sh
            pos["tranche_exits"].append({
                "id": sl["id"], "shares": sh, "exit_price": kill_px,
                "reason": "kill", "date": date_str, "hour": hour,
            })
        pos["remaining"] = 0
        return proceeds, True

    for sl in pos["slices"]:
        if sl["closed"]:
            continue
        tgt = float(sl["target_price"])
        if high >= tgt:
            sh = int(sl["shares"])
            sl["closed"] = True
            sl["exit_price"] = tgt
            sl["exit_reason"] = "target"
            proceeds += tgt * sh
            pos["realized_gross"] += (tgt - entry) * sh
            pos["remaining"] = int(pos["remaining"]) - sh
            pos["tranche_exits"].append({
                "id": sl["id"], "shares": sh, "exit_price": tgt,
                "reason": "target", "date": date_str, "hour": hour,
            })

    flat = all(sl["closed"] for sl in pos["slices"]) or int(pos["remaining"]) <= 0
    if flat:
        pos["remaining"] = 0
    return proceeds, flat


def simulate_ladders(
    *,
    start: str,
    end: str,
    starting_cash: float,
    slots: int,
    planned: list[dict[str, Any]],
    bar_cache: dict[str, Any],
) -> dict[str, Any]:
    days = trading_days(
        datetime.strptime(start, "%Y-%m-%d").date(),
        datetime.strptime(end, "%Y-%m-%d").date(),
    )
    by_hour: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for t in planned:
        by_hour.setdefault((t["date"], t["hour"]), []).append(t)

    books: dict[str, dict[str, Any]] = {}
    for key, ladder in LADDERS.items():
        books[key] = {
            "ladder": ladder,
            "cash": float(starting_cash),
            "open": {},
            "closed": [],
            "skipped": {"slots_full": 0, "already_open": 0, "expensive": 0},
            "peak": float(starting_cash),
            "max_dd": 0.0,
        }

    def _dd(book: dict[str, Any], equity: float) -> None:
        book["peak"] = max(float(book["peak"]), equity)
        peak = float(book["peak"])
        if peak > 0:
            book["max_dd"] = max(float(book["max_dd"]), (peak - equity) / peak)

    for date_str in days:
        for hour in range(5, 16):
            # exits
            for book in books.values():
                for sym in list(book["open"].keys()):
                    pos = book["open"][sym]
                    if date_str == pos["entry_date"] and hour <= int(pos["entry_hour"]):
                        continue
                    ohlc = _bar_ohlc_as_of(bar_cache.get(sym), date_str, hour)
                    if not ohlc:
                        continue
                    high, low, _c = ohlc
                    proceeds, flat = _advance_mt(
                        pos, high=high, low=low, date_str=date_str, hour=hour,
                    )
                    book["cash"] += proceeds
                    if flat:
                        book["closed"].append(
                            _close_mt(pos, status="CLOSED", exit_reason="ladder_flat")
                        )
                        del book["open"][sym]

            # entries — same candidates into each book
            cands = by_hour.get((date_str, hour), [])
            for book in books.values():
                equity = _mark_eq(book["cash"], book["open"], bar_cache, date_str, hour)
                _dd(book, equity)
                n_cap, _ = slot_ladder(equity)
                take_n = min(slots, max(0, n_cap - _full_slots(book["open"])))
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
                    equity = _mark_eq(book["cash"], book["open"], bar_cache, date_str, hour)
                    _, unit_s = slot_ladder(equity)
                    budget = min(unit_s, book["cash"])
                    shares = shares_for_budget(budget, px)
                    if shares <= 0:
                        book["skipped"]["expensive"] += 1
                        continue
                    if book["cash"] < px * shares:
                        continue
                    book["cash"] -= px * shares
                    book["open"][sym] = _open_mt(
                        sym=sym,
                        date_str=date_str,
                        hour=hour,
                        px=px,
                        shares=shares,
                        score=cand["score_v16"],
                        rank=cand["rank_in_hour"],
                        ladder=book["ladder"],
                    )
                    taken += 1

            for book in books.values():
                eq = _mark_eq(book["cash"], book["open"], bar_cache, date_str, hour)
                _dd(book, eq)

        if days.index(date_str) % 10 == 0 or date_str == days[-1]:
            parts = []
            for k, book in books.items():
                eq = _mark_eq(book["cash"], book["open"], bar_cache, date_str, 16)
                parts.append(f"{k}=${eq:,.0f}")
            print(f"  {date_str}  " + "  ".join(parts))

    # EOD flatten
    last = days[-1]
    for book in books.values():
        for sym in list(book["open"].keys()):
            pos = book["open"][sym]
            rem = int(pos["remaining"])
            if rem <= 0:
                book["closed"].append(
                    _close_mt(pos, status="CLOSED", exit_reason="ladder_flat")
                )
                del book["open"][sym]
                continue
            px = _last_px(bar_cache.get(sym), last, 16, float(pos["entry_price"]))
            entry = float(pos["entry_price"])
            # close open slices at mark
            for sl in pos["slices"]:
                if sl["closed"]:
                    continue
                sh = int(sl["shares"])
                sl["closed"] = True
                sl["exit_price"] = px
                sl["exit_reason"] = "eod_mark"
                pos["realized_gross"] += (px - entry) * sh
                pos["tranche_exits"].append({
                    "id": sl["id"], "shares": sh, "exit_price": px,
                    "reason": "eod_mark", "date": last, "hour": 16,
                })
            book["cash"] += px * rem
            pos["remaining"] = 0
            book["closed"].append(
                _close_mt(pos, status="MARKED", exit_reason="range_eod_mark")
            )
            del book["open"][sym]

    out: dict[str, Any] = {}
    for key, book in books.items():
        closed = book["closed"]
        realized = sum(float(c.get("pnl") or 0) for c in closed)
        wins = [c for c in closed if float(c.get("pnl") or 0) > 0]
        # slice reason counts
        reasons: dict[str, int] = {}
        for c in closed:
            for e in c.get("tranche_exits") or []:
                reasons[str(e.get("reason"))] = reasons.get(str(e.get("reason")), 0) + 1
        out[key] = {
            "label": book["ladder"]["label"],
            "targets_r": list(book["ladder"]["targets_r"]),
            "targets_pct": [round(100 * x, 3) for x in book["ladder"]["targets_pct"]],
            "weights": list(book["ladder"]["weights"]),
            "n_entries": len(closed),
            "n_wins": len(wins),
            "win_rate": round(len(wins) / len(closed), 4) if closed else None,
            "realized_pnl": round(realized, 2),
            "ending_equity": round(starting_cash + realized, 2),
            "return_pct": round(100.0 * realized / starting_cash, 2),
            "max_drawdown_pct": round(100.0 * float(book["max_dd"]), 2),
            "skipped": book["skipped"],
            "slice_exits": reasons,
            "closed": closed,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--cash", type=float, default=3000.0)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    t0 = time.time()
    pick = pick_blind_3m_window()
    start = args.start or pick["start"]
    end = args.end or pick["end"]

    print("=" * 72)
    print("PHP MULTI-TARGET BAR WALK-FORWARD")
    print(f"score={CONTINUATION_SCORE_VERSION}  cash=${args.cash:.0f}  slots={args.slots}")
    print(f"window={start}..{end}  (snapped={pick['snapped_to_corpus']})")
    for k, L in LADDERS.items():
        print(
            f"  {k}: {L['label']}  R={L['targets_r']}  "
            f"%={[round(100*x,2) for x in L['targets_pct']]}  w={L['weights']}"
        )
    print("=" * 72)

    df = pd.read_csv(CORPUS)
    week = df[
        (df["signal_date"].astype(str) >= start)
        & (df["signal_date"].astype(str) <= end)
    ].copy()
    print(f"Corpus rows={len(week)} — scoring...")
    week = score_week(week)
    planned = _hour_candidates(week, slots=args.slots)
    symbols = sorted({t["symbol"] for t in planned})
    print(f"Planned takes={len(planned)}  symbols={len(symbols)}")

    # Reuse bar cache if present from dual-exit run
    cache_path = RESULTS / f"bar_cache_1h_{start.replace('-','')}_{end.replace('-','')}.pkl"
    bar_cache: dict[str, Any] = {}
    if cache_path.is_file():
        print(f"Loading bar cache {cache_path.name}...")
        try:
            with cache_path.open("rb") as f:
                loaded = pickle.load(f)
            if isinstance(loaded, dict):
                bar_cache = loaded
        except Exception as exc:
            print(f"  cache load failed: {exc}")

    key = load_polygon_key()
    miss = [s for s in symbols if s not in bar_cache or getattr(bar_cache.get(s), "__len__", lambda: 0)() == 0]
    if miss:
        print(f"Prefetching {len(miss)} missing 1H bars...")
        for i, sym in enumerate(miss, 1):
            try:
                bar_cache[sym] = _bars_1h_polygon(sym, api_key=key)
                time.sleep(0.12)
            except Exception as exc:
                print(f"  bar miss {sym}: {exc}")
                bar_cache[sym] = pd.DataFrame()
            if i % 25 == 0 or i == len(miss):
                print(f"  bars {i}/{len(miss)}")
        RESULTS.mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump(bar_cache, f, protocol=pickle.HIGHEST_PROTOCOL)
            print(f"Cached bars → {cache_path.name}")
        except Exception as exc:
            print(f"Cache write skipped: {exc}")

    print("Simulating both ladders...")
    books = simulate_ladders(
        start=start,
        end=end,
        starting_cash=args.cash,
        slots=args.slots,
        planned=planned,
        bar_cache=bar_cache,
    )

    print("\n" + "=" * 72)
    print(f"{'BOOK':<22} {'N':>5} {'Win%':>7} {'PnL':>10} {'Equity':>12} {'Ret%':>8} {'MaxDD%':>8}")
    for key, s in books.items():
        print(
            f"{key:<22} {s['n_entries']:>5} "
            f"{100*(s['win_rate'] or 0):>6.1f}% "
            f"${s['realized_pnl']:>+9.2f} "
            f"${s['ending_equity']:>10.2f} "
            f"{s['return_pct']:>+7.2f}% "
            f"{s['max_drawdown_pct']:>7.2f}%"
        )
        print(f"  exits: {s['slice_exits']}")
    a = books["mt2_040_050"]
    b = books["mt3_035_050_090"]
    delta = b["realized_pnl"] - a["realized_pnl"]
    winner = "mt3" if delta > 0 else ("mt2" if delta < 0 else "tie")
    print(f"\n3-target − 2-target PnL: ${delta:+.2f}  → winner: {winner}")
    print("=" * 72)

    report = {
        "protocol": {
            "window": {"start": start, "end": end},
            "blind": pick,
            "score_version": CONTINUATION_SCORE_VERSION,
            "slots": args.slots,
            "starting_cash": args.cash,
            "kill_pct": KILL_PCT,
            "cost_per_trade": COST_PER_TRADE,
        },
        "books": {
            k: {kk: vv for kk, vv in v.items() if kk != "closed"}
            for k, v in books.items()
        },
        "closed_by_book": {k: v["closed"] for k, v in books.items()},
        "comparison": {
            "pnl_delta_3tgt_minus_2tgt": round(delta, 2),
            "winner": winner,
        },
        "runtime_sec": round(time.time() - t0, 1),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / (
        f"php_wf_mt_bars_{start.replace('-','')}_{end.replace('-','')}_s{args.slots}.json"
    )
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}  runtime={report['runtime_sec']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
