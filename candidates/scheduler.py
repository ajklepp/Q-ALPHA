# =============================================================================
# Q-ALPHA MODAL SCHEDULER — EOD + intraday monitoring (autonomous agent local)
# =============================================================================
#
# SETUP REQUIRED (run once in terminal):
#   modal secret create q-alpha-secrets \
#     POLYGON_API_KEY=your_key \
#     TELEGRAM_BOT_TOKEN=your_token \
#     TELEGRAM_CHAT_ID=your_chat_id \
#     OPENROUTER_API_KEY=your_key \
#     SUPABASE_URL=... \
#     SUPABASE_SECRET_KEY=...
#
# Morning scan + entries run LOCALLY via autonomous_agent.py (9:20 AM ET, TWS).
#
# STEP 2: Test manually before deploy
#   modal run candidates/scheduler.py::run_intraday_monitor
#   modal run candidates/scheduler.py::run_eod_monitor
#
# STEP 3: Deploy the scheduler
#   modal deploy candidates/scheduler.py
#
# IMPORTANT: After any change to intraday_monitor.py or supabase_sync.py,
# always re-run `modal deploy candidates/scheduler.py` so the :30 cron picks
# up mark-only updates (stale volume must not full-upsert CLOSED → OPEN).
# IBKR_PAPER agent marks are skipped on Modal — TWS sync (clientId 96) is SoT.
# Mark issues on Streamlit Cloud: check candidates/logs/tws_sync_*.log
# "Supabase verify" block before blaming dashboard code.
#
# STEP 4: Confirm deployment
#   modal app list          <- should show qalpha-scheduler
#   modal app logs qalpha-scheduler
#
# Cron times (UTC) — EDT (UTC-4, summer). Add 1 hour for EST (winter):
#   Every 30m 9:30-4:00 PM EDT — run_intraday_monitor ("*/30 13-20 * * 1-5")
#     → Polygon FALLBACK marks only (NOT TWS; Modal cannot reach 127.0.0.1:7497).
#     → Skips execution_mode=IBKR_PAPER (live paper marks = local TWS sync only).
#     → Does NOT close trades. Must never re-OPEN CLOSED / NEVER_FILLED.
#     → Live SoT marks + filled-flat→CLOSED: local tws_intraday_sync.py
#       (schtasks "QAlpha Live TWS Sync", clientId 96).
#   4:15 PM EDT — run_eod_monitor         ("15 20 * * 1-5")
#     → Real agent EOD (brackets / pool / Telegram). Lab morning "day summary"
#       is NOT this job; Lab settle is local ~16:20 ET (--settle).
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
    schedule=modal.Cron("*/30 13-20 * * 1-5"),
    secrets=[qalpha_secrets],
    volumes={VOLUME_PATH: volume},
    timeout=120,
    memory=512,
)
def run_intraday_monitor():
    """
    Every 30 min between 9:30 AM - 4:00 PM EDT (weekdays).
    Polygon fallback marks for still-OPEN rows only — not TWS, does not close.
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
    print("  modal run candidates/scheduler.py::run_intraday_monitor")
    print("  modal run candidates/scheduler.py::run_eod_monitor")
    print("Local autonomous agent (9:20 AM ET, TWS required):")
    print("  python candidates/autonomous_agent.py")
