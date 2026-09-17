"""1H LAUNCH tick ordering: SCAN Telegram before enter/sync; no missed-peak hammer."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pytz

from tsd_scan_pipeline import tsd_1h_launch_scan as scan
from tsd_scan_pipeline.tsd_stage_log import StageTimer

ET = pytz.timezone("America/New_York")

PASSER = {
    "symbol": "AAA",
    "pass": True,
    "buy_signal": True,
    "htf_1h_bar_hour": 10,
    "htf_1h_close": 12.5,
    "phase_3h": "LAUNCH",
    "continuation_score": 80.0,
    "case_verdict": "WAIT",
}

TAKE = {
    **PASSER,
    "case_verdict": "ENTER",
    "case_review": {"verdict": "ENTER", "confidence": 0.7},
}


class TestStageTimer(unittest.TestCase):
    def test_stage_records_and_summarizes(self):
        t = StageTimer()
        t.stage("htf")
        t.stage("bars_eval")
        d = t.as_dict()
        self.assertIn("htf", d["stages"])
        self.assertIn("bars_eval", d["stages"])
        self.assertIn("htf=", t.summary())
        self.assertGreaterEqual(d["total_sec"], 0.0)


class TestEarlyScanTelegram(unittest.TestCase):
    def test_live_scan_telegram_before_dashboard_sync(self):
        """SCAN TG fires after take is known and before dashboard sync / enter."""
        order: list[str] = []
        refresh_flags: list[bool | None] = []

        def fake_notify(msg: str) -> None:
            order.append("telegram")
            self.assertIn("Peak Hour SCAN hour=10", msg)
            self.assertIn("HTF=1 launches=1 take=0", msg)
            self.assertNotIn("entered=", msg)

        def fake_push(*_a, **kwargs):
            order.append("sync")
            refresh_flags.append(kwargs.get("refresh_missed_peaks"))
            return {}

        now = ET.localize(datetime(2026, 9, 14, 10, 15))
        with patch.object(scan, "load_polygon_key", return_value="test-key"), \
             patch.object(scan, "build_htf_universe", return_value={"rows": []}), \
             patch.object(scan, "htf_pass_symbols", return_value=["AAA"]), \
             patch.object(scan, "load_state", return_value={"positions": []}), \
             patch.object(scan, "save_state"), \
             patch.object(scan, "reset_scan_counter"), \
             patch.object(scan, "evaluate_1h_symbol", return_value=dict(PASSER)), \
             patch.object(scan, "rank_1h_launches", return_value=[dict(PASSER)]), \
             patch.object(scan, "_non_new_risk_symbols", return_value=set()), \
             patch.object(scan, "expire_stale_confirmed", return_value=[]), \
             patch.object(scan, "_write_launch_artifact"), \
             patch.object(scan, "_persist_funnel"), \
             patch(
                 "tsd_scan_pipeline.tsd_attention.build_attention_pool",
                 return_value=[dict(PASSER)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.review_attention_pool",
                 return_value=[dict(PASSER)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.select_enter_rows",
                 return_value=[],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.alert_case_rank_disagreement",
                 return_value=None,
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_notify.notify_tsd",
                 side_effect=fake_notify,
             ), \
             patch(
                 "tsd_supabase_sync.push_dashboard_best_effort",
                 side_effect=fake_push,
             ), \
             patch(
                 "tsd_scan_pipeline.php_missed_ledger.record_scan_outcomes",
                 return_value=0,
             ), \
             patch(
                 "tsd_scan_pipeline.php_missed_ledger.mark_ran_up",
                 side_effect=AssertionError("mark_ran_up must not run on launch hot path"),
             ):
            rc = scan.run_1h_launch_scan(live=True, now=now)

        self.assertEqual(rc, 0)
        self.assertIn("telegram", order)
        self.assertIn("sync", order)
        self.assertLess(order.index("telegram"), order.index("sync"))
        self.assertEqual(refresh_flags, [False])
        self.assertNotIn("enter", order)

    def test_live_scan_telegram_before_tws_enter_when_take_exists(self):
        """2026-09-14 failure: SCAN TG must not wait for clientId 93 enter/sync."""
        order: list[str] = []

        def fake_notify(msg: str) -> None:
            order.append("telegram")
            self.assertIn("Peak Hour SCAN hour=10", msg)
            self.assertIn("take=1", msg)
            self.assertNotIn("entered=", msg)

        def fake_push(*_a, **_k):
            order.append("sync")
            return {}

        class _FakeIB:
            last_connect: tuple | None = None

            def connect(self, host, port, clientId=None, timeout=None):
                _FakeIB.last_connect = (host, port, clientId)
                order.append("enter")
                raise ConnectionError("paper TWS not available in unit test")

            def disconnect(self):
                return None

        fake_ib = types.ModuleType("ib_insync")
        fake_ib.IB = _FakeIB
        fake_ib.util = types.SimpleNamespace(startLoop=lambda: None)

        now = ET.localize(datetime(2026, 9, 14, 10, 15))
        with patch.dict(sys.modules, {"ib_insync": fake_ib}), \
             patch.object(scan, "load_polygon_key", return_value="test-key"), \
             patch.object(scan, "build_htf_universe", return_value={"rows": []}), \
             patch.object(scan, "htf_pass_symbols", return_value=["AAA"]), \
             patch.object(scan, "load_state", return_value={"positions": []}), \
             patch.object(scan, "save_state"), \
             patch.object(scan, "reset_scan_counter"), \
             patch.object(scan, "evaluate_1h_symbol", return_value=dict(PASSER)), \
             patch.object(scan, "rank_1h_launches", return_value=[dict(TAKE)]), \
             patch.object(scan, "_non_new_risk_symbols", return_value=set()), \
             patch.object(scan, "expire_stale_confirmed", return_value=[]), \
             patch.object(scan, "_write_launch_artifact"), \
             patch.object(scan, "_persist_funnel"), \
             patch(
                 "tsd_scan_pipeline.tsd_attention.build_attention_pool",
                 return_value=[dict(TAKE)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.review_attention_pool",
                 return_value=[dict(TAKE)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.select_enter_rows",
                 return_value=[dict(TAKE)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.alert_case_rank_disagreement",
                 return_value=None,
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_notify.notify_tsd",
                 side_effect=fake_notify,
             ), \
             patch(
                 "tsd_supabase_sync.push_dashboard_best_effort",
                 side_effect=fake_push,
             ), \
             patch(
                 "tsd_scan_pipeline.php_missed_ledger.record_scan_outcomes",
                 return_value=0,
             ):
            rc = scan.run_1h_launch_scan(live=True, now=now)

        self.assertEqual(rc, 1)
        self.assertEqual(_FakeIB.last_connect, ("127.0.0.1", 7497, 93))
        self.assertEqual(order[:2], ["telegram", "enter"])
        self.assertLess(order.index("telegram"), order.index("enter"))
        self.assertLess(order.index("enter"), order.index("sync"))

    def test_dry_scan_skips_telegram_and_sync(self):
        order: list[str] = []
        now = ET.localize(datetime(2026, 9, 14, 10, 15))
        with patch.object(scan, "load_polygon_key", return_value="test-key"), \
             patch.object(scan, "build_htf_universe", return_value={"rows": []}), \
             patch.object(scan, "htf_pass_symbols", return_value=["AAA"]), \
             patch.object(scan, "load_state", return_value={"positions": []}), \
             patch.object(scan, "save_state"), \
             patch.object(scan, "reset_scan_counter"), \
             patch.object(scan, "evaluate_1h_symbol", return_value=dict(PASSER)), \
             patch.object(scan, "rank_1h_launches", return_value=[dict(PASSER)]), \
             patch.object(scan, "_non_new_risk_symbols", return_value=set()), \
             patch.object(scan, "expire_stale_confirmed", return_value=[]), \
             patch.object(scan, "_write_launch_artifact"), \
             patch.object(scan, "_persist_funnel"), \
             patch(
                 "tsd_scan_pipeline.tsd_attention.build_attention_pool",
                 return_value=[dict(PASSER)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.review_attention_pool",
                 return_value=[dict(PASSER)],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.select_enter_rows",
                 return_value=[],
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_case_review.alert_case_rank_disagreement",
                 return_value=None,
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_notify.notify_tsd",
                 side_effect=lambda *_a, **_k: order.append("telegram"),
             ), \
             patch(
                 "tsd_supabase_sync.push_dashboard_best_effort",
                 side_effect=lambda *_a, **_k: order.append("sync"),
             ), \
             patch(
                 "tsd_scan_pipeline.php_missed_ledger.record_scan_outcomes",
                 return_value=0,
             ):
            rc = scan.run_1h_launch_scan(live=False, now=now)
        self.assertEqual(rc, 0)
        self.assertEqual(order, [])


class TestGhostConfirmedExclude(unittest.TestCase):
    """_non_new_risk_symbols must not permanently exclude flat historical CONFIRMEDs."""

    def test_stale_confirmed_flat_not_excluded(self):
        now = ET.localize(datetime(2026, 9, 15, 8, 15))
        book = {"positions": []}
        queue = {
            "queue": [
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
            ]
        }
        with patch(
            "tsd_scan_pipeline.tsd_watch_queue.load_queue", return_value=queue,
        ), patch.object(scan, "load_state", return_value=book):
            skip = scan._non_new_risk_symbols(book, now=now)
        self.assertEqual(skip, set())

    def test_open_position_excluded(self):
        now = ET.localize(datetime(2026, 9, 15, 8, 15))
        book = {"positions": [{"symbol": "NUAI", "status": "OPEN"}]}
        queue = {
            "queue": [
                {
                    "symbol": "NUAI",
                    "status": "CONFIRMED",
                    "confirmed_at": "2026-09-11T10:22:00-04:00",
                },
            ]
        }
        with patch(
            "tsd_scan_pipeline.tsd_watch_queue.load_queue", return_value=queue,
        ):
            skip = scan._non_new_risk_symbols(book, now=now)
        self.assertEqual(skip, {"NUAI"})

    def test_todays_confirmed_in_flight_excluded(self):
        now = ET.localize(datetime(2026, 9, 15, 9, 15))
        book = {"positions": []}
        queue = {
            "queue": [
                {
                    "symbol": "IRD",
                    "status": "CONFIRMED",
                    "confirmed_at": "2026-09-15T08:18:00-04:00",
                },
            ]
        }
        with patch(
            "tsd_scan_pipeline.tsd_watch_queue.load_queue", return_value=queue,
        ):
            skip = scan._non_new_risk_symbols(book, now=now)
        self.assertEqual(skip, {"IRD"})

    def test_enter_exclude_reasons_open_confirmed_popularity(self):
        """CASE ENTER skip reasons distinguish open vs confirmed vs popularity."""
        rows = [
            {
                "symbol": "NUAI",
                "case_verdict": "ENTER",
                "tradable_popular": True,
            },
            {
                "symbol": "IRD",
                "case_verdict": "ENTER",
                "tradable_popular": True,
            },
            {
                "symbol": "ELMT",
                "case_verdict": "ENTER",
                "tradable_popular": False,
                "recent_leaderboard": False,
                "on_gainers": False,
            },
        ]
        self.assertEqual(
            scan._enter_exclude_reason(
                rows[0], open_syms={"NUAI"}, confirmed_inflight=set(),
            ),
            "open",
        )
        self.assertEqual(
            scan._enter_exclude_reason(
                rows[1], open_syms=set(), confirmed_inflight={"IRD"},
            ),
            "confirmed_inflight",
        )
        self.assertEqual(
            scan._enter_exclude_reason(
                rows[2], open_syms=set(), confirmed_inflight=set(),
            ),
            "popularity",
        )
        take = [
            {**rows[0], "tradable_popular": True},
        ]
        # NUAI taken; IRD confirmed; ELMT popularity
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            scan._log_case_enter_exclusions(
                rows,
                take,
                open_syms=set(),
                confirmed_inflight={"IRD"},
            )
        text = buf.getvalue()
        self.assertIn("CASE ENTER IRD excluded: confirmed_inflight", text)
        self.assertIn("CASE ENTER ELMT excluded: popularity", text)
        self.assertNotIn("CASE ENTER NUAI excluded", text)
        self.assertIn("ENTER skip confirmed_inflight: ['IRD']", text)
        self.assertIn("ENTER skip popularity: ['ELMT']", text)


class TestEqualSignalLaunchBanner(unittest.TestCase):
    def test_banner_reports_equal_signal_on_outside_hour_window(self):
        """Every 1H tick prints score=v1.6+equal_signal equal_signal=ON by default."""
        now = ET.localize(datetime(2026, 9, 15, 3, 15))
        prev = os.environ.pop("PHP_EQUAL_SIGNAL", None)
        buf = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as td, \
                 patch("tsd_scan_pipeline.tsd_launch_score._REPO_ROOT", Path(td)), \
                 redirect_stdout(buf):
                rc = scan.run_1h_launch_scan(live=False, now=now)
        finally:
            if prev is None:
                os.environ.pop("PHP_EQUAL_SIGNAL", None)
            else:
                os.environ["PHP_EQUAL_SIGNAL"] = prev
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("score=v1.6+equal_signal", text)
        self.assertIn("equal_signal=ON", text)
        self.assertIn("momentum_rank=", text)
        self.assertNotRegex(text, r"score=v1\.6\s+slots=")


if __name__ == "__main__":
    unittest.main()
