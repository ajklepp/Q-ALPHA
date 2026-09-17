"""Unit tests for UTS v2 LAUNCH watch queue."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

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
    "htf_range_20d_pct": 0.35,
    "htf_close_above_sma50": True,
    "htf_sma20_rising": True,
    "htf_1h_buy_signal": True,
    "htf_1h_bar_hour": 12,
    "htf_1h_close": 5.25,
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
    @patch(
        "tsd_scan_pipeline.tsd_entry_gates.evaluate_htf_daily_gates",
        return_value=(True, {}, [], 60.0),
    )
    @patch(
        "tsd_scan_pipeline.tsd_entry_gates.evaluate_1h_buy_signal",
        return_value=(True, {"htf_1h_bar_hour": 12}),
    )
    def test_skip_extension_weav(self, _1h, _htf, _occ, _reg):
        weav = {
            "symbol": "WEAV",
            "scan_score": 77.99,
            "trend_strength": 0.67,
            "buy_signal": True,
            "wt_gap": 5.0,
            "close": 7.31,
        }
        results = wq.add_to_watch_queue([weav], scan_at="2026-09-01T12:00:00-04:00")
        self.assertEqual(results[0]["status"], "SKIPPED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["queue"]), 0)

    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_skip_htf_fail(self, _occ, _reg):
        cand = {**ZIP_LAUNCH, "htf_close_above_sma50": False}
        results = wq.add_to_watch_queue([cand], scan_at="2026-09-01T12:00:00-04:00")
        self.assertEqual(results[0]["status"], "SKIPPED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(state["queue"]), 0)


class TestGhostConfirmed(unittest.TestCase):
    """Ghost CONFIRMED (historical, now flat) must not block NEW takes."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._queue_path = Path(self._tmpdir.name) / "tsd_watch_queue.json"
        self._patch_path = patch.object(wq, "QUEUE_PATH", self._queue_path)
        self._patch_path.start()

    def tearDown(self):
        self._patch_path.stop()
        self._tmpdir.cleanup()

    def _write_queue(self, rows: list[dict]) -> None:
        self._queue_path.write_text(
            json.dumps({"queue": rows, "last_updated": None}, indent=2),
            encoding="utf-8",
        )

    def test_stale_confirmed_flat_not_in_flight(self):
        """NUAI CONFIRMED 2026-09-11, book flat on 2026-09-15 → not excluded."""
        self._write_queue([
            {
                "symbol": "NUAI",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-11T10:22:00-04:00",
            },
            {
                "symbol": "IRD",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-14T11:05:00-04:00",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 8, 15))
        book = {"positions": []}
        self.assertEqual(
            wq.in_flight_confirmed_symbols(book, now=now),
            set(),
        )
        self.assertEqual(wq.confirmed_symbols(), {"NUAI", "IRD"})

    def test_open_position_confirmed_is_in_flight(self):
        """OPEN book position stays excluded even if CONFIRMED on a prior day."""
        self._write_queue([
            {
                "symbol": "ATRC",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-11T09:40:00-04:00",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 8, 15))
        book = {"positions": [{"symbol": "ATRC", "status": "OPEN"}]}
        self.assertEqual(
            wq.in_flight_confirmed_symbols(book, now=now),
            {"ATRC"},
        )

    def test_todays_confirmed_not_open_is_in_flight(self):
        """Same-session CONFIRMED blocks NEW even before the book shows OPEN."""
        self._write_queue([
            {
                "symbol": "HPE",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-15T08:18:00-04:00",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 9, 15))
        book = {"positions": []}
        self.assertEqual(
            wq.in_flight_confirmed_symbols(book, now=now),
            {"HPE"},
        )

    def test_expire_stale_confirmed_downgrades_ghosts(self):
        """Daily reset: flat prior-day CONFIRMED → CLEARED_STALE; keep in-flight."""
        self._write_queue([
            {
                "symbol": "NUAI",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-11T10:22:00-04:00",
            },
            {
                "symbol": "HPE",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-15T08:18:00-04:00",
            },
            {
                "symbol": "ATRC",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-11T09:40:00-04:00",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 8, 15))
        book = {"positions": [{"symbol": "ATRC", "status": "OPEN"}]}
        expired = wq.expire_stale_confirmed(book, now=now)
        self.assertEqual(expired, ["NUAI"])
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        by_sym = {r["symbol"]: r for r in state["queue"]}
        self.assertEqual(by_sym["NUAI"]["status"], "CLEARED_STALE")
        self.assertEqual(by_sym["HPE"]["status"], "CONFIRMED")
        self.assertEqual(by_sym["ATRC"]["status"], "CONFIRMED")

    @patch("tsd_scan_pipeline.tsd_watch_queue.enrich_queue_row", side_effect=_mock_enrich_queue_row)
    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    @patch.object(wq, "_open_symbol_set", return_value=set())
    def test_add_overwrites_stale_confirmed(self, _opens, _occ, _reg, _enrich):
        """Ghost CONFIRMED can be re-admitted as WATCHING for a new take."""
        self._write_queue([
            {
                "symbol": "ZIP",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-11T10:22:00-04:00",
            },
        ])
        results = wq.add_to_watch_queue(
            [ZIP_LAUNCH], scan_at="2026-09-15T08:15:00-04:00",
        )
        self.assertEqual(results[0]["status"], "UPDATED")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(state["queue"][0]["status"], "WATCHING")

    @patch("tsd_scan_pipeline.tsd_watch_queue.enrich_queue_row", side_effect=_mock_enrich_queue_row)
    @patch("tsd_scan_pipeline.tsd_watch_queue.fetch_regime_bull", return_value=(True, "BULL", {}))
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    @patch.object(wq, "_open_symbol_set", return_value=set())
    def test_add_keeps_todays_confirmed(self, _opens, _occ, _reg, _enrich):
        """Same-session CONFIRMED is still KEEP / already_confirmed."""
        self._write_queue([
            {
                "symbol": "ZIP",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-15T08:18:00-04:00",
            },
        ])
        results = wq.add_to_watch_queue(
            [ZIP_LAUNCH], scan_at="2026-09-15T09:15:00-04:00",
        )
        self.assertEqual(results[0]["status"], "UNCHANGED")
        self.assertEqual(results[0]["reason"], "already_confirmed")
        state = json.loads(self._queue_path.read_text(encoding="utf-8"))
        self.assertEqual(state["queue"][0]["status"], "CONFIRMED")

    def test_already_long_open_false_not_confirmed_in_flight(self):
        """Hour-14 IRD: already_long with open=False must not stay Cap in-flight."""
        self._write_queue([
            {
                "symbol": "IRD",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-15T14:17:00-04:00",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 15, 15))
        book = {
            "entries_this_scan": 0,
            "positions": [
                {
                    "symbol": "IRD",
                    "status": "CLOSED",
                    "entry_count": 1,
                    "t4_only": False,
                    "legs": [],
                },
            ],
        }
        with patch(
            "tsd_scan_pipeline.tsd_watch_queue.can_enter",
            return_value=(False, "already_long"),
        ):
            results = wq.execute_live_entries(
                object(), [{"symbol": "IRD"}], book,
            )
        self.assertEqual(results[0]["status"], "SKIPPED")
        self.assertEqual(results[0]["reason"], "already_long")
        row = json.loads(self._queue_path.read_text(encoding="utf-8"))["queue"][0]
        self.assertNotEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["status"], "SKIPPED")
        self.assertTrue(row.get("confirmed_cleared_no_risk"))
        self.assertFalse(
            wq.is_confirmed_in_flight(row, now=now, open_syms=set()),
        )
        self.assertNotIn(
            "IRD",
            wq.in_flight_confirmed_symbols(book, now=now),
        )

    def test_leftover_confirmed_already_long_not_in_flight(self):
        """Same-session CONFIRMED + already_long skip_reason is not Cap-exclude."""
        self._write_queue([
            {
                "symbol": "IRD",
                "status": "CONFIRMED",
                "confirmed_at": "2026-09-15T14:17:00-04:00",
                "skip_reason": "already_long",
            },
        ])
        now = wq.ET.localize(datetime(2026, 9, 15, 15, 15))
        book = {"positions": []}
        row = json.loads(self._queue_path.read_text(encoding="utf-8"))["queue"][0]
        self.assertFalse(
            wq.is_confirmed_in_flight(row, now=now, open_syms=set()),
        )
        self.assertEqual(wq.in_flight_confirmed_symbols(book, now=now), set())


if __name__ == "__main__":
    unittest.main()
