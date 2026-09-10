"""
Multi-day Peak Hour theoretical replay (live sizing + 4-tranche trail).

Monday last week → today (weekdays, NYSE holidays skipped).
Uses SHARE_LOT=4, slot_ladder, Attention→Case→ENTER, strategy_a trails.
Positions carry overnight; residual marked at range EOD.

Usage:
  py -3 candidates/tsd_scan_pipeline/php_range_replay.py
  py -3 candidates/tsd_scan_pipeline/php_range_replay.py --start 2026-08-31 --end 2026-09-10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT_DIR = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(ROOT_DIR / ".env")
_load_env_file(CANDIDATES_DIR / ".env")

import tsd_scan_pipeline.tsd_1h_signal as t1h_signal
from tsd_scan_pipeline.tsd_1h_launch_scan import evaluate_1h_symbol, rank_1h_launches
from tsd_scan_pipeline.tsd_attention import build_attention_pool
from tsd_scan_pipeline.tsd_capacity import (
    MAX_NEW_ENTRIES_PER_SCAN,
    SHARE_LOT,
    shares_for_budget,
    slot_ladder,
)
from tsd_scan_pipeline.tsd_case_review import review_attention_pool, select_enter_rows
from tsd_scan_pipeline.tsd_htf_universe import htf_pass_symbols, load_htf_universe
from tsd_scan_pipeline.tsd_kill import resolve_kill_pct
from tsd_scan_pipeline.tsd_popularity import build_popularity_context
from tsd_scan_pipeline.tsd_social import attach_social_to_rows
from tsd_scan_pipeline.tsd_trail import (
    evaluate_trail_tick,
    init_trail_state,
    is_t4_only,
    maybe_roll_trading_day,
    remaining_shares,
)
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
COST_PER_TRADE = 0.0015
# NYSE full closures in this window
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-07-03", "2026-09-07",  # Labor Day
    "2026-11-26", "2026-12-25",
}


def _as_et(date_str: str, hour: int, minute: int = 15) -> datetime:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return ET.localize(datetime(d.year, d.month, d.day, hour, minute, 0))


def _monday_last_week(today: date | None = None) -> date:
    t = today or datetime.now(ET).date()
    return t - timedelta(days=t.weekday() + 7)


def trading_days(start: date, end: date) -> list[str]:
    out: list[str] = []
    d = start
    while d <= end:
        ds = d.isoformat()
        if d.weekday() < 5 and ds not in NYSE_HOLIDAYS:
            out.append(ds)
        d += timedelta(days=1)
    return out


def _daily_close(symbol: str, date_str: str, api_key: str) -> float | None:
    url = f"{POLYGON_BASE}/v1/open-close/{symbol.upper()}/{date_str}"
    try:
        data = polygon_get(url, {"adjusted": "true"}, api_key, timeout=30)
        time.sleep(0.12)
        c = data.get("close")
        return float(c) if c is not None else None
    except Exception:
        return None


def _bar_ohlc_as_of(
    bars: pd.DataFrame | None,
    date_str: str,
    close_hour: int,
) -> tuple[float, float, float] | None:
    """OHLC for the 1H bar whose close hour is close_hour on date_str."""
    if bars is None or len(bars) == 0:
        return None
    for ts, row in bars.iterrows():
        start = pd.Timestamp(ts)
        if start.tzinfo is None:
            start = start.tz_localize(ET)
        else:
            start = start.tz_convert(ET)
        if start.date().isoformat() != date_str:
            continue
        if (int(start.hour) + 1) % 24 != close_hour:
            continue
        h = float(row.get("high") or row.get("h") or 0)
        l = float(row.get("low") or row.get("l") or 0)
        c = float(row.get("close") or row.get("c") or 0)
        if h > 0 and l > 0 and c > 0:
            return h, l, c
    return None


def _last_px(
    bars: pd.DataFrame | None,
    date_str: str,
    as_of_hour: int,
    fallback: float,
) -> float:
    best = fallback
    if bars is None or len(bars) == 0:
        return best
    for ts, row in bars.iterrows():
        start = pd.Timestamp(ts)
        if start.tzinfo is None:
            start = start.tz_localize(ET)
        else:
            start = start.tz_convert(ET)
        if start.date().isoformat() > date_str:
            break
        ch = (int(start.hour) + 1) % 24
        if start.date().isoformat() == date_str and ch > as_of_hour:
            break
        c = float(row.get("close") or row.get("c") or 0)
        if c > 0:
            best = c
    return best


def _full_slot_count(open_pos: dict[str, dict[str, Any]]) -> int:
    n = 0
    for pos in open_pos.values():
        trail = pos.get("trail") or {}
        if is_t4_only(trail):
            continue
        n += 1
    return n


def _mark_equity(
    cash: float,
    open_pos: dict[str, dict[str, Any]],
    bar_cache: dict[str, Any],
    date_str: str,
    hour: int,
) -> float:
    mtm = 0.0
    for sym, pos in open_pos.items():
        rem = remaining_shares(pos["trail"])
        if rem <= 0:
            continue
        px = _last_px(bar_cache.get(sym), date_str, hour, float(pos["entry_price"]))
        mtm += rem * px
    return cash + mtm


def _book_partial_exits(
    pos: dict[str, Any],
    exits: list[dict[str, Any]],
) -> float:
    """Cash proceeds from tranche exits; accumulate realized vs entry."""
    proceeds = 0.0
    entry = float(pos["entry_price"])
    for ex in exits:
        sh = int(ex["shares"])
        px = float(ex["exit_price"])
        proceeds += px * sh
        pos.setdefault("realized_gross", 0.0)
        pos["realized_gross"] += (px - entry) * sh
        pos.setdefault("tranche_exits", []).append(ex)
    return proceeds


def _close_position(
    pos: dict[str, Any],
    *,
    status: str,
    exit_reason: str,
) -> dict[str, Any]:
    entry = float(pos["entry_price"])
    shares = int(pos["shares"])
    cost = entry * shares * COST_PER_TRADE
    realized_gross = float(pos.get("realized_gross") or 0.0)
    # Any leftover marked already booked into realized_gross by caller
    pnl = realized_gross - cost
    return {
        **{k: v for k, v in pos.items() if k not in ("trail", "tsd_profile")},
        "status": status,
        "exit_reason": exit_reason,
        "pnl": round(pnl, 2),
        "tranche_exits": list(pos.get("tranche_exits") or []),
        "tranche_init": list(pos.get("tranche_init") or []),
    }


def run_range(
    *,
    start: str,
    end: str,
    starting_cash: float = 3000.0,
    allow_llm: bool = True,
    htf_day: str | None = None,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    key = load_polygon_key()
    start_d = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    days = trading_days(start_d, end_d)
    if not days:
        raise SystemExit(f"No trading days in {start}..{end}")

    print("=" * 72)
    print(f"PHP RANGE REPLAY  {days[0]} -> {days[-1]}  ({len(days)} sessions)")
    print(f"cash=${starting_cash:.0f}  SHARE_LOT={SHARE_LOT}  live trail  NO TWS fills")
    print("=" * 72)

    # HTF: prefer end-date cache, else any available (documented)
    htf_stamp = (htf_day or days[-1]).replace("-", "")
    htf_doc = load_htf_universe(day=htf_stamp)
    if not htf_doc:
        for d in reversed(days):
            htf_doc = load_htf_universe(day=d.replace("-", ""))
            if htf_doc:
                htf_stamp = d.replace("-", "")
                break
    if not htf_doc:
        raise SystemExit("No HTF cache found — need results/htf_universe/htf_pass_*.json")
    htf_rows = {str(r["symbol"]).upper(): r for r in (htf_doc.get("rows") or [])}
    symbols = htf_pass_symbols(day=htf_stamp) or [
        str(s).upper() for s in (htf_doc.get("symbols") or [])
    ]
    if max_symbols:
        symbols = symbols[:max_symbols]
    print(f"HTF universe: {len(symbols)} names from htf_pass_{htf_stamp}.json")
    print("  NOTE: single HTF snapshot for whole range (1H signals still as-of each hour).")

    bar_cache: dict[str, Any] = {}
    eod_cache: dict[str, dict[str, float | None]] = {}
    catalyst_cache: dict[str, dict[str, Any]] = {}
    case_reject_cache: dict[str, dict[str, Any]] = {}

    _orig_bars = t1h_signal._bars_1h_polygon
    _orig_sleep = time.sleep
    skip_sleep = {"on": False}

    def _cached_bars(symbol: str, api_key: str | None = None) -> pd.DataFrame:
        sym = str(symbol).upper()
        if sym not in bar_cache:
            bar_cache[sym] = _orig_bars(sym, api_key=api_key)
            _orig_sleep(0.12)
        return bar_cache[sym]

    def _smart_sleep(sec: float) -> None:
        if skip_sleep["on"]:
            return
        _orig_sleep(sec)

    t1h_signal._bars_1h_polygon = _cached_bars  # type: ignore[assignment]
    time.sleep = _smart_sleep  # type: ignore[assignment]

    print(f"Prefetching 1H bars ({len(symbols)})...")
    for i, sym in enumerate(symbols, 1):
        try:
            _cached_bars(sym, api_key=key)
        except Exception as exc:
            print(f"  bar miss {sym}: {exc}")
            bar_cache[sym] = pd.DataFrame()
        if i % 25 == 0:
            print(f"  bars {i}/{len(symbols)}")
    print(f"  bar cache: {sum(1 for v in bar_cache.values() if v is not None and len(v))} ok")

    cash = float(starting_cash)
    open_pos: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    day_logs: list[dict[str, Any]] = []
    skipped_expensive = 0

    try:
        for di, date_str in enumerate(days):
            print(f"\n{'=' * 72}\nDAY {date_str} ({di + 1}/{len(days)})\n{'=' * 72}")
            # Roll trading-day counter for overnight holds
            for pos in open_pos.values():
                pos["trail"] = maybe_roll_trading_day(
                    pos["trail"],
                    today=datetime.strptime(date_str, "%Y-%m-%d").date(),
                )

            day_taken: list[str] = []
            hour_logs: list[dict[str, Any]] = []

            for hour in range(5, 16):
                now = _as_et(date_str, hour, 15)

                # --- Advance open trails on this completed hour ---
                for sym in list(open_pos.keys()):
                    pos = open_pos[sym]
                    ohlc = _bar_ohlc_as_of(bar_cache.get(sym), date_str, hour)
                    if not ohlc:
                        continue
                    # Skip bars at/before entry on entry day
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
                        print(
                            f"  EXIT {sym} @{hour}: "
                            + ",".join(f"{e['tranche_id']}@{e['exit_price']:.2f}" for e in exits)
                        )
                    if remaining_shares(pos["trail"]) <= 0:
                        trade = _close_position(pos, status="CLOSED", exit_reason="flat")
                        trade["exit_date"] = date_str
                        trade["exit_hour"] = hour
                        closed.append(trade)
                        print(f"  FLAT {sym} pnl=${trade['pnl']:+.2f}")
                        del open_pos[sym]

                equity = _mark_equity(cash, open_pos, bar_cache, date_str, hour)
                n_cap, unit_s = slot_ladder(equity)
                slots_used = _full_slot_count(open_pos)
                if slots_used >= n_cap:
                    hour_logs.append({
                        "hour": hour, "skipped": "slots_full",
                        "slots_used": slots_used, "n_cap": n_cap, "equity": round(equity, 2),
                    })
                    continue

                skip_sleep["on"] = False
                popularity = build_popularity_context(
                    api_key=key, as_of=now, include_tws=False,
                )

                rows: list[dict[str, Any]] = []
                skip_sleep["on"] = True
                for i, sym in enumerate(symbols, 1):
                    rows.append(evaluate_1h_symbol(
                        sym, htf_row=htf_rows.get(sym), polygon_key=key, now=now,
                    ))
                    if i % 50 == 0:
                        print(f"  {date_str} {hour:02d}: scanned {i}/{len(symbols)}")
                skip_sleep["on"] = False

                ranked = rank_1h_launches(
                    rows, polygon_key=key, now=now, attach_social=False,
                )
                if ranked:
                    try:
                        ranked = attach_social_to_rows(
                            ranked, api_key=key, as_of=now,
                            include_x=False, include_st=True, include_tws=False,
                        )
                    except Exception as exc:
                        print(f"  social warn: {exc}")
                # Block re-entry while any residual (incl t4_only) still open
                ranked = [
                    r for r in ranked
                    if str(r.get("symbol") or "").upper() not in open_pos
                ]
                attention_raw = build_attention_pool(
                    ranked, polygon_key=key, popularity_ctx=popularity, include_tws=False,
                )
                for r in attention_raw:
                    sym = str(r.get("symbol") or "").upper()
                    if sym in catalyst_cache:
                        for k, v in catalyst_cache[sym].items():
                            if r.get(k) in (None, "", 0, {}):
                                r[k] = v
                    prior = case_reject_cache.get(sym)
                    if prior and str(prior.get("verdict") or "").upper() == "REJECT":
                        # Re-REJECT only same calendar day to avoid permanent blacklist
                        if prior.get("_reject_day") == date_str:
                            r["case_review"] = prior

                reviewed = review_attention_pool(
                    attention_raw,
                    use_web_search=False,
                    allow_llm=allow_llm,
                    enrich_catalyst=True,
                    polygon_key=key,
                )
                for r in reviewed:
                    sym = str(r.get("symbol") or "").upper()
                    catalyst_cache[sym] = {
                        k: r.get(k)
                        for k in (
                            "print", "outlook", "deep_summary_line", "deep_narrative",
                            "deep_catalyst", "dilution_flag", "distress_flag",
                            "guidance_cut", "news_summary", "catalyst_tier", "fresh_catalyst",
                        )
                        if r.get(k) is not None
                    }
                    cr = r.get("case_review")
                    if cr and str(cr.get("verdict") or "").upper() == "REJECT":
                        cr = dict(cr)
                        cr["_reject_day"] = date_str
                        case_reject_cache[sym] = cr

                slots_left = n_cap - _full_slot_count(open_pos)
                take_n = min(MAX_NEW_ENTRIES_PER_SCAN, slots_left)
                take_pool = select_enter_rows(reviewed, max_n=max(take_n * 5, 10))

                taken_this_hour: list[str] = []
                for cand in take_pool:
                    if len(taken_this_hour) >= take_n:
                        break
                    sym = str(cand.get("symbol") or "").upper()
                    if sym in open_pos:
                        continue
                    px = float(cand.get("htf_1h_close") or cand.get("close") or 0)
                    if px <= 0:
                        continue
                    equity = _mark_equity(cash, open_pos, bar_cache, date_str, hour)
                    _, unit_s = slot_ladder(equity)
                    budget = min(unit_s, cash)
                    shares = shares_for_budget(budget, px)
                    if shares <= 0:
                        skipped_expensive += 1
                        print(
                            f"  SKIP {sym}: lot={SHARE_LOT} budget=${budget:.0f} "
                            f"px={px:.2f} (max~${budget / SHARE_LOT:.2f})"
                        )
                        continue
                    kill_pct, kill_src = resolve_kill_pct(
                        cand.get("kill_pct"), profile=cand.get("tsd_profile"),
                    )
                    trail = init_trail_state(
                        entry_price=px,
                        n_shares=shares,
                        profile=cand.get("tsd_profile") or {},
                    )
                    t_ids = [t.get("id") for t in (trail.get("tranches") or [])]
                    case = cand.get("case_review") or {}
                    cash -= px * shares
                    open_pos[sym] = {
                        "symbol": sym,
                        "entry_date": date_str,
                        "entry_hour": hour,
                        "entry_price": round(px, 4),
                        "shares": shares,
                        "kill_pct": kill_pct,
                        "kill_source": kill_src,
                        "kill_price": round(px * (1.0 - kill_pct), 4),
                        "continuation_score": cand.get("continuation_score"),
                        "case_verdict": case.get("verdict"),
                        "case_confidence": case.get("confidence"),
                        "case_source": case.get("source"),
                        "structure_note": case.get("structure_note"),
                        "sentiment_note": case.get("sentiment_note"),
                        "room_class": case.get("room_class"),
                        "attention_reasons": cand.get("attention_reasons"),
                        "tradable_popular": cand.get("tradable_popular"),
                        "notional": round(px * shares, 2),
                        "sizing_note": f"SHARE_LOT={SHARE_LOT}",
                        "tranche_ids": t_ids,
                        "tranche_init": [
                            {
                                "id": t.get("id"),
                                "shares": t.get("shares"),
                                "trigger_pct": t.get("trigger_pct"),
                                "trigger_price": round(float(t.get("trigger_price") or 0), 4),
                            }
                            for t in (trail.get("tranches") or [])
                        ],
                        "trail": trail,
                        "realized_gross": 0.0,
                        "tranche_exits": [],
                        "tsd_profile": cand.get("tsd_profile") or {},
                    }
                    taken_this_hour.append(sym)
                    day_taken.append(sym)
                    print(
                        f"  TAKE {sym} {shares}sh @ {px:.2f} "
                        f"tranches={'+'.join(str(x) for x in t_ids)} "
                        f"case={case.get('verdict')}/{case.get('source')} "
                        f"why={','.join(cand.get('attention_reasons') or [])}"
                    )

                hour_logs.append({
                    "hour": hour,
                    "ranked": len(ranked),
                    "attention": len(reviewed),
                    "taken": taken_this_hour,
                    "open": list(open_pos.keys()),
                    "slots_used": _full_slot_count(open_pos),
                    "n_cap": n_cap,
                    "equity": round(_mark_equity(cash, open_pos, bar_cache, date_str, hour), 2),
                    "cash": round(cash, 2),
                })

            # Day EOD mark (reporting only; positions carry)
            eod_equity = _mark_equity(cash, open_pos, bar_cache, date_str, 16)
            day_logs.append({
                "date": date_str,
                "taken": day_taken,
                "open_eod": list(open_pos.keys()),
                "closed_so_far": len(closed),
                "cash": round(cash, 2),
                "equity_eod": round(eod_equity, 2),
                "hour_logs": hour_logs,
            })
            print(
                f"  DAY EOD equity=${eod_equity:,.2f} cash=${cash:,.2f} "
                f"open={list(open_pos.keys())} taken_today={day_taken}"
            )

        # Range end: mark remaining at last day's official close
        print("\n--- RANGE EOD FLATTEN ---")
        last_day = days[-1]
        for sym, pos in list(open_pos.items()):
            rem = remaining_shares(pos["trail"])
            if rem <= 0:
                del open_pos[sym]
                continue
            if sym not in eod_cache.setdefault(last_day, {}):
                eod_cache[last_day][sym] = _daily_close(sym, last_day, key)
            mark = eod_cache[last_day].get(sym) or float(pos["entry_price"])
            cash += mark * rem
            pos["realized_gross"] = float(pos.get("realized_gross") or 0.0) + (
                mark - float(pos["entry_price"])
            ) * rem
            pos.setdefault("tranche_exits", []).append({
                "tranche_id": "REMAINING",
                "shares": rem,
                "exit_price": round(mark, 4),
                "reason": "range_eod_mark",
                "hour": 16,
                "date": last_day,
            })
            trade = _close_position(pos, status="OPEN_EOD", exit_reason="range_eod_mark")
            trade["exit_date"] = last_day
            trade["exit_hour"] = 16
            closed.append(trade)
            print(f"  {sym}: mark {rem}sh @ {mark} pnl=${trade['pnl']:+.2f}")
            del open_pos[sym]
    finally:
        t1h_signal._bars_1h_polygon = _orig_bars  # type: ignore[assignment]
        time.sleep = _orig_sleep  # type: ignore[assignment]

    realized = sum(float(t["pnl"]) for t in closed)
    end_equity = starting_cash + realized
    report = {
        "start": days[0],
        "end": days[-1],
        "sessions": days,
        "htf_snapshot": htf_stamp,
        "starting_cash": starting_cash,
        "ending_cash": round(cash, 2),
        "ending_equity": round(end_equity, 2),
        "realized_pnl": round(realized, 2),
        "skipped_expensive": skipped_expensive,
        "trades": closed,
        "day_logs": [
            {k: v for k, v in d.items() if k != "hour_logs"} | {
                "hours_summary": [
                    {
                        "hour": h.get("hour"),
                        "taken": h.get("taken"),
                        "skipped": h.get("skipped"),
                        "equity": h.get("equity"),
                    }
                    for h in (d.get("hour_logs") or [])
                ]
            }
            for d in day_logs
        ],
        "runtime_sec": round(time.time() - t0, 1),
        "notes": [
            "Live rules: SHARE_LOT=4, slot_ladder, max 2 entries/hour, case ENTER-only",
            "4-tranche strategy_a trail; overnight carry; range-end mark on leftovers",
            "COST_PER_TRADE=0.0015 RT on original entry notional",
            f"HTF snapshot htf_pass_{htf_stamp}.json for whole range (signals still as-of)",
            "TWS scanners off; LLM case on (no web_search)",
        ],
    }
    out = PIPELINE_DIR / "results" / f"php_range_replay_{days[0].replace('-', '')}_{days[-1].replace('-', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"SESSIONS: {len(days)} ({days[0]} .. {days[-1]})")
    print(f"TRADES:   {len(closed)}")
    print(f"P&L:      ${realized:+,.2f}")
    print(f"Equity:   ${starting_cash:,.2f} -> ${end_equity:,.2f}")
    print(f"Skipped expensive (lot4): {skipped_expensive}")
    print(f"Wrote {out}")
    print(f"Runtime {report['runtime_sec']}s")
    print("=" * 72)
    return report


def main() -> int:
    today = datetime.now(ET).date()
    start_default = _monday_last_week(today).isoformat()
    p = argparse.ArgumentParser(description="Peak Hour multi-day live-rules replay")
    p.add_argument("--start", default=start_default)
    p.add_argument("--end", default=today.isoformat())
    p.add_argument("--cash", type=float, default=3000.0)
    p.add_argument("--htf-day", default=None, help="YYYY-MM-DD HTF cache to use")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--max-symbols", type=int, default=None)
    args = p.parse_args()
    run_range(
        start=args.start,
        end=args.end,
        starting_cash=args.cash,
        allow_llm=not args.no_llm,
        htf_day=args.htf_day,
        max_symbols=args.max_symbols,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
