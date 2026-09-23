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
    format_tranche_levels_caption,
    hold_time_display,
    is_t34_trailing_position,
    live_paper_scoreboard,
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

    def test_tranche_levels_caption_hit_and_prices(self):
        row = {
            "tranche_json": [
                {"id": "T1", "trigger_price": 6.55, "closed": True, "trailing": True},
                {"id": "T2", "trigger_price": 6.68, "closed": False, "trailing": True},
                {"id": "T3", "trigger_price": 6.87, "closed": False, "trailing": False},
                {"id": "T4", "trigger_price": 7.00, "closed": False, "trailing": False},
            ],
        }
        cap = format_tranche_levels_caption(row)
        self.assertEqual(cap, "T1=HIT · T2=HIT · T3=$6.87 · T4=$7.00")


class TestLivePaperScoreboard(unittest.TestCase):
    """Live Status Equity / P&L $ / P&L % must be one identity."""

    def _assert_identity(self, board: dict) -> None:
        self.assertAlmostEqual(
            board["total_pnl"],
            board["realized"] + board["unrealized"],
            places=2,
        )
        self.assertAlmostEqual(
            board["equity"],
            board["starting"] + board["total_pnl"],
            places=2,
        )
        self.assertAlmostEqual(
            board["equity"],
            board["cash"] + board["open_mtm"],
            places=2,
        )
        if board["starting"] > 0:
            self.assertAlmostEqual(
                board["pnl_pct"],
                board["total_pnl"] / board["starting"] * 100.0,
                places=4,
            )

    def test_screenshot_combo_is_rejected(self):
        """
        Aaron's Live Status tiles were Equity $3,504.03, P&L −$165.28 (−5.5%),
        cash $2,596.94, unrealized −5.59, realized −159.69.

        −5.5% is −$165.28 / the $3,000 starting pool (displays to 1 decimal).
        That base is not the $3,504 equity tile. Equity − cash ≈ $907 is the
        open mark, so the old formula was snapshot cash + marks while P&L
        stayed on the trade ledger. Those two books differ by about $669
        because the snapshot still held capital that was also in the marks.
        """
        starting = 3000.0
        snapshot_cash = 2596.94
        # entry×100 = 912.68 cost; mark×100 = 907.09; unrealized = −5.59
        open_rows = [{
            "symbol": "OPEN",
            "entry_price": 9.1268,
            "current_price": 9.0709,
            "shares": 100,
            "partial_realized": 0.0,
        }]
        closed_rows = [{"symbol": "CLOSED", "pnl_dollars": -159.69}]

        legacy_equity = round(snapshot_cash + (9.0709 * 100), 2)
        legacy_pnl = round(-159.69 + (9.0709 - 9.1268) * 100, 2)
        legacy_pct = legacy_pnl / starting * 100.0
        self.assertEqual(legacy_equity, 3504.03)
        self.assertEqual(legacy_pnl, -165.28)
        self.assertEqual(f"{legacy_pct:+.1f}%", "-5.5%")
        # The displayed percent implies ~$3,005, not the equity tile.
        implied_base = legacy_pnl / (float(f"{legacy_pct:+.1f}") / 100.0)
        self.assertAlmostEqual(implied_base, 3005.09, delta=0.5)
        self.assertNotAlmostEqual(legacy_equity, starting + legacy_pnl, places=2)

        board = live_paper_scoreboard(
            open_rows,
            closed_rows,
            starting=starting,
            snapshot_cash=snapshot_cash,
        )
        self._assert_identity(board)
        self.assertAlmostEqual(board["realized"], -159.69, places=2)
        self.assertAlmostEqual(board["unrealized"], -5.59, places=2)
        self.assertAlmostEqual(board["total_pnl"], -165.28, places=2)
        self.assertEqual(f"{board['pnl_pct']:+.1f}%", "-5.5%")
        self.assertAlmostEqual(board["equity"], 2834.72, places=2)
        self.assertAlmostEqual(board["cash"], 1927.63, places=2)
        self.assertAlmostEqual(board["open_mtm"], 907.09, places=2)
        self.assertNotAlmostEqual(board["equity"], 3504.03, places=2)
        # Percent is vs the starting pool, not vs the equity tile.
        self.assertNotEqual(
            f"{(board['total_pnl'] / board['equity'] * 100.0):+.1f}%",
            "-5.5%",
        )

    def test_partial_exit_keeps_cash_equity_identity(self):
        open_rows = [{
            "symbol": "PART",
            "entry_price": 10.0,
            "current_price": 9.5,
            "shares": 20,
            "partial_realized": 10.0,
            "tranche_json": [
                {"id": "T1", "shares": 10, "closed": True, "exit_price": 11.0},
                {"id": "T2", "shares": 10, "closed": False},
            ],
        }]
        closed_rows = [{"symbol": "X", "pnl_dollars": -20.0}]
        # Snapshot never debited the open cost — old equity would be 3000+95.
        board = live_paper_scoreboard(
            open_rows,
            closed_rows,
            starting=3000.0,
            snapshot_cash=3000.0,
        )
        self._assert_identity(board)
        self.assertAlmostEqual(board["realized"], -10.0, places=2)
        self.assertAlmostEqual(board["unrealized"], -5.0, places=2)
        self.assertAlmostEqual(board["total_pnl"], -15.0, places=2)
        self.assertAlmostEqual(board["open_mtm"], 95.0, places=2)
        self.assertAlmostEqual(board["equity"], 2985.0, places=2)
        self.assertAlmostEqual(board["cash"], 2890.0, places=2)
        self.assertNotAlmostEqual(board["equity"], 3095.0, places=2)

    def test_unmarked_open_stays_in_equity_at_cost(self):
        open_rows = [{
            "symbol": "FLAT",
            "entry_price": 8.0,
            "current_price": 0.0,
            "shares": 10,
            "partial_realized": 0.0,
        }]
        board = live_paper_scoreboard(
            open_rows,
            [],
            starting=3000.0,
            snapshot_cash=3000.0,
        )
        self._assert_identity(board)
        self.assertAlmostEqual(board["unrealized"], 0.0, places=2)
        self.assertAlmostEqual(board["open_mtm"], 80.0, places=2)
        self.assertAlmostEqual(board["equity"], 3000.0, places=2)
        self.assertAlmostEqual(board["cash"], 2920.0, places=2)

    def test_empty_ledger_uses_snapshot_cash(self):
        board = live_paper_scoreboard(
            [],
            [],
            starting=3000.0,
            snapshot_cash=3000.0,
        )
        self._assert_identity(board)
        self.assertAlmostEqual(board["equity"], 3000.0, places=2)
        self.assertAlmostEqual(board["cash"], 3000.0, places=2)
        self.assertAlmostEqual(board["total_pnl"], 0.0, places=2)
        self.assertAlmostEqual(board["pnl_pct"], 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
