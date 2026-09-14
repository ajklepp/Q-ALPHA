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
    BOOK_SNAPSHOT_SEC,
    DEFAULT_DEPTH_MAX,
    DEFAULT_TOP_N,
    DEPTH_SOURCE,
    DEPTH_SOURCE_L1_ONLY,
    IB_ACCOUNT_DEPTH_CAP,
    IDLE_POLL_SEC,
    JSONL_ROOT_REL,
    SOURCE_L2,
    SOURCE_TAPE,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_PAPER_PORT,
)
from .depth import merge_l1_and_depth_symbols, select_depth_symbols
from .features import BookHistory, TapeEngine, book_features
from .ib_worker import IBWorker, assert_paper_endpoint
from .jsonl_writer import JsonlWriter
from .schema import build_row
from .session import session_tag, should_stream, to_et
from .universe import repo_root, resolve_universe


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI: --symbols / --top for L1+tape; --depth-max / --depth-symbols for L2."""
    p = argparse.ArgumentParser(
        description=(
            "READ-ONLY Peak Hour microstructure logger (Features A book + B tape). "
            f"TWS paper {TWS_HOST}:{TWS_PAPER_PORT} clientId {TWS_CLIENT_ID}. "
            f"reqMktDepth capped at --depth-max (default {DEFAULT_DEPTH_MAX}; "
            f"IB Error 309 cap is {IB_ACCOUNT_DEPTH_CAP}). L1/tape is not capped by that."
        )
    )
    p.add_argument(
        "--symbols",
        default="",
        help="Comma-separated L1/tape override (skips Peak Hour artifacts). Still capped by --top.",
    )
    p.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Max names for L1/tape reqMktData (default {DEFAULT_TOP_N}). Depth is --depth-max.",
    )
    p.add_argument(
        "--depth-max",
        type=int,
        default=DEFAULT_DEPTH_MAX,
        help=(
            f"Max concurrent reqMktDepth (default {DEFAULT_DEPTH_MAX}; "
            f"IB paper Error 309 is {IB_ACCOUNT_DEPTH_CAP}). Does not cap L1/tape."
        ),
    )
    p.add_argument(
        "--depth-symbols",
        default="",
        help=(
            "Comma-separated depth set, capped by --depth-max. "
            "Use names that delivered levels_n>=5 in JSONL when known. "
            "Default: prefer NYSE/ARCA/IEX listings from the L1 universe."
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
        help="Write one L2 + one tape row per symbol then exit.",
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


def _dry_cycle(
    symbols: list[str],
    writer: JsonlWriter,
    *,
    depth_symbols: list[str] | None = None,
) -> int:
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
    depth_set = {s.upper() for s in (depth_symbols or [])}
    for symbol in symbols:
        label = DEPTH_SOURCE if symbol.upper() in depth_set else DEPTH_SOURCE_L1_ONLY
        _write_pair(
            writer,
            symbol=symbol,
            now=now,
            book=empty_book,
            tape=empty_tape,
            depth_source=label,
        )
        print(f"  dry row {symbol} session={session_tag(now)} depth_source={label}")
    return 0


def run(args: argparse.Namespace) -> int:
    """Resolve universe, optionally connect clientId 72, log A+B JSONL."""
    assert_paper_endpoint(args.host, int(args.port), TWS_CLIENT_ID)
    root = repo_root()
    uni = resolve_universe(
        root=root,
        symbols=_parse_symbols(args.symbols),
        top_n=int(args.top),
    )
    depth_override = _parse_symbols(getattr(args, "depth_symbols", "") or "")
    depth_max = max(0, int(getattr(args, "depth_max", DEFAULT_DEPTH_MAX)))
    l1_symbols = merge_l1_and_depth_symbols(uni.symbols, depth_override)
    planned_depth = select_depth_symbols(
        l1_symbols,
        depth_max,
        depth_symbols=depth_override or None,
    )
    log_root = Path(args.log_root) if args.log_root else (root / JSONL_ROOT_REL)
    writer = JsonlWriter(log_root)

    print("Q-ALPHA microstructure logger — READ-ONLY research (A book + B tape)")
    print(f"  clientId={TWS_CLIENT_ID} host={args.host}:{int(args.port)} (paper)")
    print(
        f"  depth-max={depth_max} (IB Error 309 cap={IB_ACCOUNT_DEPTH_CAP}); "
        "L1/tape not capped by depth-max"
    )
    print(
        f"  depth path=SMART entitled ARCA/NYSE/IEX; "
        "NASDAQ/BATS/BEX 2152=EXPECTED (no BATS/BEX required; no Telegram)"
    )
    print(f"  depth_source default={DEPTH_SOURCE} (never TotalView)")
    print(f"  planned depth symbols={','.join(planned_depth) or '-'} "
          f"(override={','.join(depth_override) or 'auto-prefer NYSE/ARCA/IEX listings'})")
    print(f"  jsonl={log_root}")
    print(f"  L1/tape symbols={','.join(l1_symbols)} fallback={uni.used_fallback}")
    if uni.used_fallback:
        print(f"  {uni.fallback_reason}")
    if uni.artifacts_seen:
        print(f"  artifacts={uni.artifacts_seen}")
    print("  Peak Hour / TSD code: not imported, not mutated")
    print("  deferred: Feature C confirms, Feature D options")

    if args.no_connect:
        print("  --no-connect: skipping TWS (dry structure)")
        try:
            return _dry_cycle(l1_symbols, writer, depth_symbols=planned_depth)
        finally:
            writer.close()

    now = _utcnow()
    if not should_stream(now, allow_extended=bool(args.allow_extended)) and args.once:
        print(
            f"  skip: session={session_tag(now)} ET={to_et(now).strftime('%H:%M:%S')} "
            f"(RTH-primary; pass --allow-extended to stream)"
        )
        writer.close()
        return 0

    stop = {"flag": False}

    def _handle_stop(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_stop)

    worker: IBWorker | None = None
    histories = {s: BookHistory() for s in l1_symbols}
    tapes = {s: TapeEngine() for s in l1_symbols}
    started = time.monotonic()
    snapshots = 0

    try:
        while not stop["flag"]:
            now = _utcnow()
            if args.seconds > 0 and (time.monotonic() - started) >= args.seconds:
                break
            if not should_stream(now, allow_extended=bool(args.allow_extended)):
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
                worker = IBWorker(
                    host=args.host,
                    port=int(args.port),
                    client_id=TWS_CLIENT_ID,
                    depth_max=depth_max,
                    depth_symbols=depth_override or None,
                )
                worker.start()
                subscribed = worker.subscribe(l1_symbols)
                if not subscribed:
                    print("  no symbols subscribed — exiting")
                    return 2
                # Brief bound wait so first snapshot is not all-null.
                time.sleep(min(BOOK_SNAPSHOT_SEC, 1.0))

            now = _utcnow()
            for symbol in l1_symbols:
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
                    depth_source = worker.depth_source_for(symbol)
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
                if worker is not None and worker.depth_symbols:
                    extra = " depth=" + ",".join(
                        f"{s}:{worker.depth_source_for(s)}" for s in worker.depth_symbols
                    )
                print(f"  snapshot n={snapshots} session={session_tag(now)}{extra}")
            if args.once:
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
