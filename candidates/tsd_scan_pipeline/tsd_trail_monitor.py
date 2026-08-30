"""
Q-ALPHA TSD pipeline — Phase 4 software trail monitor.

Polls IBKR for open TSD positions, runs strategy_a 4-tranche trail logic,
places session-aware SELL orders, cancels emergency kill stops once software
trail is active.

Usage (TWS paper open, port 7497):
  py -3 candidates/tsd_scan_pipeline/tsd_trail_monitor.py --once
  py -3 candidates/tsd_scan_pipeline/tsd_trail_monitor.py --loop --interval 60
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
from tsd_scan_pipeline.tsd_exit import cancel_order_safe, place_tsd_exit  # noqa: E402
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


def _process_position(
    ib: IB,
    pos: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Evaluate trail for all open legs on one symbol."""
    sym = str(pos["symbol"]).upper()
    results: list[dict[str, Any]] = []
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
        leg = _ensure_trail_on_leg(leg, sym)
        trail = maybe_roll_trading_day(dict(leg["trail"]))
        force_cap = at_time_cap(trail)
        trail, exits = evaluate_trail_tick(
            trail,
            high=quote["high"],
            low=quote["low"],
            close=quote["close"],
            when=when,
            force_time_cap=force_cap,
        )

        # Cancel emergency kill stop once software trail is managing the leg
        if not trail.get("kill_stop_cancelled") and not dry_run:
            if cancel_order_safe(ib, leg.get("kill_order_id")):
                trail["kill_stop_cancelled"] = True
                print(f"  {sym} leg[{i}]: cancelled emergency kill oid={leg.get('kill_order_id')}")

        leg["trail"] = trail

        for ex in exits:
            print(
                f"  {sym} EXIT {ex['tranche_id']}: {ex['shares']}sh "
                f"@ {ex['exit_price']:.2f} reason={ex['reason']}"
            )
            fill: dict[str, Any]
            if dry_run:
                fill = {"status": "DRY_RUN", **ex}
            else:
                fill = place_tsd_exit(
                    ib,
                    sym,
                    int(ex["shares"]),
                    ref_price=float(ex["exit_price"]),
                    reason=str(ex["reason"]),
                )
            record_leg_exit(
                pos,
                leg_index=i,
                shares=int(ex["shares"]),
                exit_price=float(fill.get("fill_price") or ex["exit_price"]),
                reason=str(ex["reason"]),
                tranche_id=str(ex["tranche_id"]),
                order_id=fill.get("order_id"),
            )
            results.append({"symbol": sym, "leg": i, **ex, "fill": fill})

        if remaining_shares(trail) <= 0:
            leg["status"] = "CLOSED"
        if is_t4_only(trail):
            pos["t4_only"] = True
            print(f"  {sym}: T4-only runner — slot freed")
        pos["legs"][i] = leg

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

    print("=" * 64)
    print(f"Q-ALPHA TSD TRAIL MONITOR - {mode}")
    print(f"ET={now.strftime('%Y-%m-%d %H:%M:%S')} clientId={TWS_CLIENT_ID}")
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
    parser.add_argument("--interval", type=int, default=60, help="Loop seconds (default 60)")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate only — no orders")
    args = parser.parse_args()

    if args.loop:
        print(f"Loop mode: interval={args.interval}s  Ctrl+C to stop")
        while True:
            try:
                from tsd_scan_pipeline.scheduler import heartbeat_trail_loop

                heartbeat_trail_loop()
            except Exception:
                pass
            run_monitor(dry_run=args.dry_run)
            time.sleep(max(5, args.interval))
    else:
        run_monitor(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
