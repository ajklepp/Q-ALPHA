"""
Theoretical Peak Hour day replay (Polygon + case model, no TWS fills).

Replays hours 05–15 ET on a session date with:
  - $3000 start, 10-slot ladder ($300/slot at full N)
  - Attention + Case Review (same live path)
  - Kill-stop then EOD mark for open leftovers

Usage:
  py -3 candidates/tsd_scan_pipeline/php_day_replay.py --date 2026-09-09
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

import pandas as pd
import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT_DIR = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))

# Python 3.14 / ib_insync: ensure an event loop exists before import
import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _load_env_file(path: Path) -> None:
    """Load KEY=VAL into os.environ without python-dotenv."""
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
    remaining_shares,
)
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
COST_PER_TRADE = 0.0015  # round-trip fraction of notional (repo standard)
# Slot must fit at least SHARE_LOT shares → max entry price = S / 4 ($75 at $300)
# Full T1–T4 weights need >=8 shares (else trail collapses to 2–3 legs).


def _as_et_day(date_str: str, hour: int, minute: int = 15) -> datetime:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return ET.localize(datetime(d.year, d.month, d.day, hour, minute, 0))


def _daily_close(symbol: str, date_str: str, api_key: str) -> float | None:
    url = f"{POLYGON_BASE}/v1/open-close/{symbol.upper()}/{date_str}"
    try:
        data = polygon_get(url, {"adjusted": "true"}, api_key, timeout=30)
        time.sleep(0.12)
        c = data.get("close")
        return float(c) if c is not None else None
    except Exception:
        return None


def _simulate_path(
    *,
    symbol: str,
    entry: float,
    shares: int,
    entry_hour: int,
    date_str: str,
    bars_1h: pd.DataFrame | None,
    eod_close: float | None,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Live-shaped exit: strategy_a 4-tranche trail on post-entry 1H bars, then EOD mark.

    Kill / T1–T4 triggers / trails come from init_trail_state (same as broker trail).
    """
    notional = entry * shares
    cost = notional * COST_PER_TRADE
    trail = init_trail_state(entry_price=entry, n_shares=shares, profile=profile or {})
    tranche_init = [
        {
            "id": t.get("id"),
            "shares": t.get("shares"),
            "trigger_pct": t.get("trigger_pct"),
            "trigger_price": round(float(t.get("trigger_price") or 0), 4),
        }
        for t in (trail.get("tranches") or [])
    ]
    realized = 0.0
    exits_log: list[dict[str, Any]] = []
    last_hour = entry_hour

    if bars_1h is not None and len(bars_1h) > 0:
        for ts, row in bars_1h.iterrows():
            start = pd.Timestamp(ts)
            if start.tzinfo is None:
                start = start.tz_localize(ET)
            else:
                start = start.tz_convert(ET)
            close_hour = (int(start.hour) + 1) % 24
            if start.date().isoformat() != date_str:
                continue
            if close_hour <= entry_hour:
                continue
            high = float(row.get("high") or row.get("h") or 0)
            low = float(row.get("low") or row.get("l") or 0)
            close = float(row.get("close") or row.get("c") or 0)
            if high <= 0 or low <= 0 or close <= 0:
                continue
            when = f"{date_str}T{close_hour:02d}:00:00"
            trail, new_exits = evaluate_trail_tick(
                trail, high=high, low=low, close=close, when=when,
            )
            last_hour = close_hour
            for ex in new_exits:
                px = float(ex["exit_price"])
                sh = int(ex["shares"])
                realized += (px - entry) * sh
                exits_log.append({
                    "tranche_id": ex["tranche_id"],
                    "shares": sh,
                    "exit_price": round(px, 4),
                    "reason": ex["reason"],
                    "hour": close_hour,
                })
            if remaining_shares(trail) <= 0:
                pnl = realized - cost
                return {
                    "exit_price": round(
                        sum(e["exit_price"] * e["shares"] for e in exits_log)
                        / max(1, sum(e["shares"] for e in exits_log)),
                        4,
                    ),
                    "exit_reason": "flat",
                    "exit_hour": last_hour,
                    "pnl": round(pnl, 2),
                    "status": "CLOSED",
                    "tranche_init": tranche_init,
                    "tranche_exits": exits_log,
                    "shares_left": 0,
                }

    rem = remaining_shares(trail)
    mark = eod_close if eod_close and eod_close > 0 else entry
    if rem > 0:
        realized += (mark - entry) * rem
        exits_log.append({
            "tranche_id": "REMAINING",
            "shares": rem,
            "exit_price": round(mark, 4),
            "reason": "eod_mark",
            "hour": 16,
        })
    pnl = realized - cost
    return {
        "exit_price": round(mark, 4),
        "exit_reason": "eod_mark" if rem > 0 else "flat",
        "exit_hour": 16,
        "pnl": round(pnl, 2),
        "status": "OPEN_EOD" if rem > 0 else "CLOSED",
        "tranche_init": tranche_init,
        "tranche_exits": exits_log,
        "shares_left": rem,
    }


