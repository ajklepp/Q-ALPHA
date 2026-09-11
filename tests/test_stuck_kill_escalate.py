"""Unit tests for stuck STP LMT kill detection (no live IB)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_exit import kill_stop_is_stuck


class TestStuckKillDetection(unittest.TestCase):
    def test_janx_like_through_limit(self):
        # SELL STP LMT ~18.89 hanging while last ~17.69
        self.assertTrue(
            kill_stop_is_stuck(
                last_price=17.69,
                stop_price=18.98,
                limit_price=18.89,
            )
        )

    def test_last_above_limit_not_stuck(self):
        self.assertFalse(
            kill_stop_is_stuck(
                last_price=19.50,
                stop_price=18.98,
                limit_price=18.89,
            )
        )

    def test_stop_breached_counts_stuck(self):
        # Last at/below stop even if still near limit band
        self.assertTrue(
            kill_stop_is_stuck(
                last_price=18.90,
                stop_price=18.98,
                limit_price=18.89,
            )
        )

    def test_invalid_last_not_stuck(self):
        self.assertFalse(
            kill_stop_is_stuck(
                last_price=0,
                stop_price=18.98,
                limit_price=18.89,
            )
        )


if __name__ == "__main__":
    unittest.main()
