"""Unit tests for 3H base detection and breakdown exit."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_base_break import (
    base_break_exit,
    check_base_break,
    detect_3h_base,
)


def _bar(high: float, low: float, close: float | None = None) -> dict:
    c = close if close is not None else (high + low) / 2
    return {"high": high, "low": low, "close": c, "open": c}


class TestBaseBreak(unittest.TestCase):
    def test_detect_tight_base(self):
        bars = [_bar(10.0, 9.8) for _ in range(6)]
        bars.append(_bar(10.2, 9.9, close=10.0))
        base = detect_3h_base(bars, lookback=6)
        self.assertIsNotNone(base)
        self.assertAlmostEqual(base["base_low"], 9.8)
        self.assertAlmostEqual(base["base_high"], 10.0)

    def test_reject_wide_base(self):
        bars = [_bar(12.0, 9.0) for _ in range(6)]
        bars.append(_bar(12.0, 9.0))
        self.assertIsNone(detect_3h_base(bars, lookback=6))

    def test_base_break_exit(self):
        self.assertTrue(base_break_exit(9.7, 9.8))
        self.assertFalse(base_break_exit(9.9, 9.8))

    def test_check_base_break_integration(self):
        bars = [_bar(10.0, 9.8) for _ in range(6)]
        bars.append(_bar(10.1, 9.7, close=9.75))
        broke, info = check_base_break(bars, 9.75)
        self.assertTrue(broke)
        self.assertIsNotNone(info)


if __name__ == "__main__":
    unittest.main()
