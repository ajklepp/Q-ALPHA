#!/usr/bin/env python3
"""
Ops helper: drop live structure_stop / BE lock fields on open Peak Hour longs.

Default is dry-run (prints the plan, writes nothing). `--apply` updates
tsd_book_state.json only:

  1) Clears structure_stop / structure_stop_reason (exit no longer uses BE lock)
  2) Never removes kill_price / kill_order_id
  3) If MFE ≥ ~3–4%, ratchets kill UP and tightens software trail widths
     using ticker MAE/MFE priors when a profile exists

Does not cancel IB orders unless `--sync-broker` is also passed. Even then,
sync_kill_quantity cancel+replaces the kill only after a replacement stop
is placed at the (same or higher) kill price.

Laptop (ATRC example):
  py -3 candidates\\tsd_scan_pipeline\\migrate_structure_stop_to_trail.py --symbol ATRC
  py -3 candidates\\tsd_scan_pipeline\\migrate_structure_stop_to_trail.py --symbol ATRC --apply

After --apply, run trail monitor --once so the broker kill ratchets up
(if kill_price rose) without a naked window.

MT3 / 3R shadow book is not modified.
"""
from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    TSD_STATE_FILE,
    load_state,
    save_state,
)
from tsd_scan_pipeline.tsd_structure import (  # noqa: E402
    disarm_live_structure_stop,
    maybe_lock_profit_via_trail,
)
from tsd_scan_pipeline.tsd_trail import load_tsd_profile  # noqa: E402


