"""
strategy_lab/strategy_a.py — "Trailing" exit strategy (lab only).

Operates on ONE setup at a time; returns a structured trade result.
A batch runner will loop this over all setups later.

Rules (levels from cached profile; fixed fallbacks if INSUFFICIENT):
  - 4 tranches 40/30/20/10 (auto-collapse if share count too small)
  - Pool $3000, ~1% risk sizing -> share count
  - Kill-all hard stop = safe_max_stop_pct (fallback ~7%)
  - Per-tranche trailing: triggers ascending (early / MFE p50 / p75 / p90),
    trail distance = MAE p50 for all (fallback ~4%); T4 runner has no target cap
  - Flag day: 1-min bars bar-by-bar; after: daily HIGH advances trails, LOW tests stops
  - Max hold = 20 trading days -> exit remainder at that day's close

Does NOT modify agent files or profiles/.
"""
from __future__ import annotations

import json
import math
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

ET = ZoneInfo("America/New_York")

POOL_USD = 3000.0
RISK_FRAC = 0.01
MAX_HOLD_TRADING_DAYS = 20

FALLBACK_KILL_PCT = 0.07
FALLBACK_TRAIL_PCT = 0.04
FALLBACK_TRIGGERS = (0.03, 0.05, 0.08, 0.10)
T1_EARLY_FRAC_OF_P50 = 0.50
T1_EARLY_FLOOR = 0.015

WEIGHTS_4 = (0.40, 0.30, 0.20, 0.10)
WEIGHTS_3 = (0.50, 0.30, 0.20)
WEIGHTS_2 = (0.60, 0.40)

HISTORY_PATH = LAB / "results" / "history.json"
BARS_DIR = LAB / "results" / "bars"
PROFILES_DIR = LAB / "profiles"
INDEX_PATH = LAB / "results" / "batch_profiles_index.json"


def size_shares(entry_price: float, kill_pct: float) -> int:
    """Share count from $3000 pool and ~1% risk to the kill stop."""
    if entry_price <= 0 or kill_pct <= 0:
        return 0
    risk_dollars = POOL_USD * RISK_FRAC
    per_share_risk = entry_price * kill_pct
    by_risk = int(math.floor(risk_dollars / per_share_risk))
    by_pool = int(math.floor(POOL_USD / entry_price))
    return max(0, min(by_risk, by_pool))


def split_tranches(n_shares: int) -> list[tuple[str, int, float]]:
    """
    Allocate integer shares across tranches.

    >=8 -> 4 (40/30/20/10); 5-7 -> 3 (50/30/20); 2-4 -> 2 (60/40); 1 -> single.
    """
    if n_shares <= 0:
        return []
    if n_shares == 1:
        return [("T1", 1, 1.0)]
    if n_shares >= 8:
        labels, weights = ["T1", "T2", "T3", "T4"], WEIGHTS_4
    elif n_shares >= 5:
        labels, weights = ["T1", "T2", "T3"], WEIGHTS_3
    else:
        labels, weights = ["T1", "T2"], WEIGHTS_2

    exact = [n_shares * w for w in weights]
    floors = [int(math.floor(x)) for x in exact]
    rem = n_shares - sum(floors)
    order = sorted(
        range(len(weights)),
        key=lambda i: (exact[i] - floors[i], -i),
        reverse=True,
    )
    for k in range(rem):
        floors[order[k % len(order)]] += 1

    out: list[tuple[str, int, float]] = []
    for lab, sh, w in zip(labels, floors, weights):
        if sh > 0:
            out.append((lab, sh, float(w)))
    return out


