#!/usr/bin/env python3
"""
One-shot: cancel duplicate protective SELL STP / STP LMT kills on paper TWS.

WHY: ATRC 2026-09 left three identical SELL STP LMT @53.13 after the kill
rewrite storm. PR #25 dedupes on the next trail tick, but Aaron cannot click
Cancel in TWS — this script clears extras NOW and leaves one good kill when
still long.

Usage (TWS paper API on 7497; prefer repo venv Python 3.12):
  python candidates/uts_v2/cancel_orphan_kills.py --dry-run --symbol ATRC
  python candidates/uts_v2/cancel_orphan_kills.py --live --symbol ATRC
  python candidates/uts_v2/cancel_orphan_kills.py --live --symbol ATRC \\
      --host 127.0.0.1 --port 7497

Safety: paper port 7497 only unless --i-really-mean-live-port.
Does NOT flatten the long. Does NOT place new kills.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

# --- Connection pins (paper only) ---------------------------------------------
TWS_HOST_DEFAULT = "127.0.0.1"
PAPER_PORT = 7497
LIVE_PORT = 7496
# Dedicated one-shot client (not trail 85/86, watch, scan, L2 72, options 71).
TWS_CLIENT_ID = 92
# Fallback owners when cancel returns Error 10147 (foreign clientId).
OWNER_CLIENT_IDS = (0, 85, 86, 91, 92, 93, 94, 95, 96, 97)
CONNECT_TIMEOUT_SEC = 12.0
TCP_PROBE_TIMEOUT_SEC = 1.5
SETTLE_SEC = 2.0
_MAX_SANE_PRICE = 1.0e7


def _prefer_venv_hint() -> None:
    """ib_insync on system Python 3.14 breaks; repo venv is 3.12."""
    if sys.version_info >= (3, 14):
        print(
            "WARN: Python >=3.14 often breaks ib_insync connect. "
            "Prefer repo venv python 3.12."
        )


def _guard_port(port: int, *, allow_live_port: bool) -> None:
    """Refuse live 7496 unless explicitly authorized."""
    if port == PAPER_PORT:
        return
    if port == LIVE_PORT and allow_live_port:
        print("WARNING: live port 7496 authorized via --i-really-mean-live-port")
        return
    raise SystemExit(
        f"REFUSE port {port}: paper cancel uses {PAPER_PORT} only "
        f"(pass --i-really-mean-live-port for {LIVE_PORT})"
    )


def tcp_reachable(host: str, port: int, *, timeout: float = TCP_PROBE_TIMEOUT_SEC) -> bool:
    """True when TCP connect succeeds (TWS API socket open)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sane_price(value: Any) -> float:
    """Return a usable USD price, or 0 if missing / IB unset sentinel."""
    try:
        px = float(value)
    except (TypeError, ValueError):
        return 0.0
    if px <= 0 or px > _MAX_SANE_PRICE:
        return 0.0
    return px


def local_order_stop_price(order: Any) -> float:
    """
    Stop trigger from an IBKR order (auxPrice first).

    Mirrors tsd_exit.order_stop_price so keep-oid selection works before
    importing the full TSD stack (cloud TCP-only probes).
    """
    if order is None:
        return 0.0
    for attr in ("auxPrice", "stopPrice"):
        px = _sane_price(getattr(order, attr, None))
        if px > 0:
            return px
    return 0.0


def choose_keep_oid(trades: list[Any], *, prefer_oid: int | None = None) -> int | None:
    """
    Pick the single kill to keep among working protective stops.

    Preference order:
      1. Explicit --keep-oid if still working
      2. Highest stop trigger (best long-side protection)
      3. Lowest orderId (stable / oldest)
    """
    if not trades:
        return None
    by_oid: dict[int, Any] = {}
    for trade in trades:
        try:
            oid = int(trade.order.orderId or 0)
        except (TypeError, ValueError):
            continue
        if oid > 0:
            by_oid[oid] = trade
    if not by_oid:
        return None
    if prefer_oid is not None and int(prefer_oid) in by_oid:
        return int(prefer_oid)

    def _key(oid: int) -> tuple[float, int]:
        trade = by_oid[oid]
        stop = local_order_stop_price(trade.order)
        return (-stop, oid)

    return sorted(by_oid.keys(), key=_key)[0]


def _load_ib_stack() -> tuple[Any, Any, Any, Any]:
    """Lazy-import ib_insync + tsd_exit cancel helpers (needs pytz/ib_insync)."""
    try:
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass
    from ib_insync import IB  # noqa: WPS433

    from tsd_scan_pipeline.tsd_exit import (  # noqa: WPS433
        cancel_extra_protective_kills,
        cancel_order_safe,
        _working_protective_kills,
    )

    return IB, cancel_extra_protective_kills, cancel_order_safe, _working_protective_kills


