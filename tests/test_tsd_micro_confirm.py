"""Unit tests for Peak Hour micro-confirm."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_micro_confirm import (  # noqa: E402
    ET,
    MicroBar,
    evaluate_micro_confirm,
    pullback_limit_price,
)


def _bar(minute: int, o: float, h: float, l: float, c: float, vol: float = 1000) -> MicroBar:
    ts = ET.localize(datetime(2026, 9, 10, 10, minute))
    return MicroBar(ts=ts, open=o, high=h, low=l, close=c, volume=vol)


def test_abort_on_dump():
    signal = 10.0
    bars = [_bar(1, 10.0, 10.1, 9.84, 9.85)]
    v, r = evaluate_micro_confirm(
        signal_close=signal, structure_level=None, bars_after=bars, minutes_elapsed=3,
    )
    assert v == "ABORT", (v, r)


def test_confirm_hold():
    signal = 10.0
    bars = [
        _bar(1, 10.0, 10.05, 9.98, 10.02),
        _bar(2, 10.02, 10.08, 10.00, 10.06),
    ]
    v, r = evaluate_micro_confirm(
        signal_close=signal, structure_level=None, bars_after=bars, minutes_elapsed=3,
    )
    assert v == "CONFIRM", (v, r)


def test_pending_min_wait():
    signal = 10.0
    bars = [_bar(0, 10.0, 10.05, 9.99, 10.03)]
    v, r = evaluate_micro_confirm(
        signal_close=signal, structure_level=None, bars_after=bars, minutes_elapsed=1,
    )
    assert v == "PENDING", (v, r)


def test_chase_waits_pullback():
    signal = 10.0
    bars = [_bar(3, 10.2, 10.25, 10.18, 10.22)]  # +2.2% chase
    v, r = evaluate_micro_confirm(
        signal_close=signal, structure_level=None, bars_after=bars, minutes_elapsed=5,
    )
    assert v == "PENDING", (v, r)
    assert "pullback" in r


def test_dead_tape_rvol():
    signal = 10.0
    bars = [
        _bar(1, 10.0, 10.05, 9.98, 10.02, 50),
        _bar(2, 10.02, 10.08, 10.00, 10.06, 50),
        _bar(3, 10.06, 10.08, 10.04, 10.07, 50),
        _bar(4, 10.07, 10.09, 10.05, 10.08, 50),
        _bar(5, 10.08, 10.10, 10.06, 10.09, 50),
    ]
    v, r = evaluate_micro_confirm(
        signal_close=signal,
        structure_level=None,
        bars_after=bars,
        minutes_elapsed=8,
        micro_rvol_ratio=0.2,
    )
    assert v == "PENDING", (v, r)
    assert "rvol" in r


def test_pullback_limit_caps_chase():
    lmt = pullback_limit_price(10.0, last_close=10.30)
    assert lmt <= 10.02 + 1e-9, lmt
    assert lmt >= 10.0, lmt


if __name__ == "__main__":
    test_abort_on_dump()
    test_confirm_hold()
    test_pending_min_wait()
    test_chase_waits_pullback()
    test_dead_tape_rvol()
    test_pullback_limit_caps_chase()
    print("micro_confirm OK")
