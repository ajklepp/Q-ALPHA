"""Unit tests for UTS v2 dashboard helpers."""
from __future__ import annotations

import math
import unittest

import pandas as pd

from dashboard_tsd_helpers import (
    build_tranche_table_rows,
    format_level,
    format_trail_stop_cell,
    hold_time_display,
    map_exit_layer,
    mfe_in_r,
    next_trail_stop,
    progress_fraction,
    progress_milestones,
    progress_tick_labels,
)


class TestDashboardHelpers(unittest.TestCase):
    def test_format_level(self):
        self.assertEqual(format_level(7.45, 7.31), "$7.45 (+1.9% entry)")

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

    def test_progress_tick_labels(self):
        row = {
            "entry_price": 7.31,
            "kill_price": 6.72,
            "structure_stop": 7.24,
            "tranche_json": [
                {"id": "T1", "trigger_price": 7.45, "closed": False},
                {"id": "T2", "trigger_price": 7.58, "closed": False},
            ],
        }
        ticks = progress_tick_labels(row)
        self.assertIn("Kill", ticks)
        self.assertIn("Entry 0%", ticks)
        self.assertIn("T1", ticks)
        self.assertIn("T2", ticks)

    def test_progress_milestones_includes_kill_and_entry(self):
        row = {
            "entry_price": 10.0,
            "kill_price": 9.2,
            "structure_stop": 9.5,
            "tranche_json": [
                {"id": "T1", "trigger_price": 10.5, "closed": False},
                {"id": "T2", "trigger_price": 11.0, "closed": False},
            ],
        }
        ms = progress_milestones(row)
        labels = [m[0] for m in ms]
        self.assertIn("Kill", labels)
        self.assertIn("Entry", labels)
        self.assertGreater(progress_fraction(10.3, ms), 0.0)

    def test_tranche_table_rows(self):
        tranches = [
            {
                "id": "T1",
                "shares": 10,
                "trigger_price": 7.45,
                "armed": False,
                "closed": False,
            },
            {
                "id": "T2",
                "shares": 8,
                "trigger_price": 7.58,
                "armed": True,
                "closed": False,
                "run_high": 7.31,
                "trail_pct": 0.054,
                "trail_stop": 6.92,
            },
        ]
        rows = build_tranche_table_rows(tranches, entry=7.31, current=7.35)
        self.assertIn("+1.9% entry", rows[0]["Trigger"])
        self.assertIn("off high", rows[1]["Trail stop"])
        self.assertEqual(rows[0]["To trigger"], "+1.4% from current")

    def test_format_trail_stop_cell(self):
        t = {
            "armed": True,
            "closed": False,
            "run_high": 7.31,
            "trail_pct": 0.054,
            "trail_stop": 6.92,
        }
        cell = format_trail_stop_cell(t)
        self.assertIn("$6.92", cell)
        self.assertIn("off high $7.31", cell)

    def test_hold_time_display(self):
        h = hold_time_display(
            "2026-08-31T10:00:00-04:00",
            "2026-09-01T14:30:00-04:00",
        )
        self.assertIn("d", h)


class TestStylePnl(unittest.TestCase):
    def test_style_pnl_accepts_formatted_strings(self):
        from dashboard import _style_pnl

        pos = _style_pnl("+5.2%")
        neg = _style_pnl("-3.1%")
        self.assertIn("font-weight", pos)
        self.assertIn("font-weight", neg)
        self.assertNotEqual(pos, neg)

    def test_style_pnl_accepts_numeric(self):
        from dashboard import _style_pnl

        self.assertIn("font-weight", _style_pnl(0.052))
        self.assertEqual(_style_pnl(float("nan")), "")


if __name__ == "__main__":
    unittest.main()