def extract_levels(profile: dict[str, Any]) -> dict[str, Any]:
    """Kill / trail / trigger levels from profile, with INSUFFICIENT fallbacks."""
    conf = str(profile.get("confidence") or "INSUFFICIENT").upper()
    meaningful = bool(profile.get("stats_meaningful", conf != "INSUFFICIENT"))
    insufficient = conf == "INSUFFICIENT" or not meaningful

    bracket = profile.get("bracket") or {}
    pct = profile.get("percentiles") or {}
    mae = pct.get("mae") or {}
    mfe = pct.get("mfe") or {}

    if insufficient:
        kill = FALLBACK_KILL_PCT
        trail = FALLBACK_TRAIL_PCT
        triggers_4 = list(FALLBACK_TRIGGERS)
        source = "fallback_insufficient"
    else:
        kill = float(bracket.get("safe_max_stop_pct") or FALLBACK_KILL_PCT)
        trail = float(mae.get("p50") or FALLBACK_TRAIL_PCT)
        p50 = float(mfe.get("p50") or FALLBACK_TRIGGERS[1])
        p75 = float(mfe.get("p75") or FALLBACK_TRIGGERS[2])
        p90 = float(mfe.get("p90") or FALLBACK_TRIGGERS[3])
        early = max(T1_EARLY_FLOOR, p50 * T1_EARLY_FRAC_OF_P50)
        t1 = early
        t2 = max(p50, t1 + 1e-6)
        t3 = max(p75, t2 + 1e-6)
        t4 = max(p90, t3 + 1e-6)
        triggers_4 = [t1, t2, t3, t4]
        source = "profile"

    return {
        "confidence": conf,
        "insufficient": insufficient,
        "kill_pct": kill,
        "trail_pct": trail,
        "triggers_4": triggers_4,
        "source": source,
    }


def triggers_for_n(triggers_4: list[float], n_tranches: int) -> list[float]:
    """Map canonical 4 triggers onto live tranche count."""
    t1, t2, _t3, t4 = triggers_4
    if n_tranches >= 4:
        return list(triggers_4)
    if n_tranches == 3:
        return [t1, t2, t4]
    if n_tranches == 2:
        return [t1, t4]
    return [t1]


@dataclass
class TrancheState:
    id: str
    shares: int
    weight: float
    trigger_pct: float
    trigger_price: float
    trail_pct: float
    trailing: bool = False  # activated: this tranche's trigger has been hit
    run_high: float = 0.0   # max HIGH seen SINCE activation only (0 until armed)
    activated_at: str | None = None
    activation_high: float | None = None
    closed: bool = False
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None  # trail | kill | time_cap
    trail_stop_at_exit: float | None = None


@dataclass
class SimState:
    entry_price: float
    kill_price: float
    kill_pct: float
    trail_pct: float
    tranches: list[TrancheState] = field(default_factory=list)
    peak_high: float = 0.0
    trading_day: int = 1
    last_bar_time: str | None = None
    last_close: float | None = None


def _trail_stop(t: TrancheState) -> float:
    """This tranche's own stop from its own post-activation running high."""
    return t.run_high * (1.0 - t.trail_pct)


