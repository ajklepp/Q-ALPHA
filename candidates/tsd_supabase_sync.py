"""
Q-ALPHA — sync TSD book (tsd_book_state.json) open legs to Supabase.

Separate from gap-agent paper_trades / trades table. Called from
tws_intraday_sync.py after agent marks (TWS SoT for current_price).

Usage:
  py -3 candidates/tsd_supabase_sync.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytz

CANDIDATES = Path(__file__).resolve().parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    full_slots_used,
    load_state,
    open_symbols,
)
from tsd_scan_pipeline.tsd_pool import load_pool  # noqa: E402

ET = pytz.timezone("America/New_York")
MARK_PX_TOL = 0.05
TSD_TABLE_MISSING_MSG = (
    "*** TSD: run candidates/sql/tsd_cloud.sql in Supabase SQL editor ***"
)

WATCHLIST_CACHE = (
    CANDIDATES / "tsd_scan_pipeline" / "results" / "last_watchlist.json"
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


def _tranche_summary(trail: dict[str, Any]) -> str:
    """Human-readable T1–T4 state for dashboard."""
    parts: list[str] = []
    for t in (trail or {}).get("tranches") or []:
        tid = str(t.get("id") or "?")
        if t.get("closed"):
            parts.append(f"{tid} closed")
        elif t.get("trailing"):
            parts.append(f"{tid} trail")
        else:
            parts.append(f"{tid} open")
    return " / ".join(parts) if parts else "—"


def flatten_open_legs(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten TSD book open legs into Supabase row dicts."""
    pos_meta = {
        str(p.get("symbol") or "").upper(): p
        for p in (book.get("positions") or [])
    }
    rows: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        if str(pos.get("status") or "").upper() != "OPEN":
            continue
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        t4_only = bool(pos.get("t4_only"))
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
                    "peak_high": _finite(trail.get("peak_high")),
                    "kill_pct": _finite(trail.get("kill_pct")),
                    "trail_pct": _finite(trail.get("trail_pct")),
                    "trading_day": int(trail.get("trading_day") or 0) or None,
                    "t4_only": t4_only,
                    "tranche_summary": _tranche_summary(trail),
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


def _assert_tsd_tables(sync) -> None:
    """Fail loudly when cloud schema is missing."""
    try:
        sync.client.table("tsd_positions").select("symbol").limit(1).execute()
    except Exception as exc:
        err = str(exc)
        if "PGRST205" in err or "tsd_positions" in err:
            raise RuntimeError(TSD_TABLE_MISSING_MSG) from exc
        raise


