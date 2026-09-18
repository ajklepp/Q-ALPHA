"""Unit tests for the research L2/tape logger — fakes only, no live IB."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from candidates.microstructure_logger.constants import (  # noqa: E402
    DEPTH_SOURCE,
    FALLBACK_SYMBOLS,
    PROBE_MAX_SYMBOLS,
    ROW_KEYS,
    SOURCE_L2,
    TEST01_REQUIRED_KEYS,
    TWS_CLIENT_ID,
)
from candidates.microstructure_logger.features import (  # noqa: E402
    BookHistory,
    BookLevel,
    TapeEngine,
    book_features,
    classify_print_side,
    imbalance,
    imbalance_l3,
    microprice,
    mid_price,
    spread_bps,
)
from candidates.microstructure_logger.ib_worker import assert_paper_endpoint  # noqa: E402
from candidates.microstructure_logger.jsonl_writer import JsonlWriter  # noqa: E402
from candidates.microstructure_logger.schema import (  # noqa: E402
    assert_row,
    build_row,
    empty_row,
    missing_required,
)
from candidates.microstructure_logger.session import (  # noqa: E402
    SESSION_CLOSED,
    SESSION_POST,
    SESSION_PRE,
    SESSION_RTH,
    session_tag,
    should_stream,
)
from candidates.microstructure_logger.universe import resolve_universe  # noqa: E402

ET = ZoneInfo("America/New_York")


def _et(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


# Friday 2026-09-11 is a weekday (TEST-01 session fixtures).
FRI = (2026, 9, 11)


def test_test01_empty_row_has_required_keys() -> None:
    row = empty_row()
    assert missing_required(row) == []
    for key in TEST01_REQUIRED_KEYS:
        assert key in row
    for key in ROW_KEYS:
        assert key in row


def test_build_row_always_has_test01_and_ab_keys() -> None:
    row = build_row(
        ts_utc="2026-09-11T14:30:00Z",
        symbol="aapl",
        session_tag="RTH",
        source=SOURCE_L2,
        features={"bid1": 100.0, "ask1": 100.1, "tape_burst_z": None},
    )
    assert_row(row)
    assert row["symbol"] == "AAPL"
    assert row["bid1"] == 100.0
    assert row["ask1"] == 100.1
    assert row["tape_burst_z"] is None
    assert row["print_imb_5s"] is None
    assert row["mid"] is None
    assert row["depth_source"] == DEPTH_SOURCE
    assert row["source"] == SOURCE_L2


def test_book_math_mid_microprice_spread_imbalance() -> None:
    bid1, ask1, bid_sz1, ask_sz1 = 10.0, 10.2, 40.0, 10.0
    mid = mid_price(bid1, ask1)
    assert mid == 10.1
    mp = microprice(bid1, ask1, bid_sz1, ask_sz1)
    assert mp is not None
    # Thinner ask (10) vs bid (40) pulls microprice toward the ask.
    assert mp > mid
    assert abs(mp - (10.0 * 10.0 + 10.2 * 40.0) / 50.0) < 1e-12
    spr = spread_bps(bid1, ask1, mid)
    assert spr is not None
    assert abs(spr - (0.2 / 10.1) * 10_000) < 1e-9
    imb = imbalance(bid_sz1, ask_sz1)
    assert imb is not None
    assert abs(imb - 30.0 / 50.0) < 1e-12


def test_imbalance_l3_null_when_shallow() -> None:
    assert imbalance_l3([1, 2], [1, 2]) is None
    got = imbalance_l3([10, 10, 10], [5, 5, 5])
    assert got is not None
    assert abs(got - 0.3333333333) < 1e-6


def test_crossed_book_mid_is_null() -> None:
    assert mid_price(10.2, 10.0) is None
    assert mid_price(None, 10.0) is None
    assert spread_bps(10.0, 10.2, None) is None


def test_book_pressure_deltas_and_flicker() -> None:
    hist = BookHistory()
    t0 = datetime(2026, 9, 11, 14, 30, 0, tzinfo=ET)
    # Stable book for 2s, then flip mid and invert size.
    snaps = [
        (t0 + timedelta(seconds=0), [BookLevel(10.00, 50)], [BookLevel(10.02, 10)]),
        (t0 + timedelta(seconds=1), [BookLevel(10.00, 50)], [BookLevel(10.02, 10)]),
        (t0 + timedelta(seconds=5), [BookLevel(10.01, 10)], [BookLevel(10.03, 50)]),
        (t0 + timedelta(seconds=6), [BookLevel(10.00, 10)], [BookLevel(10.02, 50)]),
        (t0 + timedelta(seconds=7), [BookLevel(10.01, 10)], [BookLevel(10.03, 50)]),
    ]
    last = None
    for ts, bids, asks in snaps:
        last = book_features(ts=ts, bids=bids, asks=asks, history=hist)
    assert last is not None
    assert last["imbalance_l3"] is None  # only 1 level
    assert last["book_pressure_delta_1s"] is not None
    assert last["book_pressure_delta_5s"] is not None
    # Mid moved 10.01 -> 10.00 -> 10.01: at least one flicker flip.
    assert last["quote_flicker"] is not None
    assert last["quote_flicker"] >= 0.0
    assert last["levels_n"] == 1


def test_lee_ready_then_tick_rule() -> None:
    assert classify_print_side(10.05, mid=10.0, prev_price=10.0, last_tick_sign=0) == 1
    assert classify_print_side(9.95, mid=10.0, prev_price=10.0, last_tick_sign=0) == -1
    # At mid: tick rule.
    assert classify_print_side(10.0, mid=10.0, prev_price=9.9, last_tick_sign=0) == 1
    assert classify_print_side(10.0, mid=10.0, prev_price=10.1, last_tick_sign=0) == -1
    # No mid: tick rule only.
    assert classify_print_side(10.2, mid=None, prev_price=10.0, last_tick_sign=0) == 1
    # Zero-tick inherits last sign.
    assert classify_print_side(10.0, mid=10.0, prev_price=10.0, last_tick_sign=-1) == -1


def test_tape_print_imb_vwap_uptick_and_large_flag() -> None:
    eng = TapeEngine()
    t0 = datetime(2026, 9, 11, 14, 31, 0, tzinfo=ET)
    # Three small prints then a 5x-median monster.
    prints = [
        (t0 + timedelta(seconds=0), 10.00, 100.0, 10.0),
        (t0 + timedelta(seconds=1), 10.01, 100.0, 10.0),
        (t0 + timedelta(seconds=2), 9.99, 100.0, 10.0),
        (t0 + timedelta(seconds=3), 10.02, 100.0, 10.0),
        (t0 + timedelta(seconds=4), 10.50, 600.0, 10.0),
    ]
    for ts, px, sz, mid in prints:
        eng.add_print(ts, px, sz, mid=mid)
    now = t0 + timedelta(seconds=4)
    feat = eng.features(now)
    assert feat["print_vwap_5s"] is not None
    assert feat["print_imb_5s"] is not None
    # Last print is above mid → buy; earlier mixed. Imbalance defined.
    assert -1.0 <= feat["print_imb_5s"] <= 1.0
    assert feat["large_print_flag"] == 1
    assert feat["uptick_ratio_30s"] is not None
    # Not enough 5s buckets yet.
    assert feat["tape_burst_z"] is None


def test_tape_burst_z_warmup_then_value() -> None:
    eng = TapeEngine()
    t0 = datetime(2026, 9, 11, 14, 0, 0, tzinfo=ET)
    # 12 completed 5s buckets (60s) at ~100 size, then a burst.
    for i in range(12):
        ts = t0 + timedelta(seconds=i * 5)
        eng.add_print(ts, 10.0, 100.0, mid=10.0)
    # Advance so those buckets close, then dump size in the open bucket.
    now = t0 + timedelta(seconds=12 * 5)
    assert eng.tape_burst_z(now) is not None or len(eng.buckets) >= 12
    burst_ts = now + timedelta(seconds=1)
    eng.add_print(burst_ts, 10.0, 5_000.0, mid=10.0)
    z = eng.tape_burst_z(burst_ts + timedelta(seconds=0.5))
    assert z is not None
    assert z > 0


def test_session_tags_weekday_windows() -> None:
    assert session_tag(_et(*FRI, 4, 0)) == SESSION_PRE
    assert session_tag(_et(*FRI, 9, 29)) == SESSION_PRE
    assert session_tag(_et(*FRI, 9, 30)) == SESSION_RTH
    assert session_tag(_et(*FRI, 15, 59)) == SESSION_RTH
    assert session_tag(_et(*FRI, 16, 0)) == SESSION_POST
    assert session_tag(_et(*FRI, 19, 59)) == SESSION_POST
    assert session_tag(_et(*FRI, 20, 0)) == SESSION_CLOSED
    assert session_tag(_et(*FRI, 2, 0)) == SESSION_CLOSED
    # Sunday 2026-09-13.
    assert session_tag(_et(2026, 9, 13, 12, 0)) == SESSION_CLOSED


def test_rth_primary_gate() -> None:
    rth = _et(*FRI, 10, 0)
    pre = _et(*FRI, 8, 0)
    post = _et(*FRI, 17, 0)
    assert should_stream(rth, allow_extended=False) is True
    assert should_stream(pre, allow_extended=False) is False
    assert should_stream(post, allow_extended=False) is False
    assert should_stream(pre, allow_extended=True) is True
    assert should_stream(post, allow_extended=True) is True
    assert should_stream(_et(2026, 9, 13, 10, 0), allow_extended=True) is False


def test_universe_from_launch_artifact() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        launch = root / "candidates" / "tsd_scan_pipeline" / "results"
        launch.mkdir(parents=True)
        (launch / "last_1h_launch.json").write_text(
            json.dumps({
                "rows": [
                    {"symbol": "THIN", "continuation_score": 99, "dollar_vol_1h": 1_000, "rank": 1},
                    {"symbol": "LIQD", "continuation_score": 40, "dollar_vol_1h": 9_000_000, "rank": 2},
                    {"symbol": "MID", "continuation_score": 80, "dollar_vol_1h": 500_000, "rank": 3},
                ]
            }),
            encoding="utf-8",
        )
        uni = resolve_universe(root=root, top_n=2)
        assert uni.used_fallback is False
        assert uni.symbols[0] == "LIQD"
        assert uni.symbols == ["LIQD", "MID"]


def test_universe_cli_override_and_fallback() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        over = resolve_universe(root=root, symbols=["msft", "msft", "aapl"], top_n=8)
        assert over.symbols == ["MSFT", "AAPL"]
        assert over.used_fallback is False
        empty = resolve_universe(root=root, top_n=3)
        assert empty.used_fallback is True
        assert empty.symbols == list(FALLBACK_SYMBOLS[:3])
        assert empty.fallback_reason is not None


def test_jsonl_writer_schema_and_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        writer = JsonlWriter(tmp_path / "microstructure")
        row = build_row(
            ts_utc="2026-09-11T14:31:00+00:00",
            symbol="SPY",
            session_tag="RTH",
            source=SOURCE_L2,
            levels_n=2,
            features={
                "bid1": 500.0,
                "ask1": 500.1,
                "bid_sz1": 10,
                "ask_sz1": 12,
                "mid": 500.05,
                "imbalance_l1": -0.0909,
                "spread_bps": 2.0,
                "print_imb_5s": None,
                "tape_burst_z": None,
            },
        )
        path = writer.write(row)
        writer.close()
        assert path == tmp_path / "microstructure" / "20260911" / "SPY.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert_row(loaded)
        for key in TEST01_REQUIRED_KEYS:
            assert key in loaded
        assert loaded["depth_source"] == DEPTH_SOURCE


def test_paper_endpoint_guards() -> None:
    assert_paper_endpoint("127.0.0.1", 7497, 72)
    try:
        assert_paper_endpoint("127.0.0.1", 7496, 72)
        raise AssertionError("live port must be refused")
    except RuntimeError as exc:
        assert "7496" in str(exc)
    try:
        assert_paper_endpoint("127.0.0.1", 7497, 71)
        raise AssertionError("clientId 71 must be refused")
    except RuntimeError as exc:
        assert "72" in str(exc)
    try:
        assert_paper_endpoint("gw.interactivebrokers.com", 7497, 72)
        raise AssertionError("cloud host must be refused")
    except RuntimeError as exc:
        assert "Gateway" in str(exc) or "local" in str(exc)


def test_client_id_constant() -> None:
    assert TWS_CLIENT_ID == 72


def test_cli_no_connect_writes_test01_rows() -> None:
    from candidates.microstructure_logger.runner import main

    with tempfile.TemporaryDirectory() as td:
        log_root = Path(td) / "microstructure"
        rc = main([
            "--no-connect",
            "--once",
            "--symbols",
            "SPY,QQQ",
            "--log-root",
            str(log_root),
        ])
        assert rc == 0
        for sym in ("SPY", "QQQ"):
            matches = list(log_root.glob(f"*/{sym}.jsonl"))
            assert matches, f"missing {sym} jsonl"
            rows = [json.loads(line) for line in matches[0].read_text(encoding="utf-8").splitlines() if line]
            assert len(rows) == 2
            sources = {r["source"] for r in rows}
            assert sources == {"qalpha_l2", "qalpha_tape"}
            for row in rows:
                assert_row(row)
                assert row["tape_burst_z"] is None
                assert row["print_imb_5s"] is None
                assert row["depth_source"] == DEPTH_SOURCE


def test_probe_once_caps_depth_to_few_symbols() -> None:
    """--once/--probe must never subscribe Cap-scale depth lists."""
    from candidates.microstructure_logger.runner import apply_depth_subscribe_limits, parse_args

    assert PROBE_MAX_SYMBOLS == 3

    capped = apply_depth_subscribe_limits(
        parse_args(["--once", "--top", "8", "--symbols", "SPY,QQQ,IWM,AAPL,MSFT,NVDA"])
    )
    assert capped.top == PROBE_MAX_SYMBOLS
    assert _parse_symbols_helper(capped.symbols) == ["SPY", "QQQ", "IWM"]

    probe = apply_depth_subscribe_limits(parse_args(["--probe", "--top", "100"]))
    assert probe.once is True
    assert probe.top == PROBE_MAX_SYMBOLS
    assert probe.symbols == "SPY"

    continuous = apply_depth_subscribe_limits(parse_args(["--top", "8"]))
    assert continuous.top == 8
    assert (continuous.symbols or "") == ""


def _parse_symbols_helper(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def test_run_refuses_live_port() -> None:
    from candidates.microstructure_logger.runner import parse_args, run

    args = parse_args(["--no-connect", "--port", "7496", "--symbols", "SPY"])
    try:
        run(args)
        raise AssertionError("live port must be refused even with --no-connect")
    except RuntimeError as exc:
        assert "7496" in str(exc)


def test_package_does_not_import_peak_hour() -> None:
    """Logger sources must not import TSD / Peak Hour strategy modules."""
    import ast

    banned = (
        "tsd_scan_pipeline",
        "tsd_watch_queue",
        "tsd_trail",
        "uts_v2",
        "autonomous_agent",
        "ibkr_connector",
        "paper_trader",
        "setup_watch_agent",
    )
    pkg = ROOT / "candidates" / "microstructure_logger"
    leaked: list[str] = []
    for path in pkg.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                if any(tok in name for tok in banned):
                    leaked.append(f"{path.name}:{name}")
    assert leaked == [], f"Peak Hour imports leaked: {leaked}"


if __name__ == "__main__":
    test_test01_empty_row_has_required_keys()
    test_build_row_always_has_test01_and_ab_keys()
    test_book_math_mid_microprice_spread_imbalance()
    test_imbalance_l3_null_when_shallow()
    test_crossed_book_mid_is_null()
    test_book_pressure_deltas_and_flicker()
    test_lee_ready_then_tick_rule()
    test_tape_print_imb_vwap_uptick_and_large_flag()
    test_tape_burst_z_warmup_then_value()
    test_session_tags_weekday_windows()
    test_rth_primary_gate()
    test_universe_from_launch_artifact()
    test_universe_cli_override_and_fallback()
    test_jsonl_writer_schema_and_path()
    test_paper_endpoint_guards()
    test_client_id_constant()
    test_package_does_not_import_peak_hour()
    test_cli_no_connect_writes_test01_rows()
    test_run_refuses_live_port()
    print("OK microstructure logger")
