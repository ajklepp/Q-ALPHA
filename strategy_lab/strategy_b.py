"""
strategy_lab/strategy_b.py — "Target" (scale-out) exit strategy (lab only).

Operates on ONE setup at a time; returns a structured trade result.
Mirrors strategy_a.py inputs / sizing / return shape for direct comparison.

Rules (targets from cached profile MFE percentiles; fixed fallbacks if INSUFFICIENT):
  - Same 4 tranches 40/30/20/10 (same auto-collapse) and $3000 / ~1% risk sizing
  - Kill-all hard stop = safe_max_stop_pct (fallback ~7%) — same as Strategy A
  - Fixed price TARGETS (not trailing):
      T1 = early step (same early level A uses as T1 trigger)
      T2 = MFE p50; T3 = MFE p75; T4 = MFE p90 (runner IS capped at p90)
  - Per bar: check KILL on LOW first, then TARGETS on HIGH (conservative:
    if both fire on the same daily bar, kill wins)
  - Unhit targets stay open until kill or 20-trading-day time cap at close
  - Flag day: 1-min bar-by-bar; after: daily high/low

Does NOT modify agent files or profiles/.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "candidates"))

# Reuse Strategy A helpers so sizing / levels / loaders stay comparable.
from strategy_a import (  # noqa: E402
    BARS_DIR,
    ET,
    HISTORY_PATH,
    MAX_HOLD_TRADING_DAYS,
    POOL_USD,
    RISK_FRAC,
    _parse_iso,
    _trading_days_inclusive,
    extract_levels,
    fetch_daily_after,
    load_minute_bars,
    load_profile,
    size_shares,
    split_tranches,
    triggers_for_n,
)


@dataclass
class TrancheState:
    id: str
    shares: int
    weight: float
    target_pct: float
    target_price: float
    closed: bool = False
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None  # target | kill | time_cap


@dataclass
class SimState:
    entry_price: float
    kill_price: float
    kill_pct: float
    tranches: list[TrancheState] = field(default_factory=list)
    peak_high: float = 0.0
    trading_day: int = 1
    last_bar_time: str | None = None
    last_close: float | None = None


def _close_tranche(t: TrancheState, price: float, when: str, reason: str) -> None:
    if t.closed:
        return
    t.closed = True
    t.exit_price = float(price)
    t.exit_time = when
    t.exit_reason = reason


def _close_all(state: SimState, price: float, when: str, reason: str) -> None:
    for t in state.tranches:
        if not t.closed:
            _close_tranche(t, price, when, reason)


def _all_closed(state: SimState) -> bool:
    return all(t.closed for t in state.tranches)


def process_bar(
    state: SimState,
    *,
    high: float,
    low: float,
    close: float,
    when: str,
    force_time_cap: bool = False,
) -> None:
    """
    One evaluation step (1-min or daily).

    Conservative intrabar order for longs:
      1) Kill on LOW -> sell ALL remaining at kill_price
      2) Targets on HIGH -> sell each open tranche whose target is reached
         (at its target price). Kill already won if both would fire same bar.
      3) Optional time-cap exit at CLOSE
    """
    if _all_closed(state):
        return

    state.last_bar_time = when
    state.last_close = close
    state.peak_high = max(state.peak_high, high)

    # 1) Kill-all first (priority over targets on the same bar)
    if low <= state.kill_price:
        _close_all(state, state.kill_price, when, "kill")
        return

    # 2) Fixed targets — each tranche independent
    for t in state.tranches:
        if t.closed:
            continue
        if high >= t.target_price:
            _close_tranche(t, t.target_price, when, "target")

    if _all_closed(state):
        return

    # 3) Time cap
    if force_time_cap:
        _close_all(state, close, when, "time_cap")


def run_strategy_b(
    *,
    ticker: str,
    flag_date: str,
    entry_price: float,
    entry_time: str,
    minute_bars: list[dict],
    daily_bars: list[dict],
    profile: dict[str, Any],
    n_shares: int | None = None,
    close_at_data_end: bool = True,
) -> dict[str, Any]:
    """
    Simulate Strategy B on one setup. Returns Strategy-A-shaped trade result.

    n_shares / close_at_data_end: same semantics as run_strategy_a (live settle).
    """
    levels = extract_levels(profile)
    kill_pct = levels["kill_pct"]
    # Same ascending early/p50/p75/p90 ladder A uses as triggers — here as targets.
    targets_4 = list(levels["triggers_4"])
    n_shares = int(n_shares) if n_shares is not None else size_shares(entry_price, kill_pct)
    alloc = split_tranches(n_shares)
    tgts = triggers_for_n(targets_4, len(alloc))

    tranches: list[TrancheState] = []
    for (tid, sh, w), tgt in zip(alloc, tgts):
        tranches.append(
            TrancheState(
                id=tid,
                shares=sh,
                weight=w,
                target_pct=float(tgt),
                target_price=entry_price * (1.0 + float(tgt)),
            )
        )

    state = SimState(
        entry_price=entry_price,
        kill_price=entry_price * (1.0 - kill_pct),
        kill_pct=kill_pct,
        tranches=tranches,
        peak_high=entry_price,
    )

    if n_shares <= 0 or not tranches:
        return _empty_result(
            ticker, flag_date, entry_price, entry_time, levels,
            reason="zero_shares",
        )

    entry_ts = _parse_iso(entry_time)
    for b in minute_bars:
        if _all_closed(state):
            break
        when = str(b.get("t_et") or b.get("t") or "")
        bar_ts = _parse_iso(when) if when else None
        if bar_ts is not None and entry_ts is not None and bar_ts < entry_ts:
            continue
        try:
            h, lo, c = float(b["h"]), float(b["l"]), float(b["c"])
        except (KeyError, TypeError, ValueError):
            continue
        process_bar(state, high=h, low=lo, close=c, when=when or flag_date)

    post = [d for d in daily_bars if str(d.get("date") or "")[:10] > flag_date]
    post.sort(key=lambda d: str(d.get("date"))[:10])

    for dbar in post:
        if _all_closed(state):
            break
        state.trading_day += 1
        dstr = str(dbar.get("date"))[:10]
        try:
            h = float(dbar["high"] if "high" in dbar else dbar["h"])
            lo = float(dbar["low"] if "low" in dbar else dbar["l"])
            c = float(dbar["close"] if "close" in dbar else dbar["c"])
        except (KeyError, TypeError, ValueError):
            continue
        hit_cap = state.trading_day >= MAX_HOLD_TRADING_DAYS
        process_bar(
            state, high=h, low=lo, close=c, when=dstr, force_time_cap=hit_cap,
        )
        if hit_cap:
            break

    if (
        close_at_data_end
        and not _all_closed(state)
        and state.last_close is not None
    ):
        _close_all(
            state,
            state.last_close,
            state.last_bar_time or flag_date,
            "time_cap",
        )

    return _build_result(
        ticker=ticker,
        flag_date=flag_date,
        entry_price=entry_price,
        entry_time=entry_time,
        levels=levels,
        targets_4=targets_4,
        state=state,
        n_shares=n_shares,
    )


def _empty_result(
    ticker: str,
    flag_date: str,
    entry_price: float,
    entry_time: str,
    levels: dict,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "strategy": "B_target",
        "ticker": ticker,
        "flag_date": flag_date,
        "entry_price": entry_price,
        "entry_time": entry_time,
        "status": "skipped",
        "skip_reason": reason,
        "levels": levels,
        "n_shares": 0,
        "tranches": [],
        "total_return_pct": None,
        "mfe_pct": None,
        "days_held": 0,
    }


def _build_result(
    *,
    ticker: str,
    flag_date: str,
    entry_price: float,
    entry_time: str,
    levels: dict,
    targets_4: list[float],
    state: SimState,
    n_shares: int,
) -> dict[str, Any]:
    notional = n_shares * entry_price
    pnl = 0.0
    tranche_rows: list[dict[str, Any]] = []
    last_exit_day = flag_date
    all_closed = _all_closed(state)
    open_shares = 0

    for t in state.tranches:
        if t.exit_price is None:
            open_shares += int(t.shares)
            mark = (
                float(state.last_close)
                if state.last_close is not None
                else float(entry_price)
            )
            tranche_rows.append({
                "id": t.id,
                "shares": t.shares,
                "weight": t.weight,
                "trigger_pct": round(t.target_pct, 6),
                "trigger_price": round(t.target_price, 4),
                "target_pct": round(t.target_pct, 6),
                "target_price": round(t.target_price, 4),
                "exit_price": None,
                "exit_time": None,
                "exit_reason": None,
                "open": True,
                "mark_price": round(mark, 4),
                "return_pct": None,
                "pnl_usd": None,
            })
            continue
        px = float(t.exit_price)
        pnl += t.shares * (px - entry_price)
        ret_i = (px - entry_price) / entry_price if entry_price else 0.0
        exit_day = (t.exit_time or flag_date)[:10]
        if exit_day > last_exit_day:
            last_exit_day = exit_day
        tranche_rows.append({
            "id": t.id,
            "shares": t.shares,
            "weight": t.weight,
            "trigger_pct": round(t.target_pct, 6),
            "trigger_price": round(t.target_price, 4),
            "target_pct": round(t.target_pct, 6),
            "target_price": round(t.target_price, 4),
            "exit_price": round(px, 4),
            "exit_time": t.exit_time,
            "exit_reason": t.exit_reason,
            "open": False,
            "return_pct": round(ret_i * 100, 4),
            "pnl_usd": round(t.shares * (px - entry_price), 4),
        })

    total_ret = (pnl / notional) if notional else 0.0
    mfe = (state.peak_high - entry_price) / entry_price if entry_price else 0.0
    days_held = _trading_days_inclusive(flag_date, last_exit_day)
    reasons = [r["exit_reason"] for r in tranche_rows if r.get("exit_reason")]

    return {
        "strategy": "B_target",
        "ticker": ticker,
        "flag_date": flag_date,
        "entry_price": round(entry_price, 4),
        "entry_time": entry_time,
        "status": "ok" if all_closed else "open",
        "still_open": not all_closed,
        "open_shares": open_shares,
        "last_close": (
            None if state.last_close is None else round(float(state.last_close), 4)
        ),
        "levels": {
            **levels,
            "kill_pct": round(levels["kill_pct"], 6),
            "trail_pct": round(float(levels.get("trail_pct") or 0.0), 6),
            "triggers_4": [round(x, 6) for x in targets_4],
            "targets_4": [round(x, 6) for x in targets_4],
            "kill_price": round(state.kill_price, 4),
        },
        "n_shares": n_shares,
        "notional_usd": round(notional, 2),
        "pool_usd": POOL_USD,
        "risk_frac": RISK_FRAC,
        "tranches": tranche_rows,
        "total_pnl_usd": round(pnl, 4),
        "total_return_pct": round(total_ret * 100, 4) if all_closed else None,
        "mfe_pct": round(mfe * 100, 4),
        "days_held": days_held,
        "trading_day_at_end": state.trading_day,
        "exit_reason_counts": {
            "target": reasons.count("target"),
            "kill": reasons.count("kill"),
            "time_cap": reasons.count("time_cap"),
            "trail": 0,
        },
    }


def load_setup_and_run(ticker: str, flag_date: str) -> dict[str, Any]:
    """Wire history.json + bars + profile + daily fetch -> run_strategy_b."""
    hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    key = f"{ticker.upper()}|{flag_date}"
    row = (hist.get("history") or {}).get(key)
    if not row or row.get("status") != "ok":
        raise RuntimeError(f"No ok history for {key}")

    entry_price = float(row["entry_price"])
    entry_time = str(row["entry_time"])
    bars_path = (
        row.get("minute_bars_path")
        or row.get("bars_path")
        or str(BARS_DIR / f"{ticker.upper()}_{flag_date}.json")
    )
    minute_bars = load_minute_bars(bars_path)
    profile = load_profile(ticker, flag_date)
    daily_bars = fetch_daily_after(ticker, flag_date)

    return run_strategy_b(
        ticker=ticker.upper(),
        flag_date=flag_date,
        entry_price=entry_price,
        entry_time=entry_time,
        minute_bars=minute_bars,
        daily_bars=daily_bars,
        profile=profile,
    )


def _print_result(res: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"STRATEGY B (Target) — {res['ticker']}  flag={res['flag_date']}")
    print("=" * 72)
    print(f"  Entry:     ${res['entry_price']:.4f}  @ {res['entry_time']}")
    print(f"  Shares:    {res['n_shares']}  notional=${res.get('notional_usd')}")
    lv = res.get("levels") or {}
    print(
        f"  Levels:    kill={lv.get('kill_pct')}  "
        f"source={lv.get('source')}  conf={lv.get('confidence')}"
    )
    print(f"  Kill px:   ${lv.get('kill_price')}")
    print(f"  Targets4:  {lv.get('targets_4')}")
    print("-" * 72)
    print("  Per-tranche (fixed target scale-out):")
    for t in res.get("tranches") or []:
        print(
            f"  {t['id']}: sh={t['shares']}  "
            f"target=${t['target_price']:.4f} ({t['target_pct']*100:.2f}%)"
        )
        print(
            f"       exit=${t['exit_price']:.4f}  "
            f"ret={t['return_pct']:.2f}%  "
            f"reason={t['exit_reason']}  when={t['exit_time']}"
        )
    print("-" * 72)
    print(f"  Total return % : {res.get('total_return_pct')}%")
    print(f"  MFE reached %  : {res.get('mfe_pct')}%")
    print(f"  Days held      : {res.get('days_held')}")
    print(f"  Exit counts    : {res.get('exit_reason_counts')}")
    print("=" * 72)


def main() -> None:
    # Same self-test setup as Strategy A for a direct A-vs-B comparison.
    ticker, flag_date = "JOBY", "2024-11-11"
    print(
        f"Self-test: Strategy B on ONE HIGH-confidence setup "
        f"-> {ticker}|{flag_date}\n"
    )
    res = load_setup_and_run(ticker, flag_date)
    _print_result(res)


if __name__ == "__main__":
    main()
