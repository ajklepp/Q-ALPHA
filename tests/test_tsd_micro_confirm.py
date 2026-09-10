"""Unit tests for Peak Hour micro-confirm."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_micro_confirm import (  # noqa: E402
    ET,
    MicroBar,
    evaluate_micro_confirm,
)


def _bar(minute: int, o: float, h: float, l: float, c: float) -> MicroBar:
    ts = ET.localize(datetime(2026, 9, 10, 10, minute))
    return MicroBar(ts=ts, open=o, high=h, low=l, close=c, volume=1000)


def test_abort_on_dump():
    signal = 10.0
    bars = [_bar(1, 10.0, 10.1, 9.80, 9.85)]  # pierced −1.5% floor 9.85 exactly... use 9.84
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


if __name__ == "__main__":
    test_abort_on_dump()
    test_confirm_hold()
    test_pending_min_wait()
    print("micro_confirm OK")
