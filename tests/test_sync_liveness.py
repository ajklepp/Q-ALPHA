"""Regression tests for scheduled TSD mark/trail updater liveness."""
from __future__ import annotations

import asyncio
import io
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

import tsd_supabase_sync
import tws_intraday_sync
from tsd_scan_pipeline import scheduler, tsd_exit, tsd_trail_monitor

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

    def test_kill_sync_does_not_replace_sell_when_broker_flat(self):
        fake = _FakeIB()
        leg = {
            "shares": 12,
            "kill_order_id": 41875,
            "trail": {"tranches": []},
        }
        with patch.object(tsd_exit, "remaining_shares", return_value=12):
            result = tsd_exit.sync_kill_quantity(fake, leg, "BETA")

        self.assertFalse(result)

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
            "kill_order_id": 41875,
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
        self.assertEqual(leg["exits"][0]["order_id"], 41856)
        self.assertEqual(leg["exits"][0]["reason"], "broker_flat_sell_fill")
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


NX_OPENED_AT = "2026-09-14T09:03:00-04:00"
NX_START_ROW = {
    "symbol": "NX",
    "leg_opened_at": NX_OPENED_AT,
    "current_price": 20.63,
    "shares": 7,
    "kill_price": 19.60,
}


def _nx_open_book() -> dict:
    return {
        "positions": [
            {
                "symbol": "NX",
                "status": "OPEN",
                "legs": [
                    {
                        "status": "OPEN",
                        "time": NX_OPENED_AT,
                        "price": 20.50,
                        "shares": 7,
                        "trail": {
                            "entry_price": 20.50,
                            "last_close": 20.63,
                            "tranches": [],
                        },
                        "exits": [],
                    }
                ],
            }
        ]
    }


class _FakePos:
    def __init__(self, symbol: str, qty: float):
        self.contract = type("C", (), {"symbol": symbol})()
        self.position = qty


class _FakeVerifyIB:
    def __init__(self, positions: list | None = None):
        self._positions = list(positions or [])
        self.req_calls = 0

    def reqPositions(self):
        self.req_calls += 1

    def sleep(self, _seconds):
        return None

    def positions(self):
        return list(self._positions)


class _FakeQuery:
    def __init__(self, rows_by_key: dict):
        self.rows_by_key = rows_by_key
        self._symbol = ""
        self._leg = ""

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        if field in {"symbol", "ticker"}:
            self._symbol = str(value or "").upper()
        elif field in {"leg_opened_at", "entry_date"}:
            self._leg = str(value or "")
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        row = self.rows_by_key.get((self._symbol, self._leg))
        return type("R", (), {"data": [row] if row else []})()


class _FakeSupabase:
    def __init__(self, rows_by_key: dict | None = None):
        self.rows_by_key = rows_by_key or {}
        self.client = self

    def table(self, _name):
        return _FakeQuery(self.rows_by_key)


class TestTsdVerifyClosedDuringSync(unittest.TestCase):
    """NX 2026-09-14 race: trail flattened while intraday sync was verifying."""

    def test_classifier_distinguishes_closed_during_sync_from_missing(self):
        classify = tsd_supabase_sync.classify_tsd_verify_row
        self.assertEqual(
            classify(tws_qty=0.0, still_open_book=True, cloud_present=False),
            "closed_during_sync",
        )
        self.assertEqual(
            classify(tws_qty=7.0, still_open_book=True, cloud_present=False),
            "tsd_verify_missing",
        )
        self.assertEqual(
            classify(tws_qty=7.0, still_open_book=True, cloud_present=True),
            "ok",
        )
        self.assertEqual(
            classify(tws_qty=None, still_open_book=False, cloud_present=False),
            "closed_during_sync",
        )
        self.assertEqual(
            classify(tws_qty=None, still_open_book=True, cloud_present=False),
            "tsd_verify_missing",
        )

    def test_nx_flat_at_verify_is_closed_during_sync_not_missing(self):
        closed_book = {"positions": []}
        ib = _FakeVerifyIB([])
        buf = io.StringIO()
        with (
            patch.object(tsd_supabase_sync, "load_state", return_value=closed_book),
            patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase()),
            patch("sys.stdout", buf),
        ):
            errors = tsd_supabase_sync._verify_tsd_supabase_rows(
                [NX_START_ROW],
                ib=ib,
                book=closed_book,
            )
        self.assertEqual(errors, [])
        self.assertGreaterEqual(ib.req_calls, 1)
        self.assertIn("closed_during_sync", buf.getvalue())
        self.assertNotIn("tsd_verify_missing", buf.getvalue())

    def test_nx_still_held_without_cloud_row_is_missing(self):
        open_book = _nx_open_book()
        ib = _FakeVerifyIB([_FakePos("NX", 7)])
        with (
            patch.object(tsd_supabase_sync, "load_state", return_value=open_book),
            patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase()),
        ):
            errors = tsd_supabase_sync._verify_tsd_supabase_rows(
                [NX_START_ROW],
                ib=ib,
                book=open_book,
            )
        self.assertEqual(errors, ["tsd_verify_missing:NX"])

    def test_nx_still_held_with_cloud_row_is_ok(self):
        open_book = _nx_open_book()
        ib = _FakeVerifyIB([_FakePos("NX", 7)])
        cloud = {
            ("NX", NX_OPENED_AT): {
                "symbol": "NX",
                "status": "OPEN",
                "shares": 7,
                "current_price": 20.63,
                "kill_price": 19.60,
            }
        }
        with (
            patch.object(tsd_supabase_sync, "load_state", return_value=open_book),
            patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase(cloud)),
        ):
            errors = tsd_supabase_sync._verify_tsd_supabase_rows(
                [NX_START_ROW],
                ib=ib,
                book=open_book,
            )
        self.assertEqual(errors, [])

    def test_book_closed_without_tws_is_closed_during_sync(self):
        closed_book = {"positions": []}
        with (
            patch.object(tsd_supabase_sync, "load_state", return_value=closed_book),
            patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase()),
        ):
            errors = tsd_supabase_sync._verify_tsd_supabase_rows(
                [NX_START_ROW],
                ib=None,
                book=closed_book,
            )
        self.assertEqual(errors, [])

    def test_gap_verify_skips_ticker_flat_on_tws_at_verify_time(self):
        trades = [
            {
                "ticker": "NX",
                "entry_date": "2026-09-14",
                "status": "OPEN",
                "approved_by": "autonomous_agent",
                "execution_mode": "IBKR_PAPER",
            }
        ]
        with patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase()):
            errors = tws_intraday_sync._verify_supabase_trades(
                trades,
                tickers=["NX"],
                tws_qty_map={"ATRC": 2.0},
            )
        self.assertEqual(errors, [])

    def test_gap_verify_missing_when_tws_still_holds(self):
        trades = [
            {
                "ticker": "NX",
                "entry_date": "2026-09-14",
                "status": "OPEN",
            }
        ]
        with patch("supabase_sync.SupabaseSync", return_value=_FakeSupabase()):
            errors = tws_intraday_sync._verify_supabase_trades(
                trades,
                tickers=["NX"],
                tws_qty_map={"NX": 7.0},
            )
        self.assertEqual(errors, ["verify_missing:NX"])


if __name__ == "__main__":
    unittest.main()
