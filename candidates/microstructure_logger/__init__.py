"""
Research-only READ-ONLY L2 + tape logger (Features A + B).

Hard-pinned to TWS paper 127.0.0.1:7497 clientId 72.
Does not import or mutate Peak Hour / TSD strategy modules.
Feature C (confirms) and D (options) are deferred.
"""
from __future__ import annotations

from .constants import DEPTH_SOURCE, TEST01_REQUIRED_KEYS, TWS_CLIENT_ID
from .depth import DEFAULT_DEPTH_MAX
from .schema import assert_row, build_row, empty_row

__all__ = [
    "DEFAULT_DEPTH_MAX",
    "DEPTH_SOURCE",
    "TEST01_REQUIRED_KEYS",
    "TWS_CLIENT_ID",
    "assert_row",
    "build_row",
    "empty_row",
]
