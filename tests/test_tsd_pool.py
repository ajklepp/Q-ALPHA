"""Unit tests for TSD pool deploy / release accounting."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_pool import (  # noqa: E402
    DEFAULT_STARTING_POOL,
    deploy_on_entry,
    load_pool,
    pool_equity,
    release_on_exit,
    save_pool,
)


class TestTsdPool(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.pool_path = Path(self._tmpdir.name) / "tsd_pool_state.json"
        save_pool(
            {
                "pool": DEFAULT_STARTING_POOL,
                "deployed": 0.0,
                "starting_pool": DEFAULT_STARTING_POOL,
            },
            self.pool_path,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _state(self) -> dict:
        return load_pool(self.pool_path)

    def test_loser_round_trip(self) -> None:
        """Buy 100@10, sell 100@8 → pool=2800, deployed=0, sum=2800."""
        p = self.pool_path
        deploy_on_entry(100, 10.0, path=p)
        doc = load_pool(p)
        self.assertAlmostEqual(doc["pool"], 2000.0)
        self.assertAlmostEqual(doc["deployed"], 1000.0)

        release_on_exit(100, 8.0, entry_price=10.0, path=p)
        doc = load_pool(p)
        self.assertAlmostEqual(doc["pool"], 2800.0)
        self.assertAlmostEqual(doc["deployed"], 0.0)
        self.assertAlmostEqual(doc["pool"] + doc["deployed"], 2800.0)

    def test_winner_round_trip(self) -> None:
        """Buy 50@20, sell 50@24 → pool=3200, deployed=0 (+$200)."""
        p = self.pool_path
        deploy_on_entry(50, 20.0, path=p)
        release_on_exit(50, 24.0, entry_price=20.0, path=p)
        doc = load_pool(p)
        self.assertAlmostEqual(doc["pool"], 3200.0)
        self.assertAlmostEqual(doc["deployed"], 0.0)

    def test_partial_exit_keeps_deployed(self) -> None:
        p = self.pool_path
        deploy_on_entry(100, 10.0, path=p)
        release_on_exit(40, 11.0, entry_price=10.0, path=p)
        doc = load_pool(p)
        self.assertAlmostEqual(doc["pool"], 2000.0 + 440.0)
        self.assertAlmostEqual(doc["deployed"], 600.0)
        self.assertAlmostEqual(pool_equity(doc), 3040.0)

    def test_persists_to_disk(self) -> None:
        deploy_on_entry(10, 5.0, path=self.pool_path)
        raw = json.loads(self.pool_path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(raw["deployed"], 50.0)


if __name__ == "__main__":
    unittest.main()