def _position_qty(ib: Any, symbol: str) -> int:
    """Signed STK qty for symbol (0 if flat / missing)."""
    sym = symbol.upper()
    try:
        for pos in ib.positions():
            if str(getattr(pos.contract, "symbol", "") or "").upper() != sym:
                continue
            sec = str(getattr(pos.contract, "secType", "") or "").upper()
            if sec and sec != "STK":
                continue
            return int(pos.position or 0)
    except Exception as exc:
        print(f"  positions unavailable: {exc}")
    return 0


def _describe_kill(trade: Any) -> str:
    """One-line summary of a protective stop (no secrets)."""
    o = trade.order
    stop = local_order_stop_price(o)
    try:
        lmt = float(getattr(o, "lmtPrice", 0) or 0)
    except (TypeError, ValueError):
        lmt = 0.0
    status = str(trade.orderStatus.status or "")
    cid = int(getattr(o, "clientId", 0) or 0)
    qty = int(getattr(o, "totalQuantity", 0) or 0)
    filled = int(getattr(trade.orderStatus, "filled", 0) or 0)
    return (
        f"oid={int(o.orderId or 0)} client={cid} "
        f"{o.action} {o.orderType} stop={stop:.2f} lmt={lmt:.2f} "
        f"qty={filled}/{qty} status={status}"
    )


def list_kills(ib: Any, symbol: str, working_fn: Any) -> list[Any]:
    """Refresh open orders and return working protective kills for symbol."""
    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.4)
    except Exception as exc:
        print(f"  reqAllOpenOrders warn: {exc}")
    return list(working_fn(ib, symbol))


def connect_ib(IB: Any, host: str, port: int, client_id: int) -> Any:
    """Connect to TWS; raises on failure."""
    ib = IB()
    ib.connect(host, port, clientId=client_id, timeout=CONNECT_TIMEOUT_SEC)
    return ib


