"""
Append-only JSONL writer under logs/microstructure/YYYYMMDD/{symbol}.jsonl.

WHY: Research rows stay on disk next to the repo (Documents/Q-ALPHA layout)
without touching Peak Hour state files.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .schema import assert_row


class JsonlWriter:
    """One open file handle per symbol per UTC date; rotates at UTC midnight."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._handles: dict[tuple[str, str], TextIO] = {}

    def path_for(self, symbol: str, ts_utc: datetime | None = None) -> Path:
        """Return the JSONL path for symbol on the UTC calendar date of ts."""
        when = ts_utc or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        else:
            when = when.astimezone(timezone.utc)
        day = when.strftime("%Y%m%d")
        return self.root / day / f"{symbol.upper()}.jsonl"

    def write(self, row: dict[str, Any]) -> Path:
        """Validate TEST-01 keys, append one line, return the file path."""
        assert_row(row)
        symbol = str(row["symbol"]).upper()
        ts_raw = str(row.get("ts_utc") or "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(timezone.utc)
        path = self.path_for(symbol, ts)
        day = path.parent.name
        key = (day, symbol)
        handle = self._handles.get(key)
        if handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8")
            self._handles[key] = handle
        handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
        handle.flush()
        return path

    def close(self) -> None:
        """Flush and close all symbol files."""
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                pass
        self._handles.clear()
