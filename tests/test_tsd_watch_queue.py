"""Unit tests for UTS v2 LAUNCH watch queue."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline import tsd_watch_queue as wq

ZIP_LAUNCH = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": True,
    "early_bull": False,
    "close": 5.25,
    "open": 5.30,
    "wt_gap": 5.1,
    "kill_pct": 0.08,
    "market_cap": 500_000_000,
    "tsd_profile": {"analog_count": 42, "analog_win_rate": 52.4},
}


def _mock_enrich_queue_row(cand, **kwargs):
    row = {
        **cand,
        "phase": "LAUNCH",
        "launch_score": 72.5,
        "launch_score_display": 72.5,
        "signal_bar_red": True,
        "analog_count": 42,
        "analog_win_rate": 52.4,
        "tags": ["pre_catalyst"],
        "size_mult": 1.0,
        "pre_catalyst": True,
        "news_summary": "🔀 No Catalyst: No news found",
        "catalyst_tier": 0,
        "sentiment_score": 0.0,
    }
    return row, True, {"analog_count": True, "analog_win_rate": True}, []


class TestWatchQueue(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._queue_path = Path(self._tmpdir.name) / "tsd_watch_queue.json"
        self._patch_path = patch.object(wq, "QUEUE_PATH", self._queue_path)
        self._patch_path.start()

    def tearDown(self):
        self._patch_path.stop()
        self._tmpdir.cleanup()

    @patch("tsd_scan_pipeline.tsd_watch_queue.enrich_queue_row", side_effect=_mock_enrich_queue_row)
    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_add_launch_candidate(self, _occ, _reg, _enrich):
        results = wq.add_to_watch_queue([ZIP_LAUNCH], scan_at="2026-09-01T12:00:00-04:00")
        self.assertEqual(results[0]["status"], "ADDED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        row = state["queue"][0]
        self.assertEqual(row["symbol"], "ZIP")
        self.assertEqual(row["status"], "WATCHING")
        self.assertEqual(row["phase"], "LAUNCH")
        self.assertIn("launch_score", row)
        self.assertTrue(row["pre_catalyst"])
        self.assertEqual(row["catalyst_tier"], 0)
        self.assertIn("pre_catalyst", row["tags"])

    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_skip_extension_weav(self, _occ, _reg):
        weav = {
            "symbol": "WEAV",
            "scan_score": 77.99,
            "trend_strength": 0.67,
            "buy_signal": True,
            "wt_gap": 5.0,
            "close": 7.31,
        }
        results = wq.add_to_watch_queue([weav])
        self.assertEqual(results[0]["status"], "SKIPPED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["queue"]), 0)

    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_skip_low_wt_gap(self, _occ, _reg):
        cand = {**ZIP_LAUNCH, "wt_gap": 1.0}
        results = wq.add_to_watch_queue([cand])
        self.assertEqual(results[0]["status"], "SKIPPED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["queue"]), 0)


if __name__ == "__main__":
    unittest.main()
