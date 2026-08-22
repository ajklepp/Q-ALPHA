"""
Shared state file paths for local dev vs Modal volume.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CANDIDATES_DIR = Path(__file__).resolve().parent
VOLUME_MOUNT = "/state"
ET = ZoneInfo("America/New_York")


def state_path(filename: str) -> Path:
    """Return path for a persisted state file (Modal volume or local candidates/)."""
    if os.environ.get("MODAL_ENVIRONMENT") == "1":
        path = Path(VOLUME_MOUNT) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return CANDIDATES_DIR / filename


def is_trading_day(check_date: date | None = None) -> bool:
    """
    Return False on weekends and major US market holidays.

    Default "today" is the US/Eastern calendar date (same TZ as the agent
    scheduler) — never the machine's local date, which can disagree with ET
    near midnight and break the weekend guard.
    """
    today = check_date if check_date is not None else datetime.now(ET).date()

    if today.weekday() >= 5:
        return False

    holidays_2026 = [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 11, 27),
        date(2026, 12, 25),
    ]

    return today not in holidays_2026
