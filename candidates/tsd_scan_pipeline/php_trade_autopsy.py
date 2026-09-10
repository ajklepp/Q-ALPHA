"""
Failure autopsy on php_range_replay trades.

For each trade computes:
  - MFE / MAE from entry until exit (1H highs/lows)
  - Structure risk (10-bar area low) at entry
  - Counterfactuals: structure kill, tighter % kill, BE lock after +X% MFE,
    early T1 scalp at +2%

Usage:
  py -3 candidates/tsd_scan_pipeline/php_trade_autopsy.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(ROOT / ".env")

from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon  # noqa: E402
from tsd_scan_pipeline.tsd_kill import (  # noqa: E402
    structure_area_low,
    structure_risk_pct,
)
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

ET = pytz.timezone("America/New_York")
COST = 0.0015


def _as_et_ts(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        return t.tz_localize(ET)
    return t.tz_convert(ET)


def _path_bars(
    bars: pd.DataFrame,
    *,
    entry_date: str,
    entry_hour: int,
    exit_date: str | None,
    exit_hour: int | None,
) -> list[dict[str, Any]]:
    """Bars after entry bar close through exit (inclusive of exit hour if set)."""
    out: list[dict[str, Any]] = []
    for ts, row in bars.iterrows():
        start = _as_et_ts(ts)
        d = start.date().isoformat()
        ch = (int(start.hour) + 1) % 24
        if d < entry_date:
            continue
        if d == entry_date and ch <= entry_hour:
            continue
        if exit_date:
            if d > exit_date:
                break
            if d == exit_date and exit_hour is not None and ch > int(exit_hour):
                break
        h = float(row.get("high") or 0)
        l = float(row.get("low") or 0)
        c = float(row.get("close") or 0)
        if h <= 0 or l <= 0:
            continue
        out.append({"date": d, "hour": ch, "high": h, "low": l, "close": c})
    return out


def _pre_entry_lows(bars: pd.DataFrame, entry_date: str, entry_hour: int, n: int = 10) -> list[float]:
    lows: list[float] = []
    for ts, row in bars.iterrows():
        start = _as_et_ts(ts)
        d = start.date().isoformat()
        ch = (int(start.hour) + 1) % 24
        if d > entry_date:
            break
        if d == entry_date and ch > entry_hour:
            break
        l = float(row.get("low") or 0)
        if l > 0:
            lows.append(l)
    return lows[-n:]


def autopsy_trade(trade: dict[str, Any], bars: pd.DataFrame) -> dict[str, Any]:
    entry = float(trade["entry_price"])
    shares = int(trade["shares"])
    ed = str(trade.get("entry_date") or "")
    eh = int(trade.get("entry_hour") or 0)
    xd = trade.get("exit_date")
    xh = trade.get("exit_hour")
    path = _path_bars(bars, entry_date=ed, entry_hour=eh, exit_date=xd, exit_hour=xh)

    mfe_px = entry
    mae_px = entry
    mfe_hour = None
    for b in path:
        if b["high"] > mfe_px:
            mfe_px = b["high"]
            mfe_hour = f"{b['date']}T{b['hour']}"
        if b["low"] < mae_px:
            mae_px = b["low"]
    mfe_pct = (mfe_px - entry) / entry
    mae_pct = (entry - mae_px) / entry

    pre_lows = _pre_entry_lows(bars, ed, eh, 10)
    area = structure_area_low(pre_lows, n=10)
    struct_risk = structure_risk_pct(entry, area)
    struct_stop = (area * 0.995) if area and area > 0 else None  # 0.5% buffer under structure
    struct_risk_buf = structure_risk_pct(entry, struct_stop) if struct_stop else None

    # Counterfactual A: kill at structure (with buffer) — flat at first pierce
    cf_struct_pnl = None
    cf_struct_exit = None
    if struct_stop and struct_stop < entry:
        for b in path:
            if b["low"] <= struct_stop:
                cf_struct_exit = struct_stop
                break
        if cf_struct_exit is None and path:
            # held to same end as original — use last close
            cf_struct_exit = path[-1]["close"]
        if cf_struct_exit is not None:
            cf_struct_pnl = (cf_struct_exit - entry) * shares - entry * shares * COST

    # Counterfactual B: 2.5% kill
    kill25 = entry * 0.975
    cf25_exit = None
    for b in path:
        if b["low"] <= kill25:
            cf25_exit = kill25
            break
    if cf25_exit is None and path:
        cf25_exit = path[-1]["close"]
    cf25_pnl = (
        (cf25_exit - entry) * shares - entry * shares * COST
        if cf25_exit is not None else None
    )

    # Counterfactual C: BE lock after +2% MFE touched, then stop at entry
    be_armed = False
    cf_be_exit = None
    for b in path:
        if b["high"] >= entry * 1.02:
            be_armed = True
        if be_armed and b["low"] <= entry:
            cf_be_exit = entry
            break
    if cf_be_exit is None and path:
        cf_be_exit = path[-1]["close"]
    cf_be_pnl = (
        (cf_be_exit - entry) * shares - entry * shares * COST
        if cf_be_exit is not None else None
    )

    # Counterfactual D: scalp 50% at +2%, rest to original exit path mark
    # Approximate: half at +2% if MFE>=2%, half at original avg exit
    orig_pnl = float(trade.get("pnl") or 0)
    scalp_pnl = None
    if mfe_pct >= 0.02:
        half = shares // 2
        rest = shares - half
        # find first bar that hit +2%
        scalp_px = entry * 1.02
        # remainder: use original realized approx = orig_pnl without cost re-split
        # Use last path close as remainder mark if we don't have tranche detail
        rem_px = path[-1]["close"] if path else entry
        # If trade was kill loser, rem likely also bad — use same as original exit
        exits = trade.get("tranche_exits") or []
        if exits:
            # weighted avg original exit
            rem_px = sum(float(e["exit_price"]) * int(e["shares"]) for e in exits) / max(
                1, sum(int(e["shares"]) for e in exits)
            )
        gross = (scalp_px - entry) * half + (rem_px - entry) * rest
        scalp_pnl = gross - entry * shares * COST

    # Was profitable before loss? MFE > 1% then closed red
    was_green = mfe_pct >= 0.01 and orig_pnl < 0
    gave_back = mfe_pct >= 0.02 and orig_pnl < 0

    return {
        "symbol": trade.get("symbol"),
        "entry_date": ed,
        "entry_hour": eh,
        "entry": entry,
        "shares": shares,
        "orig_pnl": orig_pnl,
        "status": trade.get("status"),
        "exit_reason": trade.get("exit_reason"),
        "mfe_pct": round(mfe_pct * 100, 2),
        "mae_pct": round(mae_pct * 100, 2),
        "mfe_at": mfe_hour,
        "struct_risk_pct": round(struct_risk * 100, 2) if struct_risk is not None else None,
        "struct_stop_risk_pct": round(struct_risk_buf * 100, 2) if struct_risk_buf is not None else None,
        "was_green_then_lost": was_green,
        "gave_back_2pct_plus": gave_back,
        "cf_struct_pnl": round(cf_struct_pnl, 2) if cf_struct_pnl is not None else None,
        "cf_kill25_pnl": round(cf25_pnl, 2) if cf25_pnl is not None else None,
        "cf_be_lock_2pct_pnl": round(cf_be_pnl, 2) if cf_be_pnl is not None else None,
        "cf_scalp_half_2pct_pnl": round(scalp_pnl, 2) if scalp_pnl is not None else None,
        "delta_struct": round(cf_struct_pnl - orig_pnl, 2) if cf_struct_pnl is not None else None,
        "delta_kill25": round(cf25_pnl - orig_pnl, 2) if cf25_pnl is not None else None,
        "delta_be": round(cf_be_pnl - orig_pnl, 2) if cf_be_pnl is not None else None,
        "delta_scalp": round(scalp_pnl - orig_pnl, 2) if scalp_pnl is not None else None,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--replay",
        default=str(
            PIPELINE_DIR / "results" / "php_range_replay_20260831_20260910.json"
        ),
    )
    args = p.parse_args()
    doc = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    trades = doc.get("trades") or []
    key = load_polygon_key()
    rows: list[dict[str, Any]] = []
    cache: dict[str, pd.DataFrame] = {}

    print(f"Autopsy {len(trades)} trades from {args.replay}")
    for i, t in enumerate(trades, 1):
        sym = str(t.get("symbol") or "").upper()
        if sym not in cache:
            try:
                cache[sym] = _bars_1h_polygon(sym, api_key=key)
                time.sleep(0.12)
            except Exception as exc:
                print(f"  skip {sym}: {exc}")
                continue
        row = autopsy_trade(t, cache[sym])
        rows.append(row)
        print(
            f"  {row['entry_date']} {sym:5} pnl={row['orig_pnl']:+7.2f} "
            f"MFE={row['mfe_pct']:5.1f}% MAE={row['mae_pct']:5.1f}% "
            f"struct={row['struct_risk_pct']} "
            f"green_then_lost={int(row['was_green_then_lost'])} "
            f"d_struct={row['delta_struct']} d_be={row['delta_be']} d_scalp={row['delta_scalp']}"
        )

    def _sum(key: str) -> float:
        return round(sum(float(r[key]) for r in rows if r.get(key) is not None), 2)

    orig = _sum("orig_pnl")
    summary = {
        "n": len(rows),
        "orig_pnl": orig,
        "cf_struct_pnl": _sum("cf_struct_pnl"),
        "cf_kill25_pnl": _sum("cf_kill25_pnl"),
        "cf_be_lock_2pct_pnl": _sum("cf_be_lock_2pct_pnl"),
        "cf_scalp_half_2pct_pnl": _sum("cf_scalp_half_2pct_pnl"),
        "n_green_then_lost": sum(1 for r in rows if r["was_green_then_lost"]),
        "n_gave_back_2pct": sum(1 for r in rows if r["gave_back_2pct_plus"]),
        "avg_mfe_losers": round(
            sum(r["mfe_pct"] for r in rows if r["orig_pnl"] < 0)
            / max(1, sum(1 for r in rows if r["orig_pnl"] < 0)),
            2,
        ),
        "avg_mae_winners": round(
            sum(r["mae_pct"] for r in rows if r["orig_pnl"] > 0)
            / max(1, sum(1 for r in rows if r["orig_pnl"] > 0)),
            2,
        ),
        "avg_struct_risk": round(
            sum(r["struct_risk_pct"] or 0 for r in rows)
            / max(1, sum(1 for r in rows if r["struct_risk_pct"] is not None)),
            2,
        ),
        "trades": rows,
    }
    # deltas vs orig
    for k in ("cf_struct_pnl", "cf_kill25_pnl", "cf_be_lock_2pct_pnl", "cf_scalp_half_2pct_pnl"):
        summary[f"lift_{k}"] = round(summary[k] - orig, 2)

    out = PIPELINE_DIR / "results" / "php_trade_autopsy_20260831_20260910.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(f"Original P&L:     ${summary['orig_pnl']:+.2f}")
    print(f"Structure kill:   ${summary['cf_struct_pnl']:+.2f}  lift={summary['lift_cf_struct_pnl']:+.2f}")
    print(f"2.5% kill:        ${summary['cf_kill25_pnl']:+.2f}  lift={summary['lift_cf_kill25_pnl']:+.2f}")
    print(f"BE after +2%:     ${summary['cf_be_lock_2pct_pnl']:+.2f}  lift={summary['lift_cf_be_lock_2pct_pnl']:+.2f}")
    print(f"Half scalp +2%:   ${summary['cf_scalp_half_2pct_pnl']:+.2f}  lift={summary['lift_cf_scalp_half_2pct_pnl']:+.2f}")
    print(f"Green-then-lost:  {summary['n_green_then_lost']} / {summary['n']}")
    print(f"Gave back >=2%:   {summary['n_gave_back_2pct']}")
    print(f"Avg MFE losers:   {summary['avg_mfe_losers']}%")
    print(f"Avg MAE winners:  {summary['avg_mae_winners']}%")
    print(f"Avg struct risk:  {summary['avg_struct_risk']}%")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