def _close_tranche(t: TrancheState, price: float, when: str, reason: str) -> None:
    if t.closed:
        return
    t.closed = True
    t.exit_price = float(price)
    t.exit_time = when
    t.exit_reason = reason
    if reason == "trail" and t.run_high > 0:
        t.trail_stop_at_exit = _trail_stop(t)


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

    Each tranche has fully independent trailing state:
      - activates only when HIGH >= ITS trigger
      - run_high = max HIGH seen since THAT activation (not shared, not pre-trigger)
      - trail stop = ITS run_high * (1 - trail_pct)
      - no trail-exit on the activation bar (never exits below trigger same-bar)

    Intrabar order for longs:
      1) Kill on LOW -> sell ALL remaining at kill_price
      2) For each open tranche: activate and/or advance ITS own run_high
      3) Test already-active trail stops on LOW (skip newly activated this bar)
      4) Optional time-cap exit at CLOSE
    """
    if _all_closed(state):
        return

    state.last_bar_time = when
    state.last_close = close
    state.peak_high = max(state.peak_high, high)

    # 1) Kill-all (shared hard stop — only shared state by design)
    if low <= state.kill_price:
        _close_all(state, state.kill_price, when, "kill")
        return

    # 2) Per-tranche activate / advance OWN run_high (no pre-activation tracking)
    newly_armed: set[str] = set()
    for t in state.tranches:
        if t.closed:
            continue
        if not t.trailing:
            if high >= t.trigger_price:
                t.trailing = True
                # Start THIS tranche's running high at this bar's high only.
                t.run_high = float(high)
                t.activated_at = when
                t.activation_high = float(high)
                newly_armed.add(t.id)
        else:
            if high > t.run_high:
                t.run_high = float(high)

    # 3) Independent trail exits (skip newly activated this bar).
    #    Also skip while this tranche's trail stop is still below its trigger
    #    (not enough post-activation run yet) — never trail-exit below trigger.
    for t in state.tranches:
        if t.closed or not t.trailing or t.id in newly_armed:
            continue
        stop = _trail_stop(t)
        if stop < t.trigger_price:
            continue
        if low <= stop:
            _close_tranche(t, stop, when, "trail")

    if _all_closed(state):
        return

    # 4) Time cap
    if force_time_cap:
        _close_all(state, close, when, "time_cap")


def run_strategy_a(
    *,
    ticker: str,
    flag_date: str,
    entry_price: float,
    entry_time: str,
    minute_bars: list[dict],
    daily_bars: list[dict],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Simulate Strategy A on one setup. Returns a structured trade result."""
    levels = extract_levels(profile)
    kill_pct = levels["kill_pct"]
    trail_pct = levels["trail_pct"]
    n_shares = size_shares(entry_price, kill_pct)
    alloc = split_tranches(n_shares)
    trigs = triggers_for_n(levels["triggers_4"], len(alloc))

    tranches: list[TrancheState] = []
    for (tid, sh, w), trig in zip(alloc, trigs):
        tranches.append(
            TrancheState(
                id=tid,
                shares=sh,
                weight=w,
                trigger_pct=float(trig),
                trigger_price=entry_price * (1.0 + float(trig)),
                trail_pct=trail_pct,
                run_high=0.0,  # unused until this tranche activates
            )
        )

    state = SimState(
        entry_price=entry_price,
        kill_price=entry_price * (1.0 - kill_pct),
        kill_pct=kill_pct,
        trail_pct=trail_pct,
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

    if not _all_closed(state) and state.last_close is not None:
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
        "strategy": "A_trailing",
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
    state: SimState,
    n_shares: int,
) -> dict[str, Any]:
    notional = n_shares * entry_price
    pnl = 0.0
    tranche_rows: list[dict[str, Any]] = []
    last_exit_day = flag_date

    for t in state.tranches:
        px = float(t.exit_price if t.exit_price is not None else entry_price)
        pnl += t.shares * (px - entry_price)
        ret_i = (px - entry_price) / entry_price if entry_price else 0.0
        exit_day = (t.exit_time or flag_date)[:10]
        if exit_day > last_exit_day:
            last_exit_day = exit_day
        tranche_rows.append({
            "id": t.id,
            "shares": t.shares,
            "weight": t.weight,
            "trigger_pct": round(t.trigger_pct, 6),
            "trigger_price": round(t.trigger_price, 4),
            "trail_pct": round(t.trail_pct, 6),
            "armed": t.trailing or t.exit_reason == "trail",
            "activated_at": t.activated_at,
            "activation_high": (
                None if t.activation_high is None
                else round(t.activation_high, 4)
            ),
            "run_high": round(t.run_high, 4) if t.run_high else None,
            "trail_stop_at_exit": (
                None if t.trail_stop_at_exit is None
                else round(t.trail_stop_at_exit, 4)
            ),
            "exit_price": round(px, 4),
            "exit_time": t.exit_time,
            "exit_reason": t.exit_reason,
            "return_pct": round(ret_i * 100, 4),
            "pnl_usd": round(t.shares * (px - entry_price), 4),
        })

    total_ret = (pnl / notional) if notional else 0.0
    mfe = (state.peak_high - entry_price) / entry_price if entry_price else 0.0
    days_held = _trading_days_inclusive(flag_date, last_exit_day)
    reasons = [r["exit_reason"] for r in tranche_rows]

    return {
        "strategy": "A_trailing",
        "ticker": ticker,
        "flag_date": flag_date,
        "entry_price": round(entry_price, 4),
        "entry_time": entry_time,
        "status": "ok",
        "levels": {
            **levels,
            "kill_pct": round(levels["kill_pct"], 6),
            "trail_pct": round(levels["trail_pct"], 6),
            "triggers_4": [round(x, 6) for x in levels["triggers_4"]],
            "kill_price": round(state.kill_price, 4),
        },
        "n_shares": n_shares,
        "notional_usd": round(notional, 2),
        "pool_usd": POOL_USD,
        "risk_frac": RISK_FRAC,
        "tranches": tranche_rows,
        "total_pnl_usd": round(pnl, 4),
        "total_return_pct": round(total_ret * 100, 4),
        "mfe_pct": round(mfe * 100, 4),
        "days_held": days_held,
        "trading_day_at_end": state.trading_day,
        "exit_reason_counts": {
            "trail": reasons.count("trail"),
            "kill": reasons.count("kill"),
            "time_cap": reasons.count("time_cap"),
        },
    }


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _trading_days_inclusive(start: str, end: str) -> int:
    try:
        a = date.fromisoformat(start[:10])
        b = date.fromisoformat(end[:10])
    except ValueError:
        return 0
    if b < a:
        return 0
    n = 0
    cur = a
    while cur <= b:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def load_minute_bars(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    data = json.loads(p.read_text(encoding="utf-8"))
    return list(data.get("bars") or [])


def load_profile(ticker: str, flag_date: str) -> dict[str, Any]:
    path = PROFILES_DIR / f"{ticker.upper()}_{flag_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing lab profile: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_daily_after(
    ticker: str,
    flag_date: str,
    *,
    calendar_span: int = 45,
) -> list[dict]:
    """Daily OHLCV from flag_date forward (sim skips flag day itself)."""
    from entry_study import load_polygon_key
    from fetch_history import fetch_daily_bars

    start = date.fromisoformat(flag_date[:10])
    end = start + timedelta(days=calendar_span)
    key = load_polygon_key()
    raw = fetch_daily_bars(
        ticker.upper(), start.isoformat(), end.isoformat(), key,
    )
    out: list[dict] = []
    for b in raw:
        ts = int(b["t"])
        dstr = datetime.fromtimestamp(ts / 1000.0, tz=ET).date().isoformat()
        out.append({
            "date": dstr,
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": float(b.get("v") or 0),
        })
    return out


