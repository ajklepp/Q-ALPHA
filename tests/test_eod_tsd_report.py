"""Peak Hour-aware EOD notification regressions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from position_monitor import format_eod_telegram


class TestPeakHourEodReport(unittest.TestCase):
    def _result(self, tsd_eod: dict) -> dict:
        return {
            "date": "2026-09-08",
            "events": [],
            "summary": {"open_trades": 0},
            "pool": {},
            "tsd_eod": tsd_eod,
        }

    def test_peak_hour_positions_override_empty_legacy_book(self):
        result = self._result({
            "available": True,
            "positions": [
                {"symbol": "PURR", "pnl_dollars": 5.25},
                {"symbol": "FWDI", "pnl_dollars": -1.00},
            ],
            "pool": {"pool": 897.18, "deployed": 2067.92},
        })

        message = format_eod_telegram(result)

        self.assertIn("PEAK HOUR EOD", message)
        self.assertIn("Open positions:  2", message)
        self.assertIn("Open P&L:        $+4.25", message)
        self.assertNotIn("No open positions", message)

    def test_unavailable_tsd_state_never_claims_flat(self):
        message = format_eod_telegram(self._result({
            "available": False,
            "positions": [],
            "error": "network",
        }))

        self.assertIn("status unavailable", message)
        self.assertIn("not reporting flat", message)


if __name__ == "__main__":
    unittest.main()
