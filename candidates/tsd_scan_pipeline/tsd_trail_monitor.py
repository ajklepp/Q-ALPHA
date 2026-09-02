"""
Q-ALPHA TSD pipeline — Phase 4 software trail monitor.

3-layer stop pyramid:
  L1 broker kill (always on) | L2 RTH structure stop | L3 T1–T4 software trail

Usage (TWS paper open, port 7497):
  py -3 candidates/tsd_scan_pipeline/tsd_trail_monitor.py --once
  py -3 candidates/tsd_scan_pipeline/tsd_trail_monitor.py --loop --adaptive
  py -3 candidates/tsd_scan_pipeline/tsd_trail_monitor.py --dry-run --once
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz
from ib_insync import IB, Stock, util

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    load_state,
    open_symbols,
    record_leg_exit,
    save_state,
)
from tsd_scan_pipeline.tsd_entry import classify_session  # noqa: E402
from tsd_scan_pipeline.tsd_exit import place_tsd_exit, sync_kill_quantity  # noqa: E402
from tsd_scan_pipeline.tsd_base_break import check_base_break
from tsd_scan_pipeline.tsd_scan_ibkr import fetch_3h_bars
from tsd_scan_pipeline.build_3h_bars import bars_from_ibkr
from tsd_scan_pipeline.tsd_structure import (  # noqa: E402
    apply_day_structure_rules,
    bootstrap_rth_structure,
    maybe_ratchet_breakeven,
    poll_interval_sec,
    should_day3_force_exit,
    structure_stop_breached,
)
from tsd_scan_pipeline.tsd_trail import (  # noqa: E402
    at_time_cap,
    evaluate_trail_tick,
    init_trail_state,
    is_t4_only,
    load_tsd_profile,
    maybe_roll_trading_day,
    remaining_shares,
)

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 95
ET = pytz.timezone("America/New_York")
RESULTS_DIR = PIPELINE_DIR / "results"


def _fetch_quote(ib: IB, symbol: str) -> dict[str, float] | None:
    """Snapshot high/low/last for trail evaluation."""
    contract = Stock(symbol.upper(), "SMART", "USD")
    ib.qualifyContracts(contract)
    t = ib.reqMktData(contract, "", False, False)
    ib.sleep(1.5)

    def _f(attr: str) -> float | None:
        try:
            v = float(getattr(t, attr, None) or 0)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    last = _f("last") or _f("close") or _f("bid")
    high = _f("high") or last
    low = _f("low") or last
    try:
        ib.cancelMktData(contract)
    except Exception:
        pass
    if last is None:
        return None
    return {
        "last": float(last),
        "high": float(high or last),
        "low": float(low or last),
        "close": float(last),
    }


def _ensure_trail_on_leg(leg: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Initialize trail state on leg if missing."""
    if leg.get("trail"):
        return leg
    profile = load_tsd_profile(symbol)
    trail = init_trail_state(
        float(leg["price"]),
        int(leg["shares"]),
        profile,
    )
    leg = dict(leg)
    leg["trail"] = trail
    if profile and not leg.get("kill_pct"):
        leg["kill_pct"] = profile.get("kill_pct")
    return leg


