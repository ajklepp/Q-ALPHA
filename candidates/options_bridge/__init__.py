"""
Read-only local TWS options data bridge for Best Strategy Finder (BSF).

HTTP JSON on 127.0.0.1:8787. Talks to laptop TWS paper (7497, clientId 71).
BSF must not import ib_insync or log into IBKR — call this service instead.

This package is a thin IBKR market-data layer only. It does not import Peak Hour,
TSD, trail-monitor, or book-state modules, and it never places or cancels orders.
"""

from .config import (
    BIND_HOST,
    DEFAULT_PORT,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_PORT,
)

__all__ = [
    "BIND_HOST",
    "DEFAULT_PORT",
    "TWS_CLIENT_ID",
    "TWS_HOST",
    "TWS_PORT",
]