def _pool_snapshot_from_local(book: dict[str, Any]) -> dict[str, Any]:
    pool_doc = load_pool()
    cash = float(pool_doc.get("pool") or 0.0)
    deployed = float(pool_doc.get("deployed") or 0.0)
    starting = float(pool_doc.get("starting_pool") or 3000.0)
    return {
        "snapshot_date": datetime.now(ET).strftime("%Y-%m-%d"),
        "pool": round(cash, 2),
        "deployed": round(deployed, 2),
        "open_positions": full_slots_used(book),
        "open_names": len(open_symbols(book)),
        "starting_pool": starting,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def sync_tsd_watchlist_from_file(
    *,
    open_symbols_set: set[str] | None = None,
    trade_symbols: set[str] | None = None,
) -> int:
    """
    Upsert current TSD watch-10 from last_watchlist.json (post-scan).
  Returns row count or 0 if cache missing.
    """
    if not WATCHLIST_CACHE.exists():
        return 0
    try:
        payload = json.loads(WATCHLIST_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    watch_rows = payload.get("rows") or []
    scan_at = str(payload.get("updated_at") or datetime.now(ET).isoformat())
    return sync_tsd_watchlist_to_supabase(
        watch_rows,
        scan_at=scan_at,
        open_symbols_set=open_symbols_set or set(),
        trade_symbols=trade_symbols or set(),
    )


def sync_tsd_watchlist_to_supabase(
    watch_rows: list[dict[str, Any]],
    *,
    scan_at: str,
    open_symbols_set: set[str] | None = None,
    trade_symbols: set[str] | None = None,
) -> int:
    """Replace-all current TSD watchlist in Supabase."""
    from supabase_sync import SupabaseSync

    opens = {s.upper() for s in (open_symbols_set or set())}
    trades = {s.upper() for s in (trade_symbols or set())}
    rows: list[dict[str, Any]] = []
    for i, w in enumerate(watch_rows[:10], start=1):
        sym = str(w.get("symbol") or "").upper()
        if not sym:
            continue
        prof = w.get("profiler") or {}
        in_book = sym in opens
        trade_pick = sym in trades
        if in_book:
            status_label = "In Book"
        elif trade_pick:
            status_label = "Trade pick"
        elif w.get("profiler_pass"):
            status_label = "Profiler OK"
        else:
            status_label = "Watching"
        rows.append(
            {
                "symbol": sym,
                "rank": int(w.get("rank") or i),
                "scan_score": _finite(w.get("scan_score")),
                "trend_strength": _finite(w.get("trend_strength")),
                "mfi": _finite(w.get("mfi")),
                "buy_signal": bool(w.get("buy_signal")),
                "profiler_pass": bool(w.get("profiler_pass")),
                "in_book": in_book,
                "trade_pick": trade_pick,
                "status_label": status_label,
                "entry_price": _finite(w.get("close")),
                "kill_price": _finite(w.get("kill_price")),
                "scan_at": scan_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if not rows:
        return 0
    sync = SupabaseSync()
    sync.replace_tsd_watchlist(rows)
    print(f"  TSD watchlist upserted {len(rows)} rows")
    return len(rows)


def sync_tsd_positions_to_supabase(
    ib=None,
    *,
    mark_fn: Callable | None = None,
) -> dict[str, Any]:
    """
    Upsert open TSD legs + pool snapshot to Supabase; prune stale OPEN rows.
    Returns summary dict with upserted count and verify errors.
    """
    from supabase_sync import SupabaseSync

    summary: dict[str, Any] = {
        "upserted": 0,
        "pruned": 0,
        "pool_synced": False,
        "watchlist_synced": 0,
        "verify_errors": [],
    }

    book = load_state()
    rows = flatten_open_legs(book)
    if ib is not None and mark_fn is not None and rows:
        apply_tws_marks(rows, ib, mark_fn)

    try:
        sync = SupabaseSync()
        _assert_tsd_tables(sync)
    except RuntimeError as exc:
        print(f"  {exc}")
        summary["verify_errors"].append(str(exc))
        return summary
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

    try:
        pool_snap = _pool_snapshot_from_local(book)
        sync.upsert_tsd_pool_snapshot(pool_snap)
        summary["pool_synced"] = True
        print(
            f"  >>> TSD pool cash=${pool_snap['pool']:.2f} "
            f"deployed=${pool_snap['deployed']:.2f} "
            f"opens={pool_snap['open_positions']} "
            f"names={pool_snap['open_names']}"
        )
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_pool:{exc}")

    try:
        open_set = set(open_symbols(book))
        summary["watchlist_synced"] = sync_tsd_watchlist_from_file(
            open_symbols_set=open_set,
        )
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_watchlist:{exc}")

    verify_errors = _verify_tsd_supabase_rows(rows)
    summary["verify_errors"].extend(verify_errors)

    try:
        sync.log_health(
            "tsd_sync",
            "OK" if not summary["verify_errors"] else "WARN",
            f"upserted={summary['upserted']} pruned={summary['pruned']} "
            f"errors={len(summary['verify_errors'])}",
        )
    except Exception:
        pass

    return summary


def _verify_tsd_supabase_rows(local_rows: list[dict[str, Any]]) -> list[str]:
    """Confirm Cloud px matches local/TWS after upsert."""
    errors: list[str] = []
    try:
        from supabase_sync import SupabaseSync

        sync = SupabaseSync()
        print("\n  TSD Supabase verify:")
        if not local_rows:
            print("    (no open TSD legs locally)")
            return errors
        for row in local_rows:
            symbol = str(row.get("symbol") or "").upper()
            leg_opened_at = str(row.get("leg_opened_at") or "")
            result = (
                sync.client.table("tsd_positions")
                .select(
                    "symbol,status,shares,current_price,kill_price,last_updated"
                )
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
                msg = (
                    f"tsd_mark_mismatch:{symbol} "
                    f"cloud={cloud_px} local={local_px:.2f}"
                )
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
        f"pool={summary.get('pool_synced')} "
        f"errors={summary.get('verify_errors') or 'none'}"
    )
    return 1 if summary.get("verify_errors") else 0


if __name__ == "__main__":
    sys.exit(main())
