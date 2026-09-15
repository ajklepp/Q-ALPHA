"""Hour-8 abort guard: overlapping 5-min ticks must not start a second launch."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pytz

from tsd_scan_pipeline import scheduler

ET = pytz.timezone("America/New_York")
REGISTER_PS1 = ROOT / "candidates" / "register_tsd_tasks.ps1"
STARTER_PS1 = ROOT / "candidates" / "start_tsd_scheduler_scheduled.ps1"


def _hold_lock_child(lock_path: Path) -> subprocess.Popen:
    """Child process that holds the scheduler flock until killed."""
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT / 'candidates')!r})\n"
        "from tsd_scan_pipeline import scheduler\n"
        f"scheduler.SCHEDULER_LOCK_PATH = Path({str(lock_path)!r})\n"
        "ok = scheduler.acquire_scheduler_tick_lock()\n"
        "sys.stdout.write('READY\\n' if ok else 'FAIL\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class TestSchedulerTickLock(unittest.TestCase):
    def setUp(self):
        scheduler.release_scheduler_tick_lock()

    def tearDown(self):
        scheduler.release_scheduler_tick_lock()

    def test_second_acquire_fails_while_other_process_holds_lock(self):
        """Two OS processes cannot both hold the hour-8 tick lock."""
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "tsd_scheduler_tick.lock"
            child = _hold_lock_child(lock_path)
            try:
                line = child.stdout.readline().strip() if child.stdout else ""
                self.assertEqual(line, "READY")
                with patch.object(scheduler, "SCHEDULER_LOCK_PATH", lock_path):
                    self.assertFalse(scheduler.acquire_scheduler_tick_lock())
            finally:
                child.kill()
                child.wait(timeout=5)

    def test_overlapping_tick_skips_launch_while_lock_held(self):
        """2026-09-14 hour-8: second 5-min tick must no-op, not start 1H LAUNCH."""
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "tsd_scheduler_tick.lock"
            child = _hold_lock_child(lock_path)
            try:
                line = child.stdout.readline().strip() if child.stdout else ""
                self.assertEqual(line, "READY")
                with patch.object(scheduler, "SCHEDULER_LOCK_PATH", lock_path), \
                     patch.object(scheduler, "run_launch_pass") as launch, \
                     patch.object(scheduler, "run_trail_pass") as trail:
                    rc = scheduler.tick(dry_run=False, live=True)
                self.assertEqual(rc, 0)
                launch.assert_not_called()
                # Second :20 tick must not start clientId 93 or tick-level
                # trail backup (08:24 trail restart / TWS contention class).
                trail.assert_not_called()
            finally:
                child.kill()
                child.wait(timeout=5)

    def test_tick_runs_due_launch_when_lock_free(self):
        """A free lock must still run the :15 1H LAUNCH (no trade-rule change)."""
        sched = ET.localize(datetime(2026, 9, 14, 8, 15))
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "tsd_scheduler_tick.lock"
            with patch.object(scheduler, "SCHEDULER_LOCK_PATH", lock_path), \
                 patch.object(scheduler, "is_trading_day", return_value=True), \
                 patch.object(scheduler, "_due_slots", return_value=[(8, sched)]), \
                 patch.object(scheduler, "run_launch_pass", return_value=0) as launch, \
                 patch.object(scheduler, "_mark_ran") as marked, \
                 patch.object(scheduler, "_due_clock", return_value=False), \
                 patch.object(scheduler, "_trail_backup_allowed", return_value=False), \
                 patch.object(scheduler, "_trail_loop_active", return_value=True):
                rc = scheduler.tick(dry_run=False, live=True)
            self.assertEqual(rc, 0)
            launch.assert_called_once_with(live=True)
            marked.assert_called_once_with("launch", 8, sched)
            self.assertIsNone(scheduler._SCHEDULER_LOCK_FH)


class TestTaskSchedulerInstancePolicy(unittest.TestCase):
    def test_register_script_sets_ignore_new_and_two_hour_limit(self):
        text = REGISTER_PS1.read_text(encoding="utf-8")
        self.assertIn('MultipleInstances = "IgnoreNew"', text)
        self.assertIn('ExecutionTimeLimit = "PT2H"', text)
        self.assertIn("Do not start a new instance", text)
        self.assertIn("hour-8", text)

    def test_starter_refuses_second_instance_without_killing_owner(self):
        text = STARTER_PS1.read_text(encoding="utf-8")
        self.assertIn("another TSD scheduler tick is in flight", text)
        self.assertIn("Do not start a new instance", text)
        self.assertIn("exit 0", text)
        self.assertNotIn("Stop-Process", text)
        self.assertNotIn("taskkill", text.lower())


if __name__ == "__main__":
    unittest.main()