def _exit_all_remaining(
    ib: IB,
    pos: dict[str, Any],
    leg_index: int,
    leg: dict[str, Any],
    sym: str,
    *,
    reason: str,
    quote: dict[str, float],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Software exit for all remaining shares (structure / day-3)."""
    trail = leg.get("trail") or {}
    rem = remaining_shares(trail) if trail else int(leg.get("shares") or 0)
    if rem <= 0:
        return []

    px = float(quote.get("close") or quote.get("last") or leg.get("price") or 0)
    entry_px = float((leg.get("trail") or {}).get("entry_price") or leg.get("price") or 0)
    print(f"  {sym} STRUCTURE EXIT {rem}sh @ ~{px:.2f} reason={reason}")

    results: list[dict[str, Any]] = []
    if dry_run:
        fill = {"status": "DRY_RUN", "fill_price": px, "shares": rem}
    else:
        fill = place_tsd_exit(
            ib, sym, rem, ref_price=px, entry_price=entry_px, reason=reason,
        )

    record_leg_exit(
        pos,
        leg_index=leg_index,
        shares=rem,
        exit_price=float(fill.get("fill_price") or px),
        reason=reason,
        tranche_id="STRUCTURE",
        order_id=fill.get("order_id"),
    )
    leg["status"] = "CLOSED"
    trail["kill_stop_cancelled"] = False
    leg["trail"] = trail
    pos["legs"][leg_index] = leg
    if not dry_run:
        sync_kill_quantity(ib, leg, sym, dry_run=False)
    results.append({"symbol": sym, "leg": leg_index, "reason": reason, "fill": fill})
    return results


def _process_leg(
    ib: IB,
    pos: dict[str, Any],
    leg_index: int,
    leg: dict[str, Any],
    sym: str,
    quote: dict[str, float],
    *,
    dry_run: bool,
    when: str,
) -> list[dict[str, Any]]:
    """RTH monitoring for one open leg."""
    results: list[dict[str, Any]] = []

    leg = _ensure_trail_on_leg(leg, sym)
    trail = maybe_roll_trading_day(dict(leg["trail"]))

    if not leg.get("rth_armed"):
        boot = bootstrap_rth_structure(ib, leg, sym, dry_run=dry_run)
        leg = boot["leg"]
        trail = leg.get("trail") or trail
        if not boot.get("armed"):
            leg["trail"] = trail
            pos["legs"][leg_index] = leg
            results.append({
                "symbol": sym,
                "leg": leg_index,
                "status": "BOOTSTRAP_PENDING",
                "reason": boot.get("reason"),
            })
            return results

    apply_day_structure_rules(leg, trail)

    if should_day3_force_exit(trail):
        results.extend(
            _exit_all_remaining(
                ib, pos, leg_index, leg, sym,
                reason="day3_thesis_fail",
                quote=quote,
                dry_run=dry_run,
            )
        )
        return results

    try:
        raw_bars = fetch_3h_bars(ib, sym)
        bars_df = bars_from_ibkr(raw_bars)
        bars_list = bars_df.reset_index().rename(columns={"index": "time"}).to_dict("records")
        broke, base_info = check_base_break(bars_list, quote["close"])
        if broke:
            print(
                f"  {sym} base_break_down close={quote['close']:.2f} "
                f"base_low={base_info.get('base_low') if base_info else '?'}"
            )
            results.extend(
                _exit_all_remaining(
                    ib, pos, leg_index, leg, sym,
                    reason="base_break_down",
                    quote=quote,
                    dry_run=dry_run,
                )
            )
            return results
    except Exception as exc:
        print(f"  {sym} base_break check skipped: {exc}")

    structure_stop = leg.get("structure_stop") or trail.get("structure_stop")
    if structure_stop_breached(quote["low"], structure_stop):
        results.extend(
            _exit_all_remaining(
                ib, pos, leg_index, leg, sym,
                reason="structure_stop",
                quote=quote,
                dry_run=dry_run,
            )
        )
        return results

    maybe_ratchet_breakeven(leg, trail, quote_high=quote["high"])
    trail = leg.get("trail") or trail

    force_cap = at_time_cap(trail)
    trail, exits = evaluate_trail_tick(
        trail,
        high=quote["high"],
        low=quote["low"],
        close=quote["close"],
        when=when,
        force_time_cap=force_cap,
    )
    leg["trail"] = trail

    for ex in exits:
        print(
            f"  {sym} EXIT {ex['tranche_id']}: {ex['shares']}sh "
            f"@ {ex['exit_price']:.2f} reason={ex['reason']}"
        )
        entry_px = float(
            (leg.get("trail") or {}).get("entry_price") or leg.get("price") or 0
        )
        if dry_run:
            fill = {"status": "DRY_RUN", **ex}
        else:
            fill = place_tsd_exit(
                ib,
                sym,
                int(ex["shares"]),
                ref_price=float(ex["exit_price"]),
                entry_price=entry_px,
                reason=str(ex["reason"]),
            )
        record_leg_exit(
            pos,
            leg_index=leg_index,
            shares=int(ex["shares"]),
            exit_price=float(fill.get("fill_price") or ex["exit_price"]),
            reason=str(ex["reason"]),
            tranche_id=str(ex["tranche_id"]),
            order_id=fill.get("order_id"),
        )
        results.append({"symbol": sym, "leg": leg_index, **ex, "fill": fill})

    if not dry_run:
        sync_kill_quantity(ib, leg, sym, dry_run=False)

    if remaining_shares(trail) <= 0:
        leg["status"] = "CLOSED"
        if not dry_run:
            sync_kill_quantity(ib, leg, sym, dry_run=False)
    if is_t4_only(trail):
        pos["t4_only"] = True
        print(f"  {sym}: T4-only runner — slot freed")

    pos["legs"][leg_index] = leg
    return results


def _process_position(
    ib: IB,
    pos: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Evaluate trail for all open legs on one symbol."""
    sym = str(pos["symbol"]).upper()
    results: list[dict[str, Any]] = []
    session = classify_session()

    if session != "RTH":
        print(f"  {sym}: session={session} — kill backstop only (no software trail)")
        for i, leg in enumerate(list(pos.get("legs") or [])):
            if leg.get("status") == "CLOSED":
                continue
            if not dry_run:
                sync_kill_quantity(ib, leg, sym, dry_run=False)
            pos["legs"][i] = leg
        return results

    quote = _fetch_quote(ib, sym)
    if quote is None:
        results.append({"symbol": sym, "status": "SKIP", "reason": "no_quote"})
        return results

    when = datetime.now(ET).isoformat()
    pos_closed = True

    for i, leg in enumerate(list(pos.get("legs") or [])):
        if leg.get("status") == "CLOSED":
            continue
        pos_closed = False
        leg_results = _process_leg(
            ib, pos, i, leg, sym, quote, dry_run=dry_run, when=when,
        )
        results.extend(leg_results)

    if pos_closed or all(l.get("status") == "CLOSED" for l in pos.get("legs") or []):
        pos["status"] = "CLOSED"
        pos["closed_at"] = when
        print(f"  {sym}: position CLOSED")

    return results


def run_monitor(*, dry_run: bool = False) -> dict[str, Any]:
    """Single monitor pass over all open TSD book positions."""
    util.startLoop()
    ib = IB()
    now = datetime.now(ET)
    mode = "DRY_RUN" if dry_run else "LIVE"
    session = classify_session(now)

    print("=" * 64)
    print(f"Q-ALPHA TSD TRAIL MONITOR - {mode}")
    print(f"ET={now.strftime('%Y-%m-%d %H:%M:%S')} session={session} clientId={TWS_CLIENT_ID}")
    print("=" * 64)

    state = load_state()
    opens = [p for p in state.get("positions") or [] if str(p.get("status", "OPEN")).upper() == "OPEN"]
    print(f"Open positions: {len(opens)}  symbols={open_symbols(state)}")

    if not opens:
        print("Nothing to monitor.")
        payload = {"mode": mode, "checked_at": now.isoformat(), "actions": []}
        _save_snapshot(payload)
        return payload

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"CONNECT FAILED: {exc}")
        return {"error": str(exc), "checked_at": now.isoformat()}

    actions: list[dict[str, Any]] = []
    for pos in opens:
        sym = pos["symbol"]
        print(f"\n--- {sym} ---")
        leg_results = _process_position(ib, pos, dry_run=dry_run)
        actions.extend(leg_results)

    if not dry_run:
        save_state(state)

    try:
        ib.disconnect()
    except Exception:
        pass

    payload = {
        "mode": mode,
        "session": session,
        "checked_at": now.isoformat(),
        "open_count": len(opens),
        "actions": actions,
    }
    path = _save_snapshot(payload)
    print("")
    print("=" * 64)
    print(f"Done — {len(actions)} action(s). Snapshot: {path}")
    print("=" * 64)
    return payload


def _save_snapshot(payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y%m%d_%H%M")
    path = RESULTS_DIR / f"trail_monitor_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    state = load_state()
    state["last_trail_monitor_at"] = payload.get("checked_at")
    if not payload.get("error"):
        save_state(state)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD 4-tranche software trail monitor")
    parser.add_argument("--once", action="store_true", help="Single pass (default)")
    parser.add_argument("--loop", action="store_true", help="Loop until interrupted")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Loop seconds when --adaptive not set (default 60)",
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="RTH 30s / extended 300s poll (recommended)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Evaluate only — no orders")
    args = parser.parse_args()

    if args.loop:
        print("Loop mode: Ctrl+C to stop")
        while True:
            try:
                from tsd_scan_pipeline.scheduler import heartbeat_trail_loop

                heartbeat_trail_loop()
            except Exception:
                pass
            run_monitor(dry_run=args.dry_run)
            wait = poll_interval_sec() if args.adaptive else max(5, args.interval)
            print(f"  sleeping {wait}s (session={classify_session()})")
            time.sleep(wait)
    else:
        run_monitor(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
