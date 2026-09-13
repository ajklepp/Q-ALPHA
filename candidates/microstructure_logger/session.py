"""
US/Eastern session tags for microstructure rows.

WHY: Features are session-dependent; TEST-01 and the A+B plan require
session_tag on every row (RTH / PRE / POST). Default ops are RTH-primary.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .constants import (
    POST_END,
    PRE_START,
    RTH_END,
    RTH_START,
)

ET = ZoneInfo("America/New_York")

SESSION_RTH = "RTH"
SESSION_PRE = "PRE"
SESSION_POST = "POST"
SESSION_CLOSED = "CLOSED"


def to_et(ts: datetime) -> datetime:
    """Attach or convert a timestamp to US/Eastern. Naive values are treated as ET."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def session_tag(ts: datetime) -> str:
    """
    Label PRE / RTH / POST / CLOSED from an ET or UTC timestamp.

    Windows (weekday):
      PRE  04:00 <= t < 09:30
      RTH  09:30 <= t < 16:00
      POST 16:00 <= t < 20:00
    Weekends and overnight are CLOSED.
    """
    local = to_et(ts)
    if local.weekday() >= 5:
        return SESSION_CLOSED
    clock = local.timetz().replace(tzinfo=None)
    if PRE_START <= clock < RTH_START:
        return SESSION_PRE
    if RTH_START <= clock < RTH_END:
        return SESSION_RTH
    if RTH_END <= clock < POST_END:
        return SESSION_POST
    return SESSION_CLOSED


def is_rth(ts: datetime) -> bool:
    """True only during weekday regular hours (09:30–16:00 ET)."""
    return session_tag(ts) == SESSION_RTH


def in_extended_or_rth(ts: datetime) -> bool:
    """True during PRE, RTH, or POST on a weekday."""
    return session_tag(ts) in {SESSION_PRE, SESSION_RTH, SESSION_POST}


def should_stream(ts: datetime, *, allow_extended: bool) -> bool:
    """
    RTH-primary gate: stream only in RTH unless --allow-extended.

    WHY: Paper L2 outside RTH is thinner and not the TEST-01 research window.
    """
    if allow_extended:
        return in_extended_or_rth(ts)
    return is_rth(ts)