def cancel_extras_with_owner_fallback(
    host: str,
    port: int,
    symbol: str,
    keep_oid: int,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """
    Cancel extras via cancel_extra_protective_kills; retry as owner clientIds
    if foreign cancels leave orphans (Error 10147 path).
    """
    IB, cancel_extra, cancel_safe, working_fn = _load_ib_stack()
    report: dict[str, Any] = {
        "cancelled": [],
        "kept": keep_oid,
        "remaining": [],
        "blocked": [],
        "client_ids_tried": [],
        "position_qty": None,
    }
    primary = connect_ib(IB, host, port, TWS_CLIENT_ID)
    report["client_ids_tried"].append(TWS_CLIENT_ID)
    leftovers: list[int] = []
    try:
        report["position_qty"] = _position_qty(primary, symbol)
        before = list_kills(primary, symbol, working_fn)
        print(f"Working protective kills for {symbol}: {len(before)}")
        for t in before:
            print(f"  {_describe_kill(t)}")

        if dry_run:
            would = [
                int(t.order.orderId or 0)
                for t in before
                if int(t.order.orderId or 0) != int(keep_oid)
            ]
            report["cancelled"] = would
            report["remaining"] = [
                int(t.order.orderId or 0)
                for t in before
                if int(t.order.orderId or 0) == int(keep_oid)
            ] or ([int(keep_oid)] if keep_oid else [])
            print(f"DRY_RUN would cancel {would} keep={keep_oid}")
            return report

        cancelled = cancel_extra(primary, symbol, keep_oid, dry_run=False)
        report["cancelled"].extend(int(x) for x in cancelled)
        time.sleep(SETTLE_SEC)
        left = list_kills(primary, symbol, working_fn)
        leftovers = [
            int(t.order.orderId or 0)
            for t in left
            if int(t.order.orderId or 0) != int(keep_oid)
        ]
    finally:
        try:
            primary.disconnect()
        except Exception:
            pass

    for cid in OWNER_CLIENT_IDS:
        if not leftovers:
            break
        if cid == TWS_CLIENT_ID:
            continue
        print(f"  retry cancel leftovers {leftovers} as clientId={cid}")
        try:
            ib = connect_ib(IB, host, port, cid)
        except Exception as exc:
            print(f"  connect clientId={cid} failed: {exc}")
            continue
        report["client_ids_tried"].append(cid)
        try:
            for oid in list(leftovers):
                if cancel_safe(ib, oid):
                    if oid not in report["cancelled"]:
                        report["cancelled"].append(oid)
            time.sleep(SETTLE_SEC)
            left = list_kills(ib, symbol, working_fn)
            leftovers = [
                int(t.order.orderId or 0)
                for t in left
                if int(t.order.orderId or 0) != int(keep_oid)
            ]
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    try:
        ib = connect_ib(IB, host, port, TWS_CLIENT_ID)
        try:
            final = list_kills(ib, symbol, working_fn)
            report["remaining"] = [int(t.order.orderId or 0) for t in final]
            report["blocked"] = [
                oid for oid in report["remaining"] if oid != int(keep_oid)
            ]
            print(f"Final kills for {symbol}: {len(final)}")
            for t in final:
                print(f"  {_describe_kill(t)}")
        finally:
            ib.disconnect()
    except Exception as exc:
        report["blocked"].append(f"final_read_failed:{exc}")
        print(f"  final read failed: {exc}")

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI for paper-only orphan kill cleanup."""
    p = argparse.ArgumentParser(
        description=(
            "Cancel duplicate protective SELL STP/STP LMT kills on TWS paper "
            f"{PAPER_PORT}; leave one good kill if still long."
        )
    )
    p.add_argument("--symbol", default="ATRC", help="Symbol to clean (default ATRC).")
    p.add_argument(
        "--host",
        default=TWS_HOST_DEFAULT,
        help="TWS host (default 127.0.0.1).",
    )
    p.add_argument("--port", type=int, default=PAPER_PORT, help="TWS port (7497 only).")
    p.add_argument(
        "--also-hosts",
        default="",
        help="Comma-separated extra hosts to probe (paper 7497).",
    )
    p.add_argument("--keep-oid", type=int, default=0, help="Force keep this orderId.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="List / plan only.")
    mode.add_argument("--live", action="store_true", help="Cancel extras for real.")
    p.add_argument(
        "--i-really-mean-live-port",
        action="store_true",
        help="Allow port 7496 (default refused).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Probe hosts, connect paper TWS, cancel duplicate ATRC-style kills."""
    _prefer_venv_hint()
    args = parse_args(argv)
    _guard_port(int(args.port), allow_live_port=bool(args.i_really_mean_live_port))

    symbol = str(args.symbol or "ATRC").upper()
    hosts = [str(args.host).strip()]
    for part in str(args.also_hosts or "").split(","):
        h = part.strip()
        if h and h not in hosts:
            hosts.append(h)

    print(
        f"cancel_orphan_kills symbol={symbol} port={args.port} "
        f"mode={'DRY_RUN' if args.dry_run else 'LIVE'} hosts={hosts}"
    )

    reachable: list[str] = []
    for host in hosts:
        ok = tcp_reachable(host, int(args.port))
        print(f"  TCP {host}:{args.port} -> {'OPEN' if ok else 'CLOSED'}")
        if ok:
            reachable.append(host)

    if not reachable:
        print(
            "BLOCKED: no TWS API socket reachable. "
            "Cloud/Modal cannot see laptop 127.0.0.1:7497. "
            "Run on the Peak Hour laptop (TWS paper open, API 7497) or a "
            "self-hosted Cursor worker on that machine:\n"
            f"  python candidates/uts_v2/cancel_orphan_kills.py "
            f"--{'dry-run' if args.dry_run else 'live'} --symbol {symbol}"
        )
        return 2

    last_err: Exception | None = None
    for host in reachable:
        print(f"Connecting {host}:{args.port} clientId={TWS_CLIENT_ID} ...")
        try:
            IB, _, _, working_fn = _load_ib_stack()
            ib = connect_ib(IB, host, int(args.port), TWS_CLIENT_ID)
        except Exception as exc:
            last_err = exc
            print(f"  connect failed: {exc}")
            continue
        try:
            kills = list_kills(ib, symbol, working_fn)
            qty = _position_qty(ib, symbol)
            print(f"Position {symbol} qty={qty} (long-only; do not flatten)")
            prefer = int(args.keep_oid) if int(args.keep_oid or 0) > 0 else None
            keep = choose_keep_oid(kills, prefer_oid=prefer)
            if keep is None:
                print(f"No working protective kills for {symbol} — nothing to cancel")
                return 0
            if len(kills) == 1:
                print(f"Already clean: single kill kept oid={keep}")
                print(f"  {_describe_kill(kills[0])}")
                return 0
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

        report = cancel_extras_with_owner_fallback(
            host,
            int(args.port),
            symbol,
            keep,
            dry_run=bool(args.dry_run),
        )
        print("--- summary ---")
        print(f"  kept={report['kept']}")
        print(f"  cancelled={report['cancelled']}")
        print(f"  remaining={report['remaining']}")
        print(f"  blocked={report['blocked']}")
        print(f"  position_qty={report['position_qty']}")
        print(f"  client_ids_tried={report['client_ids_tried']}")
        if report["blocked"]:
            return 3
        return 0

    print(f"BLOCKED: all reachable hosts failed IB connect ({last_err})")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
