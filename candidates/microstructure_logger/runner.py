"""
CLI entry for the research-only L2 + tape logger.

WHY: Local paper TWS (clientId 72) only. No Peak Hour imports, no orders,
no cloud Gateway. Feature C (confirms) and D (options) are deferred.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import (
    ABSOLUTE_MAX_DEPTH_SYMBOLS,
    BOOK_SNAPSHOT_SEC,
    DEFAULT_TOP_N,
    DEPTH_SOURCE,
    IDLE_POLL_SEC,
    JSONL_ROOT_REL,
    PROBE_DEFAULT_SYMBOLS,
    PROBE_MAX_SYMBOLS,
    SOURCE_L2,
    SOURCE_TAPE,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_PAPER_PORT,
)
from .features import BookHistory, TapeEngine, book_features
from .ib_worker import IBWorker, assert_paper_endpoint
from .jsonl_writer import JsonlWriter
from .schema import build_row
from .session import session_tag, should_stream, to_et
from .universe import repo_root, resolve_universe


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI: --symbols override, --top N, RTH-primary unless --allow-extended."""
    p = argparse.ArgumentParser(
        description=(
            "READ-ONLY Peak Hour microstructure logger (Features A book + B tape). "
            f"TWS paper {TWS_HOST}:{TWS_PAPER_PORT} clientId {TWS_CLIENT_ID}."
        )
    )
    p.add_argument(
        "--symbols",
        default="",
        help="Comma-separated override (skips Peak Hour artifacts).",
    )
    p.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=(
            f"Max names to subscribe (default {DEFAULT_TOP_N}; "
            f"--once/--probe capped at {PROBE_MAX_SYMBOLS})."
        ),
    )
    p.add_argument(
        "--allow-extended",
        action="store_true",
        help="Stream PRE/POST as well as RTH (default: idle outside RTH).",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help=(
            "Write one L2 + one tape row per symbol then exit. "
            f"Caps depth to {PROBE_MAX_SYMBOLS} names; defaults to "
            f"{','.join(PROBE_DEFAULT_SYMBOLS)} if --symbols omitted "
            "(does not pull Cap watchlists)."
        ),
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help=(
            f"L2 subscription smoke: max {PROBE_MAX_SYMBOLS} depth names "
            f"(default {','.join(PROBE_DEFAULT_SYMBOLS)}). Implies --once."
        ),
    )
    p.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="If >0, run this many seconds then exit.",
    )
    p.add_argument(
        "--no-connect",
        action="store_true",
        help="Do not open TWS — resolve universe and write null-schema rows (dry structure).",
    )
    p.add_argument(
        "--host",
        default=TWS_HOST,
        help="TWS host (local paper only; default 127.0.0.1).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=TWS_PAPER_PORT,
        help="TWS port (paper 7497 only).",
    )
    p.add_argument(
        "--log-root",
        default="",
        help="JSONL root (default <repo>/logs/microstructure).",
    )
    return p.parse_args(argv)


