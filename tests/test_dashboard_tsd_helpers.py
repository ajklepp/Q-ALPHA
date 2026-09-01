"""Unit tests for UTS v2 dashboard helpers."""
from __future__ import annotations

import unittest

from dashboard_tsd_helpers import (
    map_exit_layer,
    mfe_in_r,
    next_trail_stop,
    progress_fraction,
    progress_milestones,
)


class TestDashboardHelpers(unittest.TestCase):
    def test_map_exit_layer(self):
        self.assertEqual(map_exit_layer("structure_stop"), "Structure")
        self.assertEqual(map_exit_layer("T1_hit"), "Trail")
        self.assertEqual(map_exit_layer("kill"), "Kill")

    def test_mfe_in_r(self):
        self.assertAlmostEqual(mfe_in_r(10.0, 11.0, 9.0), 1.0)

    def test_next_trail_stop(self):
        tranches = [
            {"trailing": True, "closed": False, "run_high": 12.0, "trail_pct": 0.1},
            {"trailing": False, "closed": False, "run_high": 12.0, "trail_pct": 0.1},
        ]
        self.assertAlmostEqual(next_trail_stop(tranches), 10.8)

    def test_progress_milestones(self):
        row = {
            "entry_price": 10.0,
            "structure_stop": 9.5,
            "tranche_json": [
                {"id": "T1", "trigger_price": 10.5, "closed": False},
                {"id": "T2", "trigger_price": 11.0, "closed": False},
            ],
        }
        ms = progress_milestones(row)
        self.assertEqual(len(ms), 4)
        self.assertGreater(progress_fraction(10.3, ms), 0.0)


if __name__ == "__main__":
    unittest.main()
