"""Regression tests for scheduled TSD mark/trail updater liveness."""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pytz

import tws_intraday_sync
from tsd_scan_pipeline import scheduler, tsd_trail_monitor

ET = pytz.timezone("America/New_York")


class _FakeIB:
    def __init__(self, *, connect_error: Exception | None = None):
        self.connect_error = connect_error
        self.disconnect_calls = 0

    def connect(self, *_args, **_kwargs):
        if self.connect_error:
            raise self.connect_error
        return True

    def disconnect(self):
        self.disconnect_calls += 1

    def reqExecutions(self):
        return []

    def sleep(self, _seconds):
        return None


class TestTrailMonitorLiveness(unittest.TestCase):
    def test_failed_connect_always_disconnects_socket(self):
        fake = _FakeIB(connect_error=TimeoutError("stalled"))
        state = {
            "positions": [
                {"symbol": "TEST", "status": "OPEN", "legs": []},
            ],
        }
        with (
            patch.object(tsd_trail_monitor, "IB", return_value=fake),
            patch.object(tsd_trail_monitor, "load_state", return_value=state),
            patch.object(tsd_trail_monitor, "open_symbols", return_value=["TEST"]),
        ):
            result = tsd_trail_monitor.run_monitor()

        self.assertIn("error", result)
        self.assertEqual(fake.disconnect_calls, 1)

    def test_extended_sleep_does_not_trigger_backup_client(self):
        now = datetime.now(ET)
        with patch.object(
            scheduler,
            "_load_state",
            return_value={"trail_loop_heartbeat": (now - timedelta(minutes=6)).isoformat()},
        ):
            self.assertTrue(scheduler._trail_loop_active())

        with patch.object(
            scheduler,
            "_load_state",
            return_value={"trail_loop_heartbeat": (now - timedelta(minutes=8)).isoformat()},
        ):
            self.assertFalse(scheduler._trail_loop_active())


class TestScheduledTwsSyncLiveness(unittest.TestCase):
    def test_tsd_only_returns_before_legacy_gap_ledger(self):
        fake = _FakeIB()
        tsd_summary = {
            "upserted": 4,
            "closed_upserted": 1,
            "pool_synced": True,
            "watch_queue_synced": 2,
            "verify_errors": [],
        }

        class _Health:
            def log_health(self, *_args, **_kwargs):
                return None

        with (
            patch("ib_insync.IB", return_value=fake),
            patch.object(tws_intraday_sync, "_connect_ib", return_value=96),
            patch.object(tws_intraday_sync, "_ib_position_map", return_value={}),
            patch(
                "tsd_supabase_sync.sync_tsd_positions_to_supabase",
                return_value=tsd_summary,
            ),
            patch("supabase_sync.SupabaseSync", return_value=_Health()),
            patch.object(
                tws_intraday_sync,
                "PaperTradesStore",
                side_effect=AssertionError("legacy gap ledger should not run"),
            ),
        ):
            result = tws_intraday_sync.run_tws_intraday_sync(tsd_only=True)

        self.assertEqual(result["tsd_upserted"], 4)
        self.assertEqual(result["tsd_closed"], 1)
        self.assertTrue(result["tsd_pool"])
        self.assertGreaterEqual(fake.disconnect_calls, 1)


if __name__ == "__main__":
    unittest.main()
