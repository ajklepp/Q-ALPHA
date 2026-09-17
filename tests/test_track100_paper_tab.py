"""Unit tests for the Track 100 paper-book read contract (display only)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_track100 import (  # noqa: E402
    empty_track100_book,
    load_track100_paper_book,
    paper_book_candidates,
    resolve_paper_book_path,
)


class TestTrack100ReadContract(unittest.TestCase):
    def test_missing_file_is_clear_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope" / "paper_book.json"
            with patch.dict(os.environ, {"TRACK100_PAPER_BOOK": str(missing)}, clear=False):
                book = load_track100_paper_book()
        self.assertFalse(book["ok"])
        self.assertIn(book["reason"], ("missing_paper_book", "scan_artifact_only"))
        self.assertEqual(book["totals"]["n_open"], 0)
        self.assertEqual(book["totals"]["n_closed"], 0)
        self.assertEqual(book["open"], [])
        self.assertEqual(book["closed"], [])

    def test_env_file_wins_and_loads_v12_schema(self) -> None:
        payload = {
            "version": 1,
            "strategy": "Track100_v12",
            "updated_et": "2026-09-16T12:00:00-04:00",
            "last_scan": {
                "date_et": "2026-09-16",
                "ended": "12:00 ET",
                "exit_code": 0,
            },
            "waiting_retest": [{"symbol": "MU", "signal_low": 98.5, "notes": "deep OS"}],
            "armed": [{"symbol": "LRCX", "signal_low": 110.0, "armed_et": "11:00"}],
            "open": [{
                "symbol": "AMD",
                "entry": 120.0,
                "qty": 10,
                "stop_pct": 5,
                "target_pct": 15,
                "opened_et": "2026-09-16T10:30:00-04:00",
            }],
            "closed": [{
                "symbol": "NVDA",
                "entry": 100.0,
                "exit": 115.0,
                "pnl_pct": 0.15,
                "reason": "target",
                "closed_et": "2026-09-15T15:00:00-04:00",
            }],
            "totals": {"realized_pnl_pct": 0.15, "n_closed": 1, "n_open": 1},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_book.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"TRACK100_PAPER_BOOK": str(path)}, clear=False):
                self.assertEqual(resolve_paper_book_path(), path)
                book = load_track100_paper_book()
        self.assertTrue(book["ok"])
        self.assertEqual(book["strategy"], "Track100_v12")
        self.assertEqual(book["waiting_retest"][0]["symbol"], "MU")
        self.assertEqual(book["armed"][0]["symbol"], "LRCX")
        self.assertEqual(book["open"][0]["symbol"], "AMD")
        self.assertEqual(book["closed"][0]["reason"], "target")
        self.assertEqual(book["totals"]["n_open"], 1)
        self.assertEqual(book["totals"]["n_closed"], 1)
        self.assertEqual(book["path"], str(path))

    def test_candidates_prefer_explicit_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRACK100_PAPER_BOOK": "D:/custom/paper_book.json",
                "TRACK100_ROOT": "D:/TrackRoot",
            },
            clear=False,
        ):
            paths = [str(p) for p in paper_book_candidates()]
        self.assertEqual(paths[0], "D:/custom/paper_book.json")
        self.assertTrue(any(p.endswith("paper_book.json") for p in paths[1:]))

    def test_empty_helper_documents_reason(self) -> None:
        book = empty_track100_book(reason="missing_paper_book")
        self.assertEqual(book["reason"], "missing_paper_book")
        self.assertFalse(book["ok"])


if __name__ == "__main__":
    unittest.main()
