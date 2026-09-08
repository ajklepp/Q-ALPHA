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

    def positions(self):
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
        self.assertEqual(
            fake.disconnect_calls,
            len(tsd_trail_monitor.TWS_CLIENT_ID_FALLBACKS),
        )

    def test_trail_connect_falls_back_from_stale_primary_client_id(self):
        class _FallbackIB(_FakeIB):
            def connect(self, *_args, **kwargs):
                if kwargs.get("clientId") == 95:
                    raise TimeoutError("primary stale")
                return True

        fake = _FallbackIB()
        state = {
            "positions": [
                {"symbol": "TEST", "status": "OPEN", "legs": []},
            ],
        }
        with (
            patch.object(tsd_trail_monitor, "IB", return_value=fake),
            patch.object(tsd_trail_monitor, "load_state", return_value=state),
            patch.object(tsd_trail_monitor, "open_symbols", return_value=["TEST"]),
            patch.object(tsd_trail_monitor, "_process_position", return_value=[]),
            patch.object(tsd_trail_monitor, "_save_snapshot", return_value=Path("test.json")),
        ):
            result = tsd_trail_monitor.run_monitor(dry_run=True)

        self.assertNotIn("error", result)
        self.assertEqual(result["client_id"], 85)

    def test_live_trail_never_sells_a_broker_flat_symbol(self):
        fake = _FakeIB()
        state = {
            "positions": [
                {"symbol": "TEST", "status": "OPEN", "legs": []},
            ],
        }
        with (
            patch.object(tsd_trail_monitor, "IB", return_value=fake),
            patch.object(tsd_trail_monitor, "load_state", return_value=state),
            patch.object(tsd_trail_monitor, "open_symbols", return_value=["TEST"]),
            patch.object(tsd_trail_monitor, "_process_position") as process_position,
            patch.object(tsd_trail_monitor, "save_state"),
            patch.object(tsd_trail_monitor, "_save_snapshot", return_value=Path("test.json")),
            patch.object(tws_intraday_sync, "_reconcile_tsd_broker_kills", return_value=[]),
            patch("tsd_supabase_sync.push_dashboard_best_effort"),
        ):
            result = tsd_trail_monitor.run_monitor()

        self.assertNotIn("error", result)
        process_position.assert_not_called()

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
    def test_broker_kill_fill_closes_stale_tsd_leg(self):
        tranches = [
            {
                "id": f"T{idx}",
                "shares": shares,
                "weight": weight,
                "trigger_pct": trigger,
                "trigger_price": 22.51 * (1 + trigger),
                "trail_pct": 0.04,
                "closed": False,
            }
            for idx, shares, weight, trigger in (
                (1, 5, 0.4, 0.03),
                (2, 4, 0.3, 0.05),
                (3, 2, 0.2, 0.08),
                (4, 1, 0.1, 0.10),
            )
        ]
        leg = {
            "time": "2026-09-08T10:21:00-04:00",
            "price": 22.51,
            "shares": 12,
            "kill_order_id": 41856,
            "status": "OPEN",
            "trail": {
                "entry_price": 22.51,
                "kill_price": 21.3845,
                "kill_pct": 0.05,
                "trail_pct": 0.04,
                "tranches": tranches,
            },
            "exits": [],
        }
        position = {"symbol": "BETA", "status": "OPEN", "legs": [leg]}
        book = {"positions": [position]}
        sells = [{
            "side": "SLD",
            "price": 21.36,
            "qty": 12.0,
            "order_id": 41856,
            "time": "2026-09-08 18:48:53+00:00",
        }]

        with (
            patch("tsd_scan_pipeline.tsd_capacity.load_state", return_value=book),
            patch("tsd_scan_pipeline.tsd_capacity.save_state") as save_state,
            patch("tsd_scan_pipeline.tsd_pool.release_on_exit") as release,
            patch.object(
                tws_intraday_sync,
                "_collect_symbol_fills",
                return_value=([], sells),
            ),
        ):
            reconciled = tws_intraday_sync._reconcile_tsd_broker_kills(
                object(),
                {},
            )

        self.assertEqual(reconciled, ["BETA"])
        self.assertEqual(position["status"], "CLOSED")
        self.assertEqual(leg["status"], "CLOSED")
        self.assertTrue(all(row["closed"] for row in tranches))
        self.assertEqual(leg["exits"][0]["exit_price"], 21.36)
        release.assert_called_once()
        save_state.assert_called_once_with(book)

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
            patch.object(tws_intraday_sync, "_reconcile_tsd_broker_kills", return_value=[]),
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
