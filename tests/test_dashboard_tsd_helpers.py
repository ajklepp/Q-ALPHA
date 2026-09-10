"""Unit tests for UTS v2 dashboard helpers."""
from __future__ import annotations

import math
import unittest
from datetime import datetime

import pandas as pd

from dashboard_tsd_helpers import (
    build_tranche_table_rows,
    format_level,
    format_trail_stop_cell,
    hold_time_display,
    is_t34_trailing_position,
    map_exit_layer,
    mark_age_minutes,
    mfe_in_r,
    next_trail_stop,
    partial_realized_from_open_row,
    progress_fraction,
    progress_milestones,
    progress_tick_labels,
    remaining_open_shares,
    scoreboard_pnl,
    unrealized_from_open_row,
)


class TestDashboardHelpers(unittest.TestCase):
    def test_mark_age_minutes(self):
        now = datetime.fromisoformat("2026-09-08T10:06:00-04:00")
        age = mark_age_minutes("2026-09-08T10:00:00-04:00", now=now)
        self.assertEqual(age, 6.0)

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


class TestScoreboardPnl(unittest.TestCase):
    def test_partial_trail_exit_counts_in_realized_and_remaining_shares(self):
        open_rows = [{
            "symbol": "FWDI",
            "entry_price": 6.36,
            "current_price": 6.56,
            "shares": 44,  # stale original size
            "tranche_json": [
                {"id": "T1", "shares": 18, "closed": True, "exit_price": 6.60},
                {"id": "T2", "shares": 13, "closed": False},
                {"id": "T3", "shares": 9, "closed": False},
                {"id": "T4", "shares": 4, "closed": False},
            ],
        }]
        closed_rows = [
            {"symbol": "PURR", "pnl_dollars": 2.40},
            {"symbol": "FGI", "pnl_dollars": -13.60},
            {"symbol": "CVI", "pnl_dollars": 0.0},
        ]
        self.assertEqual(remaining_open_shares(open_rows[0]), 26)
        self.assertAlmostEqual(partial_realized_from_open_row(open_rows[0]), 4.32, places=2)
        # T2 still intact → full slot, not trailing
        self.assertFalse(is_t34_trailing_position(open_rows[0]))
        board = scoreboard_pnl(open_rows, closed_rows)
        self.assertEqual(board["winners"], 1)
        self.assertEqual(board["losers"], 1)
        self.assertEqual(board["flats"], 1)
        self.assertAlmostEqual(board["partial_realized"], 4.32, places=2)
        self.assertAlmostEqual(board["realized"], 2.40 - 13.60 + 4.32, places=2)
        self.assertAlmostEqual(
            board["unrealized"],
            (6.56 - 6.36) * 26,
            places=2,
        )
        self.assertEqual(board["full_slots"], 1)
        self.assertEqual(board["trailing_positions"], 0)

    def test_trailing_only_when_t1_t2_gone(self):
        runner = {
            "symbol": "RUN",
            "entry_price": 10.0,
            "current_price": 11.0,
            "shares": 3,
            "tranche_json": [
                {"id": "T1", "shares": 4, "closed": True, "exit_price": 10.5},
                {"id": "T2", "shares": 3, "closed": True, "exit_price": 10.8},
                {"id": "T3", "shares": 2, "closed": False},
                {"id": "T4", "shares": 1, "closed": False},
            ],
        }
        tiny = {
            "symbol": "ATRC",
            "entry_price": 50.0,
            "current_price": 51.0,
            "shares": 4,
            "tranche_json": [
                {"id": "T1", "shares": 2, "closed": False},
                {"id": "T2", "shares": 2, "closed": False},
            ],
        }
        self.assertTrue(is_t34_trailing_position(runner))
        self.assertFalse(is_t34_trailing_position(tiny))
        board = scoreboard_pnl([runner, tiny], [])
        self.assertEqual(board["full_slots"], 1)
        self.assertEqual(board["trailing_positions"], 1)
        self.assertEqual(board["open_names"], 2)


if __name__ == "__main__":
    unittest.main()
