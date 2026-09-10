"""
Q-ALPHA TSD pipeline — Phase 4 software trail monitor.

3-layer stop pyramid (Phase 2.5 kill-until-1R):
  L1 broker kill (always on) | L2 BE lock after +1R | L3 T1–T4 software trail

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
from ib_insync import IB, Stock

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
LOOP_START_HOUR_ET = 4
LOOP_STOP_HOUR_ET = 20
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    load_state,
    open_symbols,
    record_leg_exit,
    save_state,
)
from tsd_scan_pipeline.tsd_entry import classify_session  # noqa: E402
from tsd_scan_pipeline.tsd_exit import (  # noqa: E402
    housekeep_orphan_sell_orders,
    kill_price_already_through,
    place_tsd_exit,
    repair_unfillable_stop_limit_sells,
    sync_kill_quantity,
)
from tsd_scan_pipeline.tsd_notify import format_exited, notify_tsd  # noqa: E402
from tsd_scan_pipeline.tsd_base_break import check_base_break
from tsd_scan_pipeline.tsd_scan_ibkr import fetch_3h_bars
from tsd_scan_pipeline.build_3h_bars import bars_from_ibkr
from tsd_scan_pipeline.tsd_structure import (  # noqa: E402
    ensure_rth_monitoring,
    maybe_arm_be_lock_on_1r,
    maybe_ratchet_breakeven,
    poll_interval_sec,
    should_idle_no_1r,
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
TWS_CLIENT_ID_FALLBACKS = (TWS_CLIENT_ID, 85, 75)
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
        from tsd_scan_pipeline.tsd_kill import resolve_kill_pct

        kill, src = resolve_kill_pct(profile.get("kill_pct"), profile=profile)
        leg["kill_pct"] = kill
        leg["kill_source"] = src
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

    filled_sh = int(fill.get("shares") or 0)
    status = str(fill.get("status") or "")
    if status not in ("FILLED", "PARTIAL", "DRY_RUN") or filled_sh <= 0:
        print(
            f"  {sym} STRUCTURE EXIT FAIL status={status} "
            f"reason={fill.get('reason')} — book left OPEN"
        )
        results.append({
            "symbol": sym,
            "leg": leg_index,
            "reason": reason,
            "status": "EXIT_FAIL",
            "fill": fill,
        })
        return results

    if filled_sh < rem:
        print(
            f"  {sym} STRUCTURE PARTIAL fill {filled_sh}/{rem} — "
            f"keeping residual open (no false full close)"
        )

    record_leg_exit(
        pos,
        leg_index=leg_index,
        shares=filled_sh,
        exit_price=float(fill.get("fill_price") or px),
        reason=reason,
        tranche_id="STRUCTURE",
        order_id=fill.get("order_id"),
    )
    exit_px = float(fill.get("fill_price") or px)
    # Close tranches from the front until filled_sh is consumed
    left = filled_sh
    for tranche in trail.get("tranches") or []:
        if left <= 0:
            break
        if tranche.get("closed"):
            continue
        t_sh = int(tranche.get("shares") or 0)
        if t_sh <= 0:
            continue
        if t_sh <= left:
            tranche["closed"] = True
            tranche["trailing"] = False
            tranche["exit_price"] = exit_px
            tranche["exit_time"] = datetime.now(ET).isoformat()
            tranche["exit_reason"] = reason
            left -= t_sh
        else:
            # Split: close filled portion by shrinking open tranche
            tranche["shares"] = t_sh - left
            left = 0
    trail["kill_stop_cancelled"] = False
    leg["trail"] = trail

    still = remaining_shares(trail) if trail else max(0, rem - filled_sh)
    if still <= 0:
        leg["status"] = "CLOSED"
    else:
        leg["status"] = "OPEN"
        # Shrink leg share count to residual for capacity/dashboard
        leg["shares"] = still
    pos["legs"][leg_index] = leg
    if not dry_run:
        sync_kill_quantity(ib, leg, sym, dry_run=False)
    pnl = (exit_px - entry_px) * filled_sh if entry_px > 0 else None
    if not dry_run:
        notify_tsd(
            format_exited(
                sym,
                reason=reason if still <= 0 else f"{reason}_partial",
                shares=filled_sh,
                exit_price=exit_px,
                pnl_dollars=pnl,
            )
        )
    results.append({
        "symbol": sym,
        "leg": leg_index,
        "reason": reason,
        "fill": fill,
        "remaining": still,
    })
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
        boot = ensure_rth_monitoring(leg)
        leg = boot["leg"]
        trail = leg.get("trail") or trail
        if not boot.get("armed"):
            leg["trail"] = trail
            pos["legs"][leg_index] = leg
            results.append({
                "symbol": sym,
                "leg": leg_index,
                "status": "RTH_PENDING",
                "reason": boot.get("reason"),
            })
            return results

    maybe_arm_be_lock_on_1r(leg, trail, quote_high=quote["high"])
    trail = leg.get("trail") or trail

    # Fail-closed: if last already through kill STP LMT ceiling, flatten now.
    # Covers residual 1-share longs left under a gapped stop-limit (JANX).
    last_px = float(quote.get("close") or quote.get("last") or 0)
    kill_px = float(trail.get("kill_price") or 0)
    if last_px > 0 and kill_price_already_through(last_px, kill_px):
        print(
            f"  {sym} kill gap unfillable last={last_px:.2f} "
            f"kill={kill_px:.2f} — force flatten"
        )
        results.extend(
            _exit_all_remaining(
                ib, pos, leg_index, leg, sym,
                reason="kill_gap_unfillable",
                quote=quote,
                dry_run=dry_run,
            )
        )
        return results

    if should_idle_no_1r(trail, leg):
        results.extend(
            _exit_all_remaining(
                ib, pos, leg_index, leg, sym,
                reason="idle_no_1r",
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
        filled_sh = int(fill.get("shares") or 0)
        if str(fill.get("status") or "") not in ("FILLED", "PARTIAL", "DRY_RUN") or filled_sh <= 0:
            print(
                f"  {sym} EXIT FAIL {ex['tranche_id']}: "
                f"{fill.get('status')} {fill.get('reason')} — reopen tranche (no broker fill)"
            )
            # evaluate_trail_tick already closed the tranche in software — undo
            for tranche in trail.get("tranches") or []:
                if str(tranche.get("id")) == str(ex["tranche_id"]):
                    tranche["closed"] = False
                    tranche["exit_price"] = None
                    tranche["exit_time"] = None
                    tranche["exit_reason"] = None
                    break
            results.append({
                "symbol": sym,
                "leg": leg_index,
                **ex,
                "fill": fill,
                "status": "EXIT_FAIL",
            })
            continue
        # evaluate_trail_tick already marked full tranche closed; adjust if partial
        if filled_sh < int(ex["shares"]):
            for tranche in trail.get("tranches") or []:
                if str(tranche.get("id")) == str(ex["tranche_id"]):
                    tranche["closed"] = False
                    tranche["shares"] = int(ex["shares"]) - filled_sh
                    tranche["exit_price"] = None
                    tranche["exit_time"] = None
                    tranche["exit_reason"] = None
                    break
        record_leg_exit(
            pos,
            leg_index=leg_index,
            shares=filled_sh,
            exit_price=float(fill.get("fill_price") or ex["exit_price"]),
            reason=str(ex["reason"]),
            tranche_id=str(ex["tranche_id"]),
            order_id=fill.get("order_id"),
        )
        results.append({
            "symbol": sym,
            "leg": leg_index,
            **ex,
            "shares": filled_sh,
            "fill": fill,
        })

    # Keep leg.shares = remaining open size (not original fill size).
    rem_now = remaining_shares(trail)
    if rem_now >= 0:
        leg["shares"] = rem_now
    leg["trail"] = trail
    pos["legs"][leg_index] = leg

    if not dry_run:
        sync_kill_quantity(ib, leg, sym, dry_run=False)

    if remaining_shares(trail) <= 0:
        leg["status"] = "CLOSED"
        if not dry_run:
            sync_kill_quantity(ib, leg, sym, dry_run=False)
            # Aggregate PnL across exits on this leg for one telegram (full flat).
            entry_px = float(trail.get("entry_price") or leg.get("price") or 0)
            pnl = 0.0
            exit_shares = 0
            last_reason = "trail_flat"
            last_px = entry_px
            for ex in leg.get("exits") or []:
                sh = int(ex.get("shares") or 0)
                px = float(ex.get("exit_price") or 0)
                if sh > 0 and px > 0 and entry_px > 0:
                    pnl += (px - entry_px) * sh
                    exit_shares += sh
                    last_px = px
                last_reason = str(ex.get("reason") or last_reason)
            notify_tsd(
                format_exited(
                    sym,
                    reason=last_reason,
                    shares=exit_shares or None,
                    exit_price=last_px if exit_shares else None,
                    pnl_dollars=pnl if exit_shares else None,
                )
            )
    if is_t4_only(trail):
        pos["t4_only"] = True
        open_ids = [
            str(t.get("id") or "").upper()
            for t in (trail.get("tranches") or [])
            if not t.get("closed")
        ]
        label = "+".join(open_ids) if open_ids else "runner"
        print(f"  {sym}: {label} trailing — slot freed")

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
    ib = IB()
    now = datetime.now(ET)
    mode = "DRY_RUN" if dry_run else "LIVE"
    session = classify_session(now)

    print("=" * 64)
    print(f"Q-ALPHA TSD TRAIL MONITOR - {mode}")
    print("Structure: KILL ONLY until +1R")
    print(
        f"ET={now.strftime('%Y-%m-%d %H:%M:%S')} session={session} "
        f"clientIds={list(TWS_CLIENT_ID_FALLBACKS)}"
    )
    print("=" * 64)

    state = load_state()
    opens = [p for p in state.get("positions") or [] if str(p.get("status", "OPEN")).upper() == "OPEN"]
    print(f"Open positions: {len(opens)}  symbols={open_symbols(state)}")

    last_connect_error: Exception | None = None
    used_client_id: int | None = None
    for client_id in TWS_CLIENT_ID_FALLBACKS:
        try:
            ib.connect(TWS_HOST, TWS_PORT, clientId=client_id, timeout=12)
            used_client_id = client_id
            print(f"CONNECTED clientId={client_id}")
            break
        except Exception as exc:
            last_connect_error = exc
            print(f"CONNECT FAIL clientId={client_id}: {exc}")
            try:
                ib.disconnect()
            except Exception:
                pass
            time.sleep(0.25)
    if used_client_id is None:
        error = str(last_connect_error or "all client IDs unavailable")
        print(f"CONNECT FAILED: {error}")
        return {"error": error, "checked_at": now.isoformat()}

    actions: list[dict[str, Any]] = []

    # Peak Hour micro-confirm: WATCHING → BUY / SKIP (even if book is flat)
    try:
        from tsd_scan_pipeline.tsd_watch_queue import (
            process_micro_confirm_queue,
            watching_symbols,
        )

        watching = watching_symbols()
        if watching:
            print(f"\n--- MICRO-CONFIRM watching={watching} ---")
            micro = process_micro_confirm_queue(
                ib, book_state=state, live=not dry_run,
            )
            actions.extend({"type": "micro_confirm", **r} for r in micro)
            state = load_state()
            opens = [
                p for p in state.get("positions") or []
                if str(p.get("status", "OPEN")).upper() == "OPEN"
            ]
    except Exception as exc:
        print(f"  micro-confirm warn: {exc}")

    if not opens:
        print("Nothing to trail-monitor.")
        try:
            ib.disconnect()
        except Exception:
            pass
        payload = {
            "mode": mode,
            "checked_at": now.isoformat(),
            "actions": actions,
            "open_count": 0,
        }
        _save_snapshot(payload)
        return payload
    try:
        broker_positions: dict[str, float] = {}
        for broker_pos in ib.positions() or []:
            broker_symbol = str(
                getattr(broker_pos.contract, "symbol", "") or ""
            ).upper()
            if broker_symbol:
                broker_positions[broker_symbol] = (
                    broker_positions.get(broker_symbol, 0.0)
                    + float(broker_pos.position or 0)
                )
    except Exception as exc:
        print(f"BROKER POSITION CHECK FAILED: {exc}")
        try:
            ib.disconnect()
        except Exception:
            pass
        return {"error": str(exc), "checked_at": now.isoformat()}

    if not dry_run:
        try:
            from tws_intraday_sync import (
                _reconcile_broker_orphan_longs,
                _reconcile_tsd_broker_kills,
            )

            orphans = _reconcile_broker_orphan_longs(broker_positions)
            if orphans:
                print(f"Broker orphan longs reopened: {orphans}")
                state = load_state()
                opens = [
                    pos for pos in state.get("positions") or []
                    if str(pos.get("status", "OPEN")).upper() == "OPEN"
                ]
            reconciled = _reconcile_tsd_broker_kills(ib, broker_positions)
            if reconciled:
                print(f"Broker kills reconciled before trail: {reconciled}")
                state = load_state()
                opens = [
                    pos for pos in state.get("positions") or []
                    if str(pos.get("status", "OPEN")).upper() == "OPEN"
                ]
        except Exception as exc:
            print(f"  broker reconcile warn: {exc}")

    actions: list[dict[str, Any]] = []
    for pos in opens:
        sym = pos["symbol"]
        if not dry_run and float(broker_positions.get(sym, 0.0)) <= 0:
            # Never submit a SELL unless IBKR confirms a live long. A broker
            # kill may have filled while the local loop was disconnected.
            print(f"\n--- {sym} ---")
            print(f"  {sym}: broker flat — trail SELL blocked")
            continue
        print(f"\n--- {sym} ---")
        leg_results = _process_position(ib, pos, dry_run=dry_run)
        actions.extend(leg_results)

    if not dry_run:
        save_state(state)

        def _trail_mark_fn(ib_conn, symbol: str, *, timeout_sec: float = 8.0) -> float | None:
            """Mark from live quote; fall back to trail last_close on the book leg."""
            q = _fetch_quote(ib_conn, symbol)
            if q and q.get("close"):
                return float(q["close"])
            for pos in state.get("positions") or []:
                if str(pos.get("symbol") or "").upper() != str(symbol).upper():
                    continue
                for leg in pos.get("legs") or []:
                    if str(leg.get("status") or "").upper() != "OPEN":
                        continue
                    trail = leg.get("trail") or {}
                    for key in ("last_close", "peak_high"):
                        try:
                            v = float(trail.get(key) or 0)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass
                    try:
                        v = float(leg.get("price") or 0)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            return None

        # Always refresh cloud MTM when connected — do not depend solely on clientId 96 sync.
        try:
            from tsd_supabase_sync import push_dashboard_best_effort

            push_dashboard_best_effort(
                ib, mark_fn=_trail_mark_fn, book=state, telegram_on_fail=False,
            )
        except Exception as exc:
            print(f"  trail dashboard sync warn: {exc}")

        # Flatten residual longs whose STP LMT kill is unfillable (gap-through).
        if not dry_run:
            try:
                stuck_actions = repair_unfillable_stop_limit_sells(ib, dry_run=False)
                if stuck_actions:
                    actions.extend(stuck_actions)
            except Exception as exc:
                print(f"  stuck-exit repair warn: {exc}")

        # Cancel leftover SELL stops on broker-flat names (missed kill cancels).
        if not dry_run:
            try:
                orphan_actions = housekeep_orphan_sell_orders(ib, dry_run=False)
                if orphan_actions:
                    actions.extend(
                        {"type": "orphan_sell_cancel", **row} for row in orphan_actions
                    )
            except Exception as exc:
                print(f"  orphan housekeep warn: {exc}")

    try:
        ib.disconnect()
    except Exception:
        pass

    payload = {
        "mode": mode,
        "session": session,
        "client_id": used_client_id,
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
            now_et = datetime.now(ET)
            if now_et.hour >= LOOP_STOP_HOUR_ET or now_et.hour < LOOP_START_HOUR_ET:
                print(
                    f"Loop window closed at {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                    f"(active {LOOP_START_HOUR_ET:02d}:00–{LOOP_STOP_HOUR_ET:02d}:00 ET)"
                )
                break
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
