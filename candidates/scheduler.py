# =============================================================================
# Q-ALPHA MODAL SCHEDULER — Automated weekday trading pipeline
# =============================================================================
#
# SETUP REQUIRED (run once in terminal):
#   modal secret create q-alpha-secrets \
#     POLYGON_API_KEY=your_key \
#     TELEGRAM_BOT_TOKEN=your_token \
#     TELEGRAM_CHAT_ID=your_chat_id \
#     OPENROUTER_API_KEY=your_key
#
# STEP 1: Create Modal secrets (one time only)
#   modal secret create q-alpha-secrets \
#     POLYGON_API_KEY=... \
#     TELEGRAM_BOT_TOKEN=... \
#     TELEGRAM_CHAT_ID=... \
#     OPENROUTER_API_KEY=...
#
# STEP 2: Test manually before deploy
#   modal run candidates/scheduler.py::run_morning_scan
#   modal run candidates/scheduler.py::run_eod_monitor
#
# Approval processor runs LOCALLY at 9:25 AM (not on Modal):
#   python candidates/local_approval_runner.py
#   (See local_approval_runner.py for Windows Task Scheduler setup)
#
# STEP 3: Deploy the scheduler
#   modal deploy candidates/scheduler.py
#
# STEP 4: Confirm deployment
#   modal app list          ← should show qalpha-scheduler
#   modal app logs qalpha-scheduler
#
# Cron times (UTC) — EDT (UTC-4, summer). Add 1 hour for EST (winter):
#   8:30 AM EDT — run_morning_scan        ("30 12 * * 1-5")
#   9:25 AM EDT — local_approval_runner   ("25 13 * * 1-5", Windows Task Scheduler)
#   4:15 PM EDT — run_eod_monitor         ("15 20 * * 1-5")
#   6:00 AM EDT Mon — run_universe_refresh   ("0 10 * * 1", CS ticker list)
#   Every 30m 9:30-4:00 PM EDT — run_intraday_monitor ("*/30 13-20 * * 1-5")
# =============================================================================
from __future__ import annotations

import os
import sys
from pathlib import Path

import modal

CANDIDATES_DIR = Path(__file__).resolve().parent

app = modal.App("qalpha-scheduler")

volume = modal.Volume.from_name("qalpha-state", create_if_missing=True)
VOLUME_PATH = "/state"
CANDIDATES_MOUNT = "/root/candidates"

qalpha_secrets = modal.Secret.from_name("q-alpha-secrets")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "requests",
        "python-telegram-bot",
        "pandas",
        "numpy",
        "python-dotenv",
        "supabase",
        "tzdata",
        "pytz",
    ])
    .add_local_dir(str(CANDIDATES_DIR), remote_path=CANDIDATES_MOUNT)
)


def _commit_state() -> None:
    """Persist volume writes."""
    volume.commit()


def _prepare_modal_imports() -> None:
    """Ensure candidate modules resolve state to /state/ volume."""
    if CANDIDATES_MOUNT not in sys.path:
        sys.path.insert(0, CANDIDATES_MOUNT)


@app.function(
    image=image,
    schedule=modal.Cron("0 10 * * 1"),
    secrets=[qalpha_secrets],
    volumes={VOLUME_PATH: volume},
    timeout=1800,
    memory=1024,
)
def run_universe_refresh():
    """
    6:00 AM EDT Monday — refresh CS ticker universe from Polygon.
    """
    import os

    os.environ["MODAL_ENVIRONMENT"] = "1"
    _prepare_modal_imports()
    from pre_market_scanner import load_dotenv_if_available, refresh_universe

    load_dotenv_if_available()
    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not set")

    try:
        refresh_universe(api_key)
    finally:
        _commit_state()


@app.function(
    image=image,
    schedule=modal.Cron("30 12 * * 1-5"),
    secrets=[qalpha_secrets],
    volumes={VOLUME_PATH: volume},
    timeout=600,
    memory=2048,
)
def run_morning_scan():
    """
    8:30 AM ET (approx) Monday-Friday.
    Scans for gaps, sizes orders, saves pending_approvals.json, sends alerts.
    Exits immediately — no waiting for human replies.
    """
    import os
    from datetime import datetime

    import pytz

    os.environ["MODAL_ENVIRONMENT"] = "1"
    _prepare_modal_imports()

    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    print(f"Scanner started: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Expected: 8:30 AM ET — Actual: {now_et.strftime('%H:%M %Z')}")

    from pre_market_scanner import run_scan

    try:
        run_scan()
    finally:
        _commit_state()


@app.function(
    image=image,
    schedule=modal.Cron("*/30 13-20 * * 1-5"),
    secrets=[qalpha_secrets],
    volumes={VOLUME_PATH: volume},
    timeout=120,
    memory=512,
)
def run_intraday_monitor():
    """
    Every 30 min between 9:30 AM - 4:00 PM EDT (weekdays).
    Fetches live prices, updates Supabase for dashboard.
    """
    import os
    from datetime import datetime, time as dtime

    import pytz

    os.environ["MODAL_ENVIRONMENT"] = "1"
    _prepare_modal_imports()

    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)

    if not (dtime(9, 30) <= now_et.time() <= dtime(16, 0)):
        print(f"Outside market hours: {now_et.strftime('%H:%M ET')}")
        return

    print(f"Intraday monitor started: {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    from intraday_monitor import run_intraday_monitor as intraday_monitor_run

    intraday_monitor_run()


@app.function(
    image=image,
    schedule=modal.Cron("15 20 * * 1-5"),
    secrets=[qalpha_secrets],
    volumes={VOLUME_PATH: volume},
    timeout=600,
    memory=1024,
)
def run_eod_monitor():
    """
    4:15 PM ET (approx) Monday-Friday.
    Fills MOC entries, checks bracket exits, updates pool, sends EOD report.
    """
    import os

    os.environ["MODAL_ENVIRONMENT"] = "1"
    _prepare_modal_imports()
    from position_monitor import run_monitor

    try:
        run_monitor()
    finally:
        _commit_state()


@app.local_entrypoint()
def main():
    print("Q-Alpha scheduler deployed via: modal deploy candidates/scheduler.py")
    print("Manual runs:")
    print("  modal run candidates/scheduler.py::run_morning_scan")
    print("  modal run candidates/scheduler.py::run_universe_refresh")
    print("  modal run candidates/scheduler.py::run_intraday_monitor")
    print("  modal run candidates/scheduler.py::run_eod_monitor")
    print("Local approval (9:25 AM ET, TWS required):")
    print("  python candidates/local_approval_runner.py")
