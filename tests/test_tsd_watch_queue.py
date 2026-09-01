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
    "close": 4.245,
    "open": 4.30,
    "wt_gap": 5.1,
    "kill_pct": 0.08,
}


class TestWatchQueue(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._queue_path = Path(self._tmpdir.name) / "tsd_watch_queue.json"
        self._patch_path = patch.object(wq, "QUEUE_PATH", self._queue_path)
        self._patch_path.start()

    def tearDown(self):
        self._patch_path.stop()
        self._tmpdir.cleanup()

    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_add_launch_candidate(self, _occ, _reg):
        results = wq.add_to_watch_queue([ZIP_LAUNCH], scan_at="2026-09-01T12:00:00-04:00")
        self.assertEqual(results[0]["status"], "ADDED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        row = state["queue"][0]
        self.assertEqual(row["symbol"], "ZIP")
        self.assertEqual(row["status"], "WATCHING")
        self.assertEqual(row["phase"], "LAUNCH")
        self.assertIn("launch_score", row)
        self.assertIn("signal_bar_red", row)
        self.assertIn("early_bull", row)
        self.assertGreaterEqual(row["launch_score"], 50)

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
