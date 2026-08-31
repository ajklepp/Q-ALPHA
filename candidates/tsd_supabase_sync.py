"""
Q-ALPHA — sync TSD book (tsd_book_state.json) open legs to Supabase.

Separate from gap-agent paper_trades / trades table. Called from
tws_intraday_sync.py after agent marks (TWS SoT for current_price).

Usage:
  py -3 candidates/tsd_supabase_sync.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytz

CANDIDATES = Path(__file__).resolve().parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from tsd_scan_pipeline.tsd_capacity import load_state  # noqa: E402

ET = pytz.timezone("America/New_York")
MARK_PX_TOL = 0.05
TSD_POSITION_FIELDS = (
    "symbol",
    "entry_date",
    "leg_opened_at",
    "entry_price",
    "shares",
    "kill_price",
    "current_price",
    "pnl_dollars",
    "pnl_pct",
    "status",
    "last_bar_time",
    "scan_score",
    "last_updated",
)


def _finite(val: Any) -> float | None:
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def flatten_open_legs(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten TSD book open legs into Supabase row dicts."""
    rows: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        if str(pos.get("status") or "").upper() != "OPEN":
            continue
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        for leg in pos.get("legs") or []:
            if str(leg.get("status") or "").upper() != "OPEN":
                continue
            trail = leg.get("trail") or {}
            leg_time = str(leg.get("time") or trail.get("opened_at") or "")
            entry_date = leg_time[:10] if leg_time else ""
            if not entry_date:
                entry_date = datetime.now(ET).strftime("%Y-%m-%d")
            entry_price = _finite(trail.get("entry_price")) or _finite(leg.get("price"))
            shares = int(leg.get("shares") or 0)
            kill_price = _finite(trail.get("kill_price"))
            current_price = _finite(trail.get("last_close")) or entry_price
            scan_score = _finite(leg.get("scan_score"))

            pnl_dollars = None
            pnl_pct = None
            if entry_price and shares > 0 and current_price:
                pnl_dollars = round((current_price - entry_price) * shares, 2)
                pnl_pct = round((current_price - entry_price) / entry_price, 4)

            rows.append(
                {
                    "symbol": symbol,
                    "entry_date": entry_date,
                    "leg_opened_at": leg_time or f"{entry_date}T00:00:00",
                    "entry_price": entry_price,
                    "shares": shares,
                    "kill_price": kill_price,
                    "current_price": current_price,
                    "pnl_dollars": pnl_dollars,
                    "pnl_pct": pnl_pct,
                    "status": "OPEN",
                    "last_bar_time": trail.get("last_bar_time"),
                    "scan_score": scan_score,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
    return rows


def apply_tws_marks(
    rows: list[dict[str, Any]],
    ib,
    mark_fn: Callable,
) -> None:
    """Refresh current_price / PnL from TWS snapshot marks."""
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        mark = mark_fn(ib, symbol)
        if mark is None or mark <= 0:
            continue
        entry = _finite(row.get("entry_price")) or 0.0
        shares = int(row.get("shares") or 0)
        row["current_price"] = round(mark, 4)
        if entry > 0 and shares > 0:
            row["pnl_dollars"] = round((mark - entry) * shares, 2)
            row["pnl_pct"] = round((mark - entry) / entry, 4)
        row["last_updated"] = datetime.now(timezone.utc).isoformat()


def sync_tsd_positions_to_supabase(
    ib=None,
    *,
    mark_fn: Callable | None = None,
) -> dict[str, Any]:
    """
    Upsert open TSD legs to Supabase; prune stale OPEN rows.
    Returns summary dict with upserted count and verify errors.
    """
    from supabase_sync import SupabaseSync

    summary: dict[str, Any] = {
        "upserted": 0,
        "pruned": 0,
        "verify_errors": [],
    }

    book = load_state()
    rows = flatten_open_legs(book)
    if ib is not None and mark_fn is not None and rows:
        apply_tws_marks(rows, ib, mark_fn)

    try:
        sync = SupabaseSync()
    except Exception as exc:
        print(f"  TSD Supabase sync skipped: {exc}")
        summary["verify_errors"].append(f"sync_init:{exc}")
        return summary

    open_keys: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row["symbol"]), str(row["leg_opened_at"]))
        open_keys.append(key)
        try:
            sync.upsert_tsd_position(row)
            summary["upserted"] += 1
            print(
                f"  TSD upsert {row['symbol']} "
                f"px={row.get('current_price')} "
                f"kill={row.get('kill_price')} "
                f"shares={row.get('shares')}"
            )
        except Exception as exc:
            msg = f"tsd_upsert:{row.get('symbol')}:{exc}"
            summary["verify_errors"].append(msg)
            print(f"  *** TSD SYNC FAILED {row.get('symbol')}: {exc} ***")

    try:
        summary["pruned"] = sync.prune_stale_tsd_positions(open_keys)
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_prune:{exc}")

    verify_errors = _verify_tsd_supabase_rows(rows)
    summary["verify_errors"].extend(verify_errors)
    return summary


def _verify_tsd_supabase_rows(local_rows: list[dict[str, Any]]) -> list[str]:
    """Confirm Cloud px matches local/TWS after upsert."""
    if not local_rows:
        return []
    errors: list[str] = []
    try:
        from supabase_sync import SupabaseSync

        sync = SupabaseSync()
        print("\n  TSD Supabase verify:")
        for row in local_rows:
            symbol = str(row.get("symbol") or "").upper()
            leg_opened_at = str(row.get("leg_opened_at") or "")
            result = (
                sync.client.table("tsd_positions")
                .select("symbol,status,shares,current_price,kill_price,last_updated")
                .eq("symbol", symbol)
                .eq("leg_opened_at", leg_opened_at)
                .limit(1)
                .execute()
            )
            cloud = (result.data or [{}])[0] if result.data else {}
            if not cloud:
                msg = f"tsd_verify_missing:{symbol}"
                errors.append(msg)
                print(f"    {symbol}: (no row)")
                continue
            local_px = _finite(row.get("current_price"))
            cloud_px = _finite(cloud.get("current_price"))
            mismatch = ""
            if (
                local_px is not None
                and cloud_px is not None
                and abs(local_px - cloud_px) > MARK_PX_TOL
            ):
                msg = f"tsd_mark_mismatch:{symbol} cloud={cloud_px} local={local_px:.2f}"
                errors.append(msg)
                mismatch = " *** MISMATCH ***"
            print(
                f"    {symbol}: status={cloud.get('status')} "
                f"shares={cloud.get('shares')} "
                f"px={cloud.get('current_price')} "
                f"kill={cloud.get('kill_price')}"
                f"{mismatch}"
            )
    except Exception as exc:
        errors.append(f"tsd_verify:{exc}")
        print(f"  TSD Supabase verify warn: {exc}")
    return errors


def main() -> int:
    summary = sync_tsd_positions_to_supabase()
    print(
        f"TSD sync done upserted={summary.get('upserted')} "
        f"pruned={summary.get('pruned')} "
        f"errors={summary.get('verify_errors') or 'none'}"
    )
    return 1 if summary.get("verify_errors") else 0


if __name__ == "__main__":
    sys.exit(main())
