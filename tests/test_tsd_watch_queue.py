"""Unit tests for UTS v2 Phase 1 watch queue."""
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
    def test_add_passing_candidate(self, _occ, _reg):
        cand = {
            "symbol": "WEAV",
            "scan_score": 78,
            "wt_gap": 5.1,
            "close": 7.31,
            "kill_pct": 0.08,
            "buy_signal": True,
        }
        results = wq.add_to_watch_queue([cand], scan_at="2026-09-01T12:00:00-04:00")
        self.assertEqual(results[0]["status"], "ADDED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        row = state["queue"][0]
        self.assertEqual(row["symbol"], "WEAV")
        self.assertEqual(row["status"], "WATCHING")
        self.assertEqual(row["signal_lane"], "A")
        self.assertEqual(row["cross_level"], 7.31)
        self.assertGreaterEqual(row["entry_score"], 70)

    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_skip_low_wt_gap(self, _occ, _reg):
        cand = {"symbol": "X", "scan_score": 80, "wt_gap": 1.0, "close": 10.0}
        results = wq.add_to_watch_queue([cand])
        self.assertEqual(results[0]["status"], "SKIPPED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["queue"]), 0)


if __name__ == "__main__":
    unittest.main()
