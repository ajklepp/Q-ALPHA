#!/usr/bin/env python3
"""
Blind Peak Hour walk-forward cash test for continuation_score v1.6.

Protocol (no peeking):
  1. Pick a completed Mon–Fri week uniformly from the prior ~2 months
     using a fixed draw seed (documented). Outcomes are not consulted.
  2. Rank each hour using ONLY pre-signal features via live
     compute_continuation_score (v1.6). Never touch hit_1r / mfe / r_multiple
     for ranking.
  3. Take top `slots` admitted names per hour; size from $3k slot_ladder +
     SHARE_LOT=4; apply COST_PER_TRADE=0.0015 RT on entry notional.
  4. Realize dollar PnL from corpus path labels: R$ = kill_pct * entry * shares
     × r_multiple (hit=+1R, kill=-1R, else mfe/kill).

Usage:
  py -3 candidates/tsd_scan_pipeline/php_wf_week_cash_test.py
  py -3 candidates/tsd_scan_pipeline/php_wf_week_cash_test.py --slots 2 --slots 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    SHARE_LOT,
    shares_for_budget,
    slot_ladder,
)
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    CONTINUATION_SCORE_VERSION,
    compute_continuation_score,
    is_continuation_list_candidate,
)

COST_PER_TRADE = 0.0015
KILL_PCT = 0.05  # matches EXP-0021 path labels (1R = 5%)
CORPUS = ROOT / "experiments" / "EXP-0021" / "corpus_htf_universe_social.csv"
RESULTS = Path(__file__).resolve().parent / "results"
# Blind draw seed — selects the week only; does not encode outcomes.
BLIND_DRAW_SEED = 20260911
TODAY_ASOF = date(2026, 9, 11)


def candidate_weeks(asof: date) -> list[tuple[date, date]]:
    """Completed Mon–Fri weeks whose Friday is in [asof-60d, asof-7d]."""
    out: list[tuple[date, date]] = []
    d = asof - timedelta(days=60)
    end_lim = asof - timedelta(days=7)
    while d <= end_lim:
        if d.weekday() == 0:
            fri = d + timedelta(days=4)
            if fri <= end_lim:
                out.append((d, fri))
        d += timedelta(days=1)
    return out


def pick_blind_week(*, seed: int = BLIND_DRAW_SEED, asof: date = TODAY_ASOF) -> dict[str, Any]:
    weeks = candidate_weeks(asof)
    if not weeks:
        raise RuntimeError("No candidate weeks in lookback")
    rng = random.Random(seed)
    mon, fri = rng.choice(weeks)
    return {
        "seed": seed,
        "asof": asof.isoformat(),
        "n_candidates": len(weeks),
        "start": mon.isoformat(),
        "end": fri.isoformat(),
        "candidates": [(a.isoformat(), b.isoformat()) for a, b in weeks],
    }


def _row_to_score_feat(row: dict[str, Any]) -> dict[str, Any]:
    """Map corpus row → live ranker fields (signal-time only)."""
    feat = dict(row)
    feat["htf_1h_bar_hour"] = int(row.get("hour") or 0)
    feat["1h_close"] = float(row.get("close") or 0)
    # Prefer live recompute; drop stored continuation so we do not leak v1.
    feat.pop("continuation_score", None)
    feat.pop("continuation_score_v0", None)
    feat.pop("continuation_score_v1", None)
    return feat


def score_week(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    scores: list[float] = []
    admit: list[int] = []
    for _, row in work.iterrows():
        feat = _row_to_score_feat(row.to_dict())
        try:
            ok = is_continuation_list_candidate(feat)
        except Exception:
            ok = False
        admit.append(1 if ok else 0)
        try:
            scores.append(float(compute_continuation_score(feat)))
        except Exception:
            scores.append(float("-inf"))
    work["score_v16"] = scores
    work["admit_v16"] = admit
    return work


def simulate_cash(
    week: pd.DataFrame,
    *,
    slots: int,
    starting_cash: float = 3000.0,
) -> dict[str, Any]:
    """Hour-by-hour top-N by score_v16; independent positions (corpus labels)."""
    cash = float(starting_cash)
    equity = float(starting_cash)
    trades: list[dict[str, Any]] = []
    peak = equity
    max_dd = 0.0

    days = sorted(week["signal_date"].astype(str).unique())
    for day in days:
        day_df = week[week["signal_date"].astype(str) == day]
        for hour in sorted(int(h) for h in day_df["hour"].unique()):
            hour_df = day_df[day_df["hour"].astype(int) == hour]
            pool = hour_df[hour_df["admit_v16"] == 1].sort_values(
                "score_v16", ascending=False
            )
            take = pool.head(slots)
            if take.empty:
                continue

            _, unit_s = slot_ladder(equity)
            for _, r in take.iterrows():
                px = float(r["close"] or 0)
                if px <= 0 or cash < px * SHARE_LOT:
                    continue
                budget = min(unit_s, cash)
                shares = shares_for_budget(budget, px)
                if shares <= 0:
                    continue
                notional = shares * px
                cost = notional * COST_PER_TRADE
                r_mult = float(r["r_multiple"])
                # Path label → dollars: 1R = KILL_PCT of entry notional
                gross = notional * KILL_PCT * r_mult
                pnl = gross - cost
                equity = equity + pnl
                cash = equity  # flat book (corpus labels already include exit path)
                peak = max(peak, equity)
                dd = (peak - equity) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                trades.append({
                    "date": day,
                    "hour": int(hour),
                    "symbol": str(r["symbol"]).upper(),
                    "score_v16": round(float(r["score_v16"]), 2),
                    "shares": shares,
                    "entry": round(px, 4),
                    "notional": round(notional, 2),
                    "r_multiple": round(r_mult, 4),
                    "hit_1r": int(r["hit_1r"]),
                    "mfe": round(float(r["mfe"]), 4),
                    "pnl": round(pnl, 2),
                    "equity_after": round(equity, 2),
                })

    seen: dict[tuple[str, int], int] = {}
    for t in trades:
        key = (t["date"], t["hour"])
        seen[key] = seen.get(key, 0) + 1
        t["rank_in_hour"] = seen[key]

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = equity - starting_cash
    return {
        "slots": slots,
        "starting_cash": starting_cash,
        "ending_equity": round(equity, 2),
        "pnl": round(total_pnl, 2),
        "return_pct": round(100.0 * total_pnl / starting_cash, 2),
        "n_trades": len(trades),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "avg_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else None,
        "avg_r": round(
            sum(t["r_multiple"] for t in trades) / len(trades), 4
        ) if trades else None,
        "hit_1r_rate": round(
            sum(t["hit_1r"] for t in trades) / len(trades), 4
        ) if trades else None,
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "trades": trades,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, action="append", default=None)
    ap.add_argument("--cash", type=float, default=3000.0)
    ap.add_argument("--seed", type=int, default=BLIND_DRAW_SEED)
    ap.add_argument("--start", default=None, help="Override blind week start (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="Override blind week end")
    args = ap.parse_args()
    slot_list = args.slots or [2, 3]

    pick = pick_blind_week(seed=args.seed)
    start = args.start or pick["start"]
    end = args.end or pick["end"]

    if not CORPUS.is_file():
        raise SystemExit(f"Missing corpus {CORPUS}")

    print("=" * 72)
    print("PHP BLIND WF CASH TEST")
    print(f"score={CONTINUATION_SCORE_VERSION}  cash=${args.cash:.0f}  slots={slot_list}")
    print(f"blind pick seed={pick['seed']}  week={start}..{end}  "
          f"(from {pick['n_candidates']} candidates, outcomes not used in draw)")
    print("=" * 72)

    df = pd.read_csv(CORPUS)
    week = df[
        (df["signal_date"].astype(str) >= start)
        & (df["signal_date"].astype(str) <= end)
    ].copy()
    if week.empty:
        raise SystemExit(f"No corpus rows for {start}..{end}")

    print(f"Corpus week rows: {len(week)}  "
          f"({week['signal_date'].nunique()} days, "
          f"{week['symbol'].nunique()} symbols)")
    print("Scoring with live v1.6 (features only)...")
    week = score_week(week)
    n_admit = int(week["admit_v16"].sum())
    print(f"Admitted: {n_admit}/{len(week)}")

    runs: dict[str, Any] = {}
    for slots in slot_list:
        print(f"\n--- slots={slots} ---")
        res = simulate_cash(week, slots=slots, starting_cash=args.cash)
        runs[str(slots)] = {k: v for k, v in res.items() if k != "trades"}
        runs[str(slots)]["trades"] = res["trades"]
        print(
            f"  trades={res['n_trades']}  win%={res['win_rate']}  "
            f"avgR={res['avg_r']}  hit1R={res['hit_1r_rate']}"
        )
        print(
            f"  equity ${res['starting_cash']:.2f} -> ${res['ending_equity']:.2f}  "
            f"PnL ${res['pnl']:+.2f} ({res['return_pct']:+.2f}%)  "
            f"maxDD {res['max_drawdown_pct']:.2f}%"
        )

    # Slot comparison
    if "2" in runs and "3" in runs:
        d_pnl = runs["3"]["pnl"] - runs["2"]["pnl"]
        d_n = runs["3"]["n_trades"] - runs["2"]["n_trades"]
        print("\n=== 3-slot vs 2-slot ===")
        print(f"  extra trades: {d_n:+d}")
        print(f"  PnL delta: ${d_pnl:+.2f}")
        better = "3-slot" if d_pnl > 0 else ("2-slot" if d_pnl < 0 else "tie")
        print(f"  winner on PnL: {better}")

    report = {
        "protocol": {
            "blind_week": pick,
            "week_used": {"start": start, "end": end},
            "score_version": CONTINUATION_SCORE_VERSION,
            "starting_cash": args.cash,
            "kill_pct": KILL_PCT,
            "cost_per_trade": COST_PER_TRADE,
            "share_lot": SHARE_LOT,
            "no_peeking": (
                "Week drawn before reading outcomes; ranking uses only signal-time "
                "features via compute_continuation_score; labels applied after take."
            ),
            "corpus": str(CORPUS.relative_to(ROOT)),
        },
        "runs": {
            k: {kk: vv for kk, vv in v.items() if kk != "trades"}
            for k, v in runs.items()
        },
        "trades_by_slots": {k: v["trades"] for k, v in runs.items()},
        "comparison_3_vs_2": (
            {
                "pnl_delta": round(runs["3"]["pnl"] - runs["2"]["pnl"], 2),
                "trade_delta": runs["3"]["n_trades"] - runs["2"]["n_trades"],
                "winner": (
                    "3-slot"
                    if runs["3"]["pnl"] > runs["2"]["pnl"]
                    else ("2-slot" if runs["3"]["pnl"] < runs["2"]["pnl"] else "tie")
                ),
            }
            if "2" in runs and "3" in runs
            else None
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"php_wf_cash_{start.replace('-', '')}_{end.replace('-', '')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
