"""Smoke: tsd_notify never raises without tokens; format helpers return text."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_notify import (  # noqa: E402
    format_entered,
    format_exited,
    format_queue_skip_summary,
    notify_tsd,
)


def test_notify_no_raise() -> None:
    notify_tsd("Peak Hour smoke test (ignore)")


def test_formatters() -> None:
    e = format_entered("ABC", shares=10, fill_price=12.5, kill_pct=0.05, bar_hour=7, rank=88.1)
    assert "ENTERED ABC" in e and "kill=5.0%" in e
    x = format_exited("ABC", reason="idle_no_1r", shares=10, exit_price=11.0, pnl_dollars=-15.0)
    assert "EXITED ABC" in x and "idle_no_1r" in x
    s = format_queue_skip_summary(2, [{"symbol": "ZZ", "status": "SKIPPED", "reason": "full"}])
    assert "0 queue-admitted" in s


if __name__ == "__main__":
    test_notify_no_raise()
    test_formatters()
    print("OK tsd_notify smoke")
