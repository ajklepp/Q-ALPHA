"""Unit tests for Peak Hour bar_state + continuation rank + kill resolve."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_kill import FALLBACK_KILL_PCT, resolve_kill_pct
from tsd_scan_pipeline.tsd_launch_score import (
    classify_bar_state,
    compute_continuation_score_v0,
    enrich_launch_fields,
    is_launch_candidate,
)


BASE = {
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": True,
    "early_bull": False,
    "htf_score": 80.0,
    "htf_1h_bar_hour": 11,
}


class TestKillResolve(unittest.TestCase):
    def test_fallback_is_5pct(self):
        self.assertEqual(FALLBACK_KILL_PCT, 0.05)

    def test_profile_in_band(self):
        k, src = resolve_kill_pct(0.046)
        self.assertAlmostEqual(k, 0.046)
        self.assertEqual(src, "raw")

    def test_profile_out_of_band_uses_fallback(self):
        k, src = resolve_kill_pct(0.08)
        self.assertEqual(k, 0.05)
        self.assertEqual(src, "fallback_5pct")

    def test_too_tight_uses_fallback(self):
        k, src = resolve_kill_pct(0.01)
        self.assertEqual(k, 0.05)
        self.assertEqual(src, "fallback_5pct")


class TestBarState(unittest.TestCase):
    def test_orange_doji(self):
        row = {**BASE, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.05}
        self.assertEqual(classify_bar_state(row), "orange")

    def test_red(self):
        row = {**BASE, "open": 10.0, "high": 10.2, "low": 9.0, "close": 9.2}
        self.assertEqual(classify_bar_state(row), "red")

    def test_yellow_weak_green(self):
        row = {**BASE, "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.3}
        self.assertEqual(classify_bar_state(row), "yellow")

    def test_green_strong(self):
        row = {**BASE, "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.8}
        self.assertEqual(classify_bar_state(row), "green")

    def test_extended(self):
        row = {**BASE, "scan_score": 80, "open": 10, "close": 10.5, "high": 11, "low": 10}
        self.assertEqual(classify_bar_state(row), "extended")


class TestRedNotRequired(unittest.TestCase):
    def test_yellow_launch_candidate(self):
        row = enrich_launch_fields({
            **BASE,
            "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.3,
        })
        self.assertEqual(row["bar_state"], "yellow")
        self.assertFalse(row["signal_bar_red"])
        self.assertTrue(is_launch_candidate(row))

    def test_green_launch_candidate(self):
        row = enrich_launch_fields({
            **BASE,
            "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.8,
        })
        self.assertEqual(row["bar_state"], "green")
        self.assertTrue(is_launch_candidate(row))

    def test_orange_can_pass_but_ranks_lower(self):
        yellow = enrich_launch_fields({
            **BASE, "symbol": "Y", "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.3,
        })
        orange = enrich_launch_fields({
            **BASE, "symbol": "O", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.05,
        })
        red = enrich_launch_fields({
            **BASE, "symbol": "R", "open": 10.0, "high": 10.2, "low": 9.0, "close": 9.2,
        })
        green = enrich_launch_fields({
            **BASE, "symbol": "G", "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.8,
        })
        self.assertTrue(is_launch_candidate(orange))
        scores = {
            "yellow": yellow["continuation_score"],
            "red": red["continuation_score"],
            "green": green["continuation_score"],
            "orange": orange["continuation_score"],
        }
        self.assertGreater(scores["yellow"], scores["red"])
        self.assertGreater(scores["red"], scores["green"])
        self.assertGreater(scores["green"], scores["orange"])

    def test_hour_13_demotes_v0(self):
        """v0 still soft-demotes 13; v1 uses equal peak bonus for 11/13."""
        h11 = enrich_launch_fields({**BASE, "htf_1h_bar_hour": 11, "open": 10, "close": 9.5, "high": 10.2, "low": 9})
        h13 = enrich_launch_fields({**BASE, "htf_1h_bar_hour": 13, "open": 10, "close": 9.5, "high": 10.2, "low": 9})
        self.assertGreater(h11["continuation_score_v0"], h13["continuation_score_v0"])
        self.assertEqual(h13["hour_mult"], 0.85)
        # Live ranker v1: both peak hours get the same peak bonus
        self.assertEqual(h11["continuation_score"], h13["continuation_score"])

    def test_guidance_cut_penalty(self):
        base = enrich_launch_fields({**BASE, "open": 10, "close": 9.5, "high": 10.2, "low": 9})
        cut = enrich_launch_fields({
            **BASE, "open": 10, "close": 9.5, "high": 10.2, "low": 9, "outlook": "lowered",
        })
        self.assertGreater(base["continuation_score"], cut["continuation_score"])
        self.assertAlmostEqual(
            base["continuation_score"] - cut["continuation_score"],
            25.0,
            places=0,
        )


if __name__ == "__main__":
    unittest.main()
