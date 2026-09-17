"""
JSONL row schema for TEST-01 + A+B book/tape features.

WHY: Strategy Finder TEST-01 requires a fixed key set on every row.
Warm-up values may be null, but keys must always be present.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .constants import (
    DEPTH_SOURCE,
    ROW_KEYS,
    TEST01_REQUIRED_KEYS,
)


def json_safe(value: Any) -> Any:
    """Coerce NaN/inf to None so json.dumps(allow_nan=False) cannot lie."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def empty_row() -> dict[str, Any]:
    """All A+B keys present, values null — the TEST-01 contract."""
    return {key: None for key in ROW_KEYS}


def utc_iso(ts: datetime | None = None) -> str:
    """UTC ISO-8601 with Z suffix for ts_utc."""
    when = ts or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    return when.isoformat().replace("+00:00", "Z")


def build_row(
    *,
    ts_utc: str,
    symbol: str,
    session_tag: str,
    source: str,
    depth_source: str = DEPTH_SOURCE,
    levels_n: int | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Assemble one JSONL object with the full A+B key set.

    Unknown feature keys are ignored; missing A+B keys stay null.
    """
    row = empty_row()
    row["ts_utc"] = ts_utc
    row["symbol"] = str(symbol).upper()
    row["session_tag"] = session_tag
    row["source"] = source
    row["depth_source"] = depth_source or DEPTH_SOURCE
    row["levels_n"] = levels_n
    for key, val in (features or {}).items():
        if key in row:
            row[key] = json_safe(val)
    for key in ROW_KEYS:
        row[key] = json_safe(row[key])
    return row


def missing_required(row: dict[str, Any]) -> list[str]:
    """Return TEST-01 keys absent from the row (values may be None)."""
    return [k for k in TEST01_REQUIRED_KEYS if k not in row]


def assert_row(row: dict[str, Any]) -> dict[str, Any]:
    """Raise if TEST-01 or A+B keys are missing. Values may be null."""
    missing = missing_required(row)
    if missing:
        raise ValueError(f"TEST-01 schema missing keys: {missing}")
    absent = [k for k in ROW_KEYS if k not in row]
    if absent:
        raise ValueError(f"A+B schema missing keys: {absent}")
    return row
