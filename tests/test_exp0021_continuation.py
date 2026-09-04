"""Unit tests for EXP-0021 continuation features + social failure modes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments" / "EXP-0021" / "lib"))

from features import (  # noqa: E402
    all_hours_admit,
    continuation_score_v1,
    path_labels_after_entry,
    peak_hour_v0_admit,
)
from social import fetch_x_recent, fetch_stocktwits  # noqa: E402


class TestPathLabels(unittest.TestCase):
    def test_hit_before_kill(self):
        lab = path_labels_after_entry(100.0, [103.0, 106.0], [99.0, 98.0])
        self.assertEqual(lab["hit_1r"], 1)
        self.assertEqual(lab["mfe_ge_5"], 1)

    def test_kill_before_target(self):
        lab = path_labels_after_entry(100.0, [101.0, 102.0], [94.0, 93.0])
        self.assertEqual(lab["hit_1r"], 0)
        self.assertEqual(lab["killed"], 1)


class TestAdmit(unittest.TestCase):
    def test_peak_v0_blocks_off_hour(self):
        feat = {
            "hour": 14,
            "buy_signal": True,
            "early_bull": False,
            "scan_score": 40,
            "bar_state": "yellow",
            "phase": "LAUNCH",
        }
        self.assertFalse(peak_hour_v0_admit(feat))
        self.assertTrue(all_hours_admit(feat))

    def test_peak_v0_blocks_high_scan(self):
        feat = {
            "hour": 11,
            "buy_signal": True,
            "scan_score": 61,
            "bar_state": "green",
            "phase": "NEUTRAL",
            "launch_score": 55.0,
        }
        self.assertFalse(peak_hour_v0_admit(feat))
        self.assertTrue(all_hours_admit(feat))

    def test_all_hours_blocks_late_and_weak_ext(self):
        late = {
            "hour": 15,
            "buy_signal": True,
            "scan_score": 40,
            "launch_score": 60,
            "bar_state": "yellow",
        }
        self.assertFalse(all_hours_admit(late))
        weak_ext = {
            "hour": 14,
            "buy_signal": True,
            "scan_score": 62,
            "launch_score": 20,
            "bar_state": "green",
        }
        self.assertFalse(all_hours_admit(weak_ext))


class TestScoreV1(unittest.TestCase):
    def test_peak_bonus_and_guidance(self):
        base = {
            "hour": 11,
            "peak_hour": 1,
            "bar_state": "yellow",
            "dist_20d_high_pct": 0.12,
            "dist_20d_low_bounce": 0.8,
            "vol_ratio_20": 2.0,
            "ticker_prior_hit1r_rate": 0.4,
            "news_velocity_24h": 3,
            "st_msg_24h": 10,
            "st_bull_ratio": 0.7,
            "scan_score": 35,
        }
        s0 = continuation_score_v1(base)
        s1 = continuation_score_v1({**base, "guidance_cut": 1})
        self.assertGreater(s0, s1)
        s_off = continuation_score_v1({**base, "hour": 14, "peak_hour": 0})
        self.assertGreater(s0, s_off)


class TestSocialNonBlocking(unittest.TestCase):
    def test_x_without_bearer_zeros(self):
        out = fetch_x_recent("IREN")
        self.assertEqual(out["x_ok"], 0)
        self.assertEqual(out["x_posts_24h"], 0.0)

    def test_stocktwits_shape(self):
        out = fetch_stocktwits("AAPL")
        self.assertIn("st_msg_24h", out)
        self.assertIn("st_bull_ratio", out)


class TestTwsClientIdFallbacks(unittest.TestCase):
    def test_fallback_tuple(self):
        sys.path.insert(0, str(ROOT / "candidates"))
        import tws_intraday_sync as sync

        self.assertEqual(sync.TWS_CLIENT_ID, 96)
        self.assertEqual(sync.TWS_CLIENT_ID_FALLBACKS, (96, 86, 76))
        self.assertTrue(callable(sync._connect_ib))
        self.assertTrue(callable(sync._notify_connect_failed))


if __name__ == "__main__":
    unittest.main()
