"""Smoke: Peak Hour Performers reset defaults match loaders."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_capacity import _default_state as book_default
from tsd_scan_pipeline.tsd_pool import DEFAULT_STARTING_POOL, _default_state as pool_default

_RESET = ROOT / "candidates" / "uts_v2" / "reset_peak_hour_performers.py"
_spec = importlib.util.spec_from_file_location("reset_php", _RESET)
assert _spec and _spec.loader
_reset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reset)


class TestPhpResetDefaults(unittest.TestCase):
    def test_pool_matches_loader(self):
        p = _reset._default_pool()
        self.assertEqual(p["pool"], DEFAULT_STARTING_POOL)
        self.assertEqual(p["deployed"], 0.0)
        self.assertEqual(p["starting_pool"], DEFAULT_STARTING_POOL)
        self.assertEqual(pool_default()["pool"], DEFAULT_STARTING_POOL)

    def test_book_empty(self):
        b = _reset._default_book()
        self.assertEqual(b["positions"], [])
        self.assertEqual(b["entries_this_scan"], 0)
        self.assertEqual(book_default()["positions"], [])

    def test_queue_and_scheduler(self):
        self.assertEqual(_reset._default_queue()["queue"], [])
        self.assertEqual(_reset._default_scheduler()["last_runs"], {})

    def test_dry_run_ok(self):
        _reset.archive_and_reset(dry_run=True)


if __name__ == "__main__":
    unittest.main()