def _finite(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def plan_leg_migration(
    leg: dict[str, Any],
    *,
    quote_high: float | None = None,
    quote_last: float | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return a migrated copy of an open leg plus a summary of field changes.

    Kill is never cleared. structure_stop fields are always cleared.
    """
    before = deepcopy(leg)
    after = deepcopy(leg)
    trail = dict(after.get("trail") or {})
    kill_before = _finite(trail.get("kill_price") or after.get("kill_price"))
    if kill_before <= 0:
        return {
            "ok": False,
            "reason": "no_kill_price",
            "before": before,
            "after": after,
        }

    trail = dict(after.get("trail") or {})
    disarm_live_structure_stop(after, trail)
    trail = dict(after.get("trail") or trail)
    peak = quote_high
    if peak is None or peak <= 0:
        peak = max(
            _finite(trail.get("peak_high")),
            _finite(after.get("peak_high")),
            _finite(after.get("price") or trail.get("entry_price")),
        )
    last = quote_last if quote_last and quote_last > 0 else None
    if last is None:
        last = _finite(trail.get("last_close")) or None
        if last is None:
            last = _finite(after.get("last_close")) or None
    maybe_lock_profit_via_trail(
        after,
        trail,
        quote_high=float(peak or 0),
        quote_last=last,
        profile=profile,
        force=True,
    )
    after_trail = after.get("trail") or trail
    kill_after = _finite(after_trail.get("kill_price") or after.get("kill_price"))
    if kill_after <= 0:
        # Defensive: never persist a naked long. Restore prior kill.
        after_trail["kill_price"] = kill_before
        after["kill_price"] = kill_before
        after["trail"] = after_trail
        kill_after = kill_before

    if kill_after + 1e-9 < kill_before:
        after_trail["kill_price"] = kill_before
        after["kill_price"] = kill_before
        after["trail"] = after_trail
        kill_after = kill_before

    return {
        "ok": True,
        "reason": "migrated",
        "before": before,
        "after": after,
        "kill_before": kill_before,
        "kill_after": kill_after,
        "structure_stop_before": before.get("structure_stop")
        or (before.get("trail") or {}).get("structure_stop"),
        "structure_stop_after": after.get("structure_stop"),
        "kill_order_id": after.get("kill_order_id"),
        "lock_profit_armed": bool(after_trail.get("lock_profit_armed")),
    }


def migrate_book(
    state: dict[str, Any],
    *,
    symbol: str | None = None,
    quote_high: float | None = None,
    quote_last: float | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply plan_leg_migration to matching OPEN legs. Returns (new_state, reports)."""
    out = deepcopy(state)
    reports: list[dict[str, Any]] = []
    want = symbol.upper() if symbol else None
    for pos in out.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        if want and sym != want:
            continue
        profile = load_tsd_profile(sym)
        for i, leg in enumerate(list(pos.get("legs") or [])):
            if str(leg.get("status") or "OPEN").upper() == "CLOSED":
                continue
            report = plan_leg_migration(
                leg,
                quote_high=quote_high,
                quote_last=quote_last,
                profile=profile,
            )
            report["symbol"] = sym
            report["leg_index"] = i
            reports.append(report)
            if report.get("ok"):
                pos["legs"][i] = report["after"]
    return out, reports


def _print_reports(reports: list[dict[str, Any]], *, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "APPLY"
    if not reports:
        print(f"{mode}: no matching OPEN legs")
        return
    for r in reports:
        if not r.get("ok"):
            print(
                f"{mode} {r.get('symbol')} leg={r.get('leg_index')} "
                f"SKIP reason={r.get('reason')}"
            )
            continue
        print(
            f"{mode} {r.get('symbol')} leg={r.get('leg_index')} "
            f"structure_stop {r.get('structure_stop_before')} -> "
            f"{r.get('structure_stop_after')} "
            f"kill {r.get('kill_before')} -> {r.get('kill_after')} "
            f"kill_oid={r.get('kill_order_id')} "
            f"lock_profit={r.get('lock_profit_armed')}"
        )


def _sync_broker(reports: list[dict[str, Any]], *, dry_run: bool) -> None:
    """Cancel+replace broker kill via sync_kill_quantity (replacement first)."""
    from ib_insync import IB
    from tsd_scan_pipeline.tsd_exit import sync_kill_quantity

    tws_host = "127.0.0.1"
    tws_port = 7497
    # Distinct from trail monitor (95) so we do not kick the live loop.
    tws_client_id = 98

    ib = IB()
    ib.connect(tws_host, tws_port, clientId=tws_client_id)
    try:
        for r in reports:
            if not r.get("ok"):
                continue
            leg = r["after"]
            if _finite((leg.get("trail") or {}).get("kill_price")) <= 0:
                print(f"  {r['symbol']}: skip broker sync — no kill_price")
                continue
            sync_kill_quantity(ib, leg, r["symbol"], dry_run=dry_run)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear structure_stop on open Peak Hour longs; keep/raise kill.",
    )
    parser.add_argument("--symbol", default=None, help="Limit to one ticker (e.g. ATRC).")
    parser.add_argument("--book", default=None, help="Override tsd_book_state.json path.")
    parser.add_argument("--peak", type=float, default=None, help="Override peak/MFE high.")
    parser.add_argument("--last", type=float, default=None, help="Last/close for kill cap.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write book state. Default is dry-run.",
    )
    parser.add_argument(
        "--sync-broker",
        action="store_true",
        help="After book update, sync IB kill (cancel+replace at new price). "
        "Never cancels kill without a replacement price.",
    )
    args = parser.parse_args(argv)

    book_path = Path(args.book) if args.book else None
    state = load_state(book_path)
    if book_path is None:
        from state_paths import state_path

        book_path = state_path(TSD_STATE_FILE)

    new_state, reports = migrate_book(
        state,
        symbol=args.symbol,
        quote_high=args.peak,
        quote_last=args.last,
    )
    dry_run = not args.apply
    _print_reports(reports, dry_run=dry_run)

    if dry_run:
        print(f"No write. Re-run with --apply to update {book_path}")
        return 0 if reports else 1

    save_state(new_state, book_path)
    print(f"Wrote {book_path}")

    if args.sync_broker:
        _sync_broker(reports, dry_run=False)
    else:
        print(
            "Broker kill unchanged here. Run tsd_trail_monitor.py --once "
            "so sync_kill_quantity ratchets the working stop UP if needed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
