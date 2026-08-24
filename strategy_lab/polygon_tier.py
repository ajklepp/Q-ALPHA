"""
strategy_lab/polygon_tier.py — confirmed Polygon/Massive plan facts (docs layer).

Single source of truth for entitlement assumptions. Import for logging/docs only.
Do NOT wire into strategy math, sizing, or lookbacks.

Verified 2026-08-23 from polygon.io / massive.com pricing (Stocks Developer $79/mo).
Step 0 (Aaron): confirm dashboard plan page + API base URL still api.polygon.io
(vs api.massive.com) and set TRADES below if the dashboard disagrees.
"""
from __future__ import annotations

from typing import Any

# --- Confirmed tier constants -------------------------------------------------
POLYGON_PLAN = "stocks_developer_79"
BRAND = "Massive (Polygon)"  # Polygon rebrand; docs at massive.com
REAL_TIME = False
DELAY_MINUTES = 15
REST_CALLS = "unlimited"
HISTORY_YEARS = 10
SNAPSHOT = True
SECOND_AGGREGATES = True
MINUTE_AGGREGATES = True
WEBSOCKET = True  # same 15-min delay — no latency benefit vs REST
CORPORATE_ACTIONS = True
FLAT_FILES = True
# Confirm on dashboard (Trades entitlement listed on pricing; leave True if shown):
TRADES = True

API_BASE_URL = "https://api.polygon.io"  # Step 0: update if dashboard redirects to api.massive.com
DOCS_HOME = "https://massive.com"  # llms.txt / .md available
CLIENT_PYTHON = "massive-com/client-python"


def tier_summary() -> dict[str, Any]:
    """Dict of plan facts for logging / handoff rendering."""
    return {
        "plan": POLYGON_PLAN,
        "brand": BRAND,
        "real_time": REAL_TIME,
        "delay_minutes": DELAY_MINUTES,
        "rest_calls": REST_CALLS,
        "history_years": HISTORY_YEARS,
        "snapshot": SNAPSHOT,
        "second_aggregates": SECOND_AGGREGATES,
        "minute_aggregates": MINUTE_AGGREGATES,
        "websocket": WEBSOCKET,
        "websocket_note": "same 15-min delay as REST",
        "corporate_actions": CORPORATE_ACTIONS,
        "flat_files": FLAT_FILES,
        "trades": TRADES,
        "api_base_url": API_BASE_URL,
        "docs_home": DOCS_HOME,
        "client_python": CLIENT_PYTHON,
    }


def describe_tier() -> str:
    """One-line human summary."""
    return (
        f"{BRAND} plan={POLYGON_PLAN}: "
        f"{DELAY_MINUTES}-min delayed, REST={REST_CALLS}, "
        f"real_time={REAL_TIME}"
    )