def _parse_symbols(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _merge_features(book: dict[str, Any], tape: dict[str, Any]) -> dict[str, Any]:
    """Combine A+B dicts; levels_n stays on the book side."""
    out = dict(book)
    out.update(tape)
    return out


def _write_pair(
    writer: JsonlWriter,
    *,
    symbol: str,
    now: datetime,
    book: dict[str, Any],
    tape: dict[str, Any],
    depth_source: str,
) -> None:
    """Emit one qalpha_l2 and one qalpha_tape row; both carry TEST-01 keys."""
    features = _merge_features(book, tape)
    levels_n = features.pop("levels_n", None)
    tag = session_tag(now)
    ts = now.astimezone(timezone.utc)
    common = dict(
        ts_utc=ts.isoformat().replace("+00:00", "Z"),
        symbol=symbol,
        session_tag=tag,
        depth_source=depth_source or DEPTH_SOURCE,
        levels_n=levels_n,
        features=features,
    )
    writer.write(build_row(source=SOURCE_L2, **common))
    writer.write(build_row(source=SOURCE_TAPE, **common))


def _dry_cycle(symbols: list[str], writer: JsonlWriter) -> int:
    """Write null-valued schema rows without IB — VM / structure check."""
    now = _utcnow()
    empty_book = {
        "bid1": None,
        "ask1": None,
        "bid_sz1": None,
        "ask_sz1": None,
        "mid": None,
        "microprice": None,
        "spread_bps": None,
        "imbalance_l1": None,
        "imbalance_l3": None,
        "book_pressure_delta_1s": None,
        "book_pressure_delta_5s": None,
        "quote_flicker": None,
        "levels_n": 0,
    }
    empty_tape = {
        "print_vwap_5s": None,
        "print_imb_5s": None,
        "large_print_flag": None,
        "uptick_ratio_30s": None,
        "tape_burst_z": None,
    }
    for symbol in symbols:
        _write_pair(
            writer,
            symbol=symbol,
            now=now,
            book=empty_book,
            tape=empty_tape,
            depth_source=DEPTH_SOURCE,
        )
        print(f"  dry row {symbol} session={session_tag(now)} depth_source={DEPTH_SOURCE}")
    return 0


def apply_depth_subscribe_limits(args: argparse.Namespace) -> argparse.Namespace:
    """
    Cap L2 depth subscriptions so probes never request Cap-scale lists.

    WHY: Paper SMART depth (reqMktDepth) is fragile — 100+ names (or even a
    full Cap watchlist) can fail. --once / --probe stay at 1–3 symbols.
    Continuous logging still uses DEFAULT_TOP_N but never above the hard max.
    """
    probeish = bool(getattr(args, "probe", False) or getattr(args, "once", False))
    if getattr(args, "probe", False):
        args.once = True
    top = max(1, int(args.top))
    if probeish:
        if top > PROBE_MAX_SYMBOLS:
            print(
                f"  probe/once depth cap: --top {top} -> {PROBE_MAX_SYMBOLS} "
                "(few symbols at a time)"
            )
            top = PROBE_MAX_SYMBOLS
        if not (args.symbols or "").strip():
            args.symbols = ",".join(PROBE_DEFAULT_SYMBOLS)
            print(
                f"  probe/once default symbols={args.symbols} "
                "(skip Cap watchlist artifacts)"
            )
    if top > ABSOLUTE_MAX_DEPTH_SYMBOLS:
        print(
            f"  absolute depth cap: --top {top} -> {ABSOLUTE_MAX_DEPTH_SYMBOLS}"
        )
        top = ABSOLUTE_MAX_DEPTH_SYMBOLS
    args.top = top
    # Also truncate an explicit --symbols list that exceeds the capped top.
    parsed = _parse_symbols(args.symbols)
    if parsed and len(parsed) > top:
        kept = parsed[:top]
        print(
            f"  truncating --symbols {len(parsed)} -> {top}: {','.join(kept)}"
        )
        args.symbols = ",".join(kept)
    return args


def run(args: argparse.Namespace) -> int:
    """Resolve universe, optionally connect clientId 72, log A+B JSONL."""
    assert_paper_endpoint(args.host, int(args.port), TWS_CLIENT_ID)
    args = apply_depth_subscribe_limits(args)
    root = repo_root()
    uni = resolve_universe(
        root=root,
        symbols=_parse_symbols(args.symbols),
        top_n=int(args.top),
    )
    log_root = Path(args.log_root) if args.log_root else (root / JSONL_ROOT_REL)
    writer = JsonlWriter(log_root)

    print("Q-ALPHA microstructure logger — READ-ONLY research (A book + B tape)")
    print(f"  clientId={TWS_CLIENT_ID} host={args.host}:{int(args.port)} (paper)")
    print(f"  depth_source={DEPTH_SOURCE} (never TotalView; Error 2152 possible)")
    print(f"  jsonl={log_root}")
    print(f"  symbols={','.join(uni.symbols)} fallback={uni.used_fallback}")
    if uni.used_fallback:
        print(f"  {uni.fallback_reason}")
    if uni.artifacts_seen:
        print(f"  artifacts={uni.artifacts_seen}")
    print("  Peak Hour / TSD code: not imported, not mutated")
    print("  deferred: Feature C confirms, Feature D options")

    if args.no_connect:
        print("  --no-connect: skipping TWS (dry structure)")
        try:
            return _dry_cycle(uni.symbols, writer)
        finally:
            writer.close()

    now = _utcnow()
    streaming = should_stream(now, allow_extended=bool(args.allow_extended))
    # --probe must still hit TWS even overnight (CLOSED) so smoke gets a real
    # connect result instead of a silent session skip. Continuous/--once
    # remain RTH-primary (plus PRE/POST with --allow-extended).
    if not streaming and args.once and not bool(getattr(args, "probe", False)):
        print(
            f"  skip: session={session_tag(now)} ET={to_et(now).strftime('%H:%M:%S')} "
            f"(RTH-primary; pass --allow-extended to stream)"
        )
        writer.close()
        return 0
    if not streaming and bool(getattr(args, "probe", False)):
        print(
            f"  probe: session={session_tag(now)} ET={to_et(now).strftime('%H:%M:%S')} "
            "outside PRE/RTH/POST — still connecting for L2 smoke"
        )

    stop = {"flag": False}

    def _handle_stop(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    worker: IBWorker | None = None
    histories = {s: BookHistory() for s in uni.symbols}
    tapes = {s: TapeEngine() for s in uni.symbols}
    started = time.monotonic()
    snapshots = 0

    try:
        while not stop["flag"]:
            now = _utcnow()
            if args.seconds > 0 and (time.monotonic() - started) >= args.seconds:
                break
            streaming_now = should_stream(now, allow_extended=bool(args.allow_extended))
            probe_force = bool(getattr(args, "probe", False))
            if not streaming_now and not probe_force:
                if worker is not None:
                    print(f"  idle session={session_tag(now)} — cancelling subscriptions")
                    worker.cancel_subscriptions()
                    worker.stop()
                    worker = None
                if args.once:
                    break
                time.sleep(IDLE_POLL_SEC)
                continue

            if worker is None:
                print(
                    f"  connecting {args.host}:{int(args.port)} clientId={TWS_CLIENT_ID} "
                    f"readonly session={session_tag(now)}"
                )
                worker = IBWorker(host=args.host, port=int(args.port), client_id=TWS_CLIENT_ID)
                worker.start()
                subscribed = worker.subscribe(uni.symbols)
                if not subscribed:
                    print("  no symbols subscribed — exiting")
                    return 2
                # Brief bound wait so first snapshot is not all-null.
                time.sleep(min(BOOK_SNAPSHOT_SEC, 1.0))

            now = _utcnow()
            for symbol in uni.symbols:
                book_quote = worker.latest_book(symbol)
                mid_hint = None
                if book_quote is not None and book_quote.bids and book_quote.asks:
                    mid_hint = (book_quote.bids[0].price + book_quote.asks[0].price) / 2.0
                for rec in worker.drain_prints(symbol):
                    tapes[symbol].add_print(rec.ts, rec.price, rec.size, mid=mid_hint)
                if book_quote is None:
                    book_feat = book_features(
                        ts=now,
                        bids=[],
                        asks=[],
                        history=histories[symbol],
                    )
                    depth_source = worker.depth_source
                else:
                    book_feat = book_features(
                        ts=book_quote.ts,
                        bids=book_quote.bids,
                        asks=book_quote.asks,
                        history=histories[symbol],
                    )
                    depth_source = book_quote.depth_source
                tape_feat = tapes[symbol].features(now)
                _write_pair(
                    writer,
                    symbol=symbol,
                    now=now,
                    book=book_feat,
                    tape=tape_feat,
                    depth_source=depth_source,
                )
            snapshots += 1
            if snapshots == 1 or snapshots % 25 == 0:
                extra = ""
                if worker is not None and worker.error_2152:
                    extra = " error_2152=yes"
                print(f"  snapshot n={snapshots} session={session_tag(now)}{extra}")
            if args.once or probe_force:
                break
            time.sleep(BOOK_SNAPSHOT_SEC)
        return 0
    finally:
        writer.close()
        if worker is not None:
            worker.stop()
            print("  IB worker stopped (no orders were sent)")


def main(argv: list[str] | None = None) -> int:
    """Parse CLI and run. Never connects when --no-connect is set."""
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