def load_setup_and_run(ticker: str, flag_date: str) -> dict[str, Any]:
    """Wire history.json + bars + profile + daily fetch -> run_strategy_a."""
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

    return run_strategy_a(
        ticker=ticker.upper(),
        flag_date=flag_date,
        entry_price=entry_price,
        entry_time=entry_time,
        minute_bars=minute_bars,
        daily_bars=daily_bars,
        profile=profile,
    )


def pick_high_confidence_joby() -> tuple[str, str]:
    """Prefer a HIGH JOBY day with room for trails to fire (not an instant kill)."""
    preferred = [
        ("JOBY", "2024-11-11"),
        ("JOBY", "2024-10-23"),
        ("JOBY", "2024-11-25"),
        ("JOBY", "2025-05-28"),
        ("JOBY", "2025-08-15"),
    ]
    for t, d in preferred:
        prof = PROFILES_DIR / f"{t}_{d}.json"
        if not prof.exists():
            continue
        p = json.loads(prof.read_text(encoding="utf-8"))
        if str(p.get("confidence")).upper() == "HIGH":
            return t, d

    if INDEX_PATH.exists():
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        for key, row in idx.items():
            if key.startswith("JOBY|") and str(row.get("confidence")).upper() == "HIGH":
                return "JOBY", key.split("|", 1)[1]
    raise RuntimeError("No HIGH-confidence JOBY setup found in lab profiles")


def _print_result(res: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"STRATEGY A (Trailing) — {res['ticker']}  flag={res['flag_date']}")
    print("=" * 72)
    print(f"  Entry:     ${res['entry_price']:.4f}  @ {res['entry_time']}")
    print(f"  Shares:    {res['n_shares']}  notional=${res.get('notional_usd')}")
    lv = res.get("levels") or {}
    print(
        f"  Levels:    kill={lv.get('kill_pct')}  trail={lv.get('trail_pct')}  "
        f"source={lv.get('source')}  conf={lv.get('confidence')}"
    )
    print(f"  Kill px:   ${lv.get('kill_price')}")
    print(f"  Triggers4: {lv.get('triggers_4')}")
    print("-" * 72)
    print("  Per-tranche (independent activation / run-high / trail stop):")
    for t in res.get("tranches") or []:
        print(
            f"  {t['id']}: sh={t['shares']}  "
            f"trig=${t['trigger_price']:.4f} ({t['trigger_pct']*100:.2f}%)"
        )
        print(
            f"       activated={t.get('activated_at')}  "
            f"act_high={t.get('activation_high')}  "
            f"run_high={t.get('run_high')}  "
            f"trail_stop={t.get('trail_stop_at_exit')}"
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
    # Fixed self-test setup so trailing independence is easy to eyeball.
    ticker, flag_date = "JOBY", "2024-11-11"
    print(
        f"Self-test: Strategy A on ONE HIGH-confidence setup "
        f"-> {ticker}|{flag_date}\n"
    )
    res = load_setup_and_run(ticker, flag_date)
    _print_result(res)


if __name__ == "__main__":
    main()