def run_day(
    *,
    date_str: str,
    starting_cash: float = 3000.0,
    force_slots: int = 10,
    allow_llm: bool = True,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    key = load_polygon_key()
    print("=" * 72)
    print(f"PHP DAY REPLAY  date={date_str}  cash=${starting_cash:.0f}  slots={force_slots}")
    print("NO live orders — Polygon + case model only")
    print("=" * 72)

    day_stamp = date_str.replace("-", "")
    htf_doc = load_htf_universe(day=day_stamp)
    if not htf_doc:
        raise SystemExit(
            f"No HTF cache for {day_stamp}. Expected "
            f"results/htf_universe/htf_pass_{day_stamp}.json — build that day first."
        )
    htf_rows = {str(r["symbol"]).upper(): r for r in (htf_doc.get("rows") or [])}
    symbols = htf_pass_symbols(day=day_stamp)
    if not symbols:
        symbols = [str(s).upper() for s in (htf_doc.get("symbols") or [])]
    if max_symbols:
        symbols = symbols[:max_symbols]
    print(f"HTF-pass ({day_stamp}): {len(symbols)}")

    # Cache 1H bars once per symbol; skip rate-limit sleeps on cache hits
    bar_cache: dict[str, Any] = {}
    eod_cache: dict[str, float | None] = {}
    catalyst_cache: dict[str, dict[str, Any]] = {}
    case_cache: dict[str, dict[str, Any]] = {}  # reuse case when dossier unchanged-ish
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

    print(f"Prefetching 1H bars for {len(symbols)} symbols...")
    for i, sym in enumerate(symbols, 1):
        try:
            _cached_bars(sym, api_key=key)
        except Exception as exc:
            print(f"  bar miss {sym}: {exc}")
            bar_cache[sym] = pd.DataFrame()
        if i % 25 == 0:
            print(f"  bars {i}/{len(symbols)}")
    print(f"  bar cache ready: {sum(1 for v in bar_cache.values() if v is not None and len(v))} ok")

    cash = float(starting_cash)
    open_pos: dict[str, dict[str, Any]] = {}
    closed: list[dict[str, Any]] = []
    hour_logs: list[dict[str, Any]] = []

    # Force 10-slot sizing for this thought experiment
    unit_s = starting_cash / force_slots
    ladder_n, ladder_s = slot_ladder(starting_cash)
    print(
        f"Ladder at ${starting_cash:.0f}: N={ladder_n} S=${ladder_s:.0f}; "
        f"replay uses N={force_slots} S=${unit_s:.0f} SHARE_LOT={SHARE_LOT} "
        f"(max px ~${unit_s / SHARE_LOT:.0f} for min lot; "
        f">={SHARE_LOT * 2}sh needed for full T1-T4)"
    )

    try:
        for hour in range(5, 16):
            now = _as_et_day(date_str, hour, 15)
            print(f"\n--- {now.strftime('%Y-%m-%d %H:%M ET')} ---")
            n_open = len(open_pos)
            if n_open >= force_slots:
                print(f"  slots full ({n_open}/{force_slots}) — skip new entries")
                hour_logs.append({
                    "hour": hour, "ranked": 0, "attention": 0,
                    "enter": 0, "skipped": "slots_full",
                })
                continue

            # Refresh live gainers each hour; recent boards cache by day
            skip_sleep["on"] = False
            popularity = build_popularity_context(
                api_key=key, as_of=now, include_tws=False,
            )

            rows: list[dict[str, Any]] = []
            skip_sleep["on"] = True  # bar cache hits — no 0.12s × N spam
            for i, sym in enumerate(symbols, 1):
                row = evaluate_1h_symbol(
                    sym, htf_row=htf_rows.get(sym), polygon_key=key, now=now,
                )
                rows.append(row)
                if i % 50 == 0:
                    print(f"  scanned {i}/{len(symbols)}")
            skip_sleep["on"] = False

            ranked = rank_1h_launches(
                rows, polygon_key=key, now=now, attach_social=False,
            )
            # Social without TWS (avoids ib_insync connect spam in offline replay)
            if ranked:
                try:
                    ranked = attach_social_to_rows(
                        ranked,
                        api_key=key,
                        as_of=now,
                        include_x=False,
                        include_st=True,
                        include_tws=False,
                    )
                except Exception as exc:
                    print(f"  social warn: {exc}")
            ranked = [
                r for r in ranked
                if str(r.get("symbol") or "").upper() not in open_pos
            ]
            attention_raw = build_attention_pool(
                ranked,
                polygon_key=key,
                popularity_ctx=popularity,
                include_tws=False,
            )

            # Reuse catalyst + prior case REJECT across hours (ENTER/WAIT re-reviewed)
            for r in attention_raw:
                sym = str(r.get("symbol") or "").upper()
                if sym in catalyst_cache:
                    for k, v in catalyst_cache[sym].items():
                        if r.get(k) in (None, "", 0, {}):
                            r[k] = v
                prior = case_cache.get(sym)
                if prior and str(prior.get("verdict") or "").upper() == "REJECT":
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
                        "deep_catalyst", "dilution_flag", "distress_flag", "guidance_cut",
                        "news_summary", "catalyst_tier", "fresh_catalyst",
                    )
                    if r.get(k) is not None
                }
                if r.get("case_review"):
                    case_cache[sym] = r["case_review"]

            slots_left = force_slots - len(open_pos)
            take_n = min(MAX_NEW_ENTRIES_PER_SCAN, slots_left)
            # Pull extra ENTER candidates so share-size skips can fill the 2/scan cap
            take_pool = select_enter_rows(reviewed, max_n=max(take_n * 5, 10))

            taken_this_hour: list[dict[str, Any]] = []
            for cand in take_pool:
                if len(taken_this_hour) >= take_n:
                    break
                sym = str(cand.get("symbol") or "").upper()
                if sym in open_pos:
                    continue
                px = float(cand.get("htf_1h_close") or cand.get("close") or 0)
                if px <= 0:
                    continue
                budget = min(unit_s, cash)
                shares = shares_for_budget(budget, px)  # live: SHARE_LOT=4
                if shares <= 0:
                    print(
                        f"  SKIP {sym}: shares_zero budget=${budget:.0f} px={px:.2f} "
                        f"(need >= {SHARE_LOT} shares / max px ~${budget / SHARE_LOT:.2f})"
                    )
                    continue
                kill_pct, kill_src = resolve_kill_pct(
                    cand.get("kill_pct"), profile=cand.get("tsd_profile"),
                )
                case = cand.get("case_review") or {}
                trail_preview = init_trail_state(
                    entry_price=px,
                    n_shares=shares,
                    profile=cand.get("tsd_profile") or {},
                )
                t_ids = [t.get("id") for t in (trail_preview.get("tranches") or [])]
                cash -= px * shares
                open_pos[sym] = {
                    "symbol": sym,
                    "entry_hour": hour,
                    "entry_price": round(px, 4),
                    "shares": shares,
                    "kill_pct": kill_pct,
                    "kill_source": kill_src,
                    "kill_price": round(px * (1.0 - kill_pct), 4),
                    "continuation_score": cand.get("continuation_score"),
                    "scan_score": cand.get("scan_score"),
                    "case_verdict": case.get("verdict"),
                    "case_confidence": case.get("confidence"),
                    "case_source": case.get("source"),
                    "structure_note": case.get("structure_note"),
                    "sentiment_note": case.get("sentiment_note"),
                    "room_class": case.get("room_class"),
                    "momentum_context": cand.get("momentum_context"),
                    "tradable_popular": cand.get("tradable_popular"),
                    "recent_leaderboard": cand.get("recent_leaderboard"),
                    "attention_reasons": cand.get("attention_reasons"),
                    "notional": round(px * shares, 2),
                    "sizing_note": f"live SHARE_LOT={SHARE_LOT}",
                    "tranche_ids": t_ids,
                    "tsd_profile": cand.get("tsd_profile") or {},
                }
                taken_this_hour.append(open_pos[sym])
                print(
                    f"  TAKE {sym} {shares}sh @ {px:.2f} kill={kill_pct:.1%} "
                    f"tranches={'+'.join(str(x) for x in t_ids)} "
                    f"cont={cand.get('continuation_score')} "
                    f"pop={int(bool(cand.get('tradable_popular')))} "
                    f"case={case.get('verdict')}/{case.get('source')} "
                    f"why={','.join(cand.get('attention_reasons') or [])}"
                )
                print(f"       structure={case.get('structure_note')!s}")
                print(f"       sentiment={case.get('sentiment_note')!s}")

            # Also log WAIT/REJECT attention for the report
            case_summary = []
            for r in reviewed:
                case_summary.append({
                    "symbol": r.get("symbol"),
                    "verdict": r.get("case_verdict"),
                    "conf": r.get("case_confidence"),
                    "src": (r.get("case_review") or {}).get("source"),
                    "reasons": r.get("attention_reasons"),
                    "cont": r.get("continuation_score"),
                })

            hour_logs.append({
                "hour": hour,
                "ranked": len(ranked),
                "attention": len(reviewed),
                "case_enter": sum(
                    1 for r in reviewed
                    if str(r.get("case_verdict") or "").upper() == "ENTER"
                ),
                "case_board": case_summary,
                "taken": [t["symbol"] for t in taken_this_hour],
                "open_count": len(open_pos),
                "cash": round(cash, 2),
                "popularity_union": len(popularity.get("popular_symbols") or []),
            })
            if not taken_this_hour:
                enters = [c for c in case_summary if c.get("verdict") == "ENTER"]
                waits = [c for c in case_summary if c.get("verdict") == "WAIT"]
                rejects = [c for c in case_summary if c.get("verdict") == "REJECT"]
                print(
                    f"  no takes | ranked={len(ranked)} attn={len(reviewed)} "
                    f"ENTER={len(enters)} WAIT={len(waits)} REJECT={len(rejects)}"
                )

        # Resolve all open to kill-path or EOD
        print("\n--- RESOLVE TO EOD ---")
        skip_sleep["on"] = False
        for sym, pos in list(open_pos.items()):
            if sym not in bar_cache or bar_cache[sym] is None or len(bar_cache[sym]) == 0:
                try:
                    bar_cache[sym] = _orig_bars(sym, api_key=key)
                    _orig_sleep(0.12)
                except Exception:
                    bar_cache[sym] = None
            if sym not in eod_cache:
                eod_cache[sym] = _daily_close(sym, date_str, key)

            result = _simulate_path(
                symbol=sym,
                entry=float(pos["entry_price"]),
                shares=int(pos["shares"]),
                entry_hour=int(pos["entry_hour"]),
                date_str=date_str,
                bars_1h=bar_cache.get(sym),
                eod_close=eod_cache.get(sym),
                profile=pos.get("tsd_profile") or {},
            )
            # Don't persist bulky profile in trade ledger
            pos_out = {k: v for k, v in pos.items() if k != "tsd_profile"}
            trade = {**pos_out, **result}
            # Return cash for remaining + exited notionals at exit prices
            proceeds = 0.0
            for ex in result.get("tranche_exits") or []:
                proceeds += float(ex["exit_price"]) * int(ex["shares"])
            if not result.get("tranche_exits"):
                proceeds = float(result["exit_price"]) * int(pos["shares"])
            cash += proceeds
            closed.append(trade)
            t_ex = ",".join(
                f"{e['tranche_id']}@{e['exit_price']}"
                for e in (result.get("tranche_exits") or [])
            )
            print(
                f"  {sym}: {result['status']} {result['exit_reason']} "
                f"pnl=${result['pnl']:+.2f} exits=[{t_ex}]"
            )
            del open_pos[sym]
    finally:
        t1h_signal._bars_1h_polygon = _orig_bars  # type: ignore[assignment]
        time.sleep = _orig_sleep  # type: ignore[assignment]

    realized = sum(float(t["pnl"]) for t in closed)
    end_equity = starting_cash + realized
    report = {
        "date": date_str,
        "starting_cash": starting_cash,
        "force_slots": force_slots,
        "unit_s": unit_s,
        "ending_cash": round(cash, 2),
        "ending_equity": round(end_equity, 2),
        "realized_pnl": round(realized, 2),
        "trades": closed,
        "hour_logs": hour_logs,
        "runtime_sec": round(time.time() - t0, 1),
        "ladder": {"n": ladder_n, "s": ladder_s},
        "notes": [
            "Theoretical: no TWS fills; entry at 1H close",
            "Sizing: live shares_for_budget SHARE_LOT=4 (skip if price > ~S/4)",
            "Exits: strategy_a 4-tranche trail on 1H bars (collapses to 2 legs if only 4sh); else EOD mark",
            "COST_PER_TRADE=0.0015 RT; max 2 new entries/hour; 10 slots @ $300",
            "Case: rules + LLM (no web_search); TWS scanners off for offline replay",
        ],
    }
    out_path = PIPELINE_DIR / "results" / f"php_day_replay_{date_str.replace('-', '')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"TRADES: {len(closed)}")
    print(f"P&L:    ${realized:+,.2f}")
    print(f"Equity: ${starting_cash:,.2f} -> ${end_equity:,.2f}")
    print(f"Wrote {out_path}")
    print(f"Runtime {report['runtime_sec']}s")
    print("=" * 72)
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Peak Hour theoretical day replay")
    p.add_argument("--date", default="2026-09-09")
    p.add_argument("--cash", type=float, default=3000.0)
    p.add_argument("--slots", type=int, default=10)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--max-symbols", type=int, default=None)
    args = p.parse_args()
    run_day(
        date_str=args.date,
        starting_cash=args.cash,
        force_slots=args.slots,
        allow_llm=not args.no_llm,
        max_symbols=args.max_symbols,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
