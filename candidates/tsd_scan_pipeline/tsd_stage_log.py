"""
Wall-clock stage timings for Peak Hour 1H LAUNCH ticks.

Prints each phase with seconds so lag is diagnosable in scheduler logs
(stdout is often block-buffered when redirected — always flush).
"""
from __future__ import annotations

import time
from typing import Any


class StageTimer:
    """Named wall-clock stages from a shared t0 (never raises)."""

    def __init__(self) -> None:
        self.t0 = time.time()
        self._mark = self.t0
        self.stages: list[tuple[str, float]] = []

    def stage(self, name: str) -> float:
        """Close the current stage, print it, and start the next. Returns dt seconds."""
        now = time.time()
        dt = now - self._mark
        self._mark = now
        self.stages.append((name, dt))
        total = now - self.t0
        print(f"  STAGE {name}: {dt:.1f}s  (total {total:.1f}s)", flush=True)
        return dt

    def elapsed(self) -> float:
        """Seconds since timer start."""
        return time.time() - self.t0

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly stage map plus total."""
        return {
            "stages": {name: round(dt, 2) for name, dt in self.stages},
            "total_sec": round(self.elapsed(), 2),
        }

    def summary(self) -> str:
        """One-line stage dump for the tick footer."""
        parts = [f"{name}={dt:.1f}s" for name, dt in self.stages]
        return f"stages: {', '.join(parts)}  total={self.elapsed():.1f}s"
