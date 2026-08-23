"""
strategy_lab/forward_runtime.py — LIVE entry/settle helpers (lock, wait, cache).

Used by live_forward.py. Does not touch candidates/.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
BARS_DIR = LAB / "results" / "bars"
DAILY_CACHE_DIR = LAB / "results" / "daily_cache"
LOCK_PATH = LAB / "results" / ".live_lock"
LOCK_STALE_SEC = 2 * 3600  # 2 hours

WAIT_0930_POLL_SEC = 60
WAIT_0930_DEADLINE = dtime(10, 5)  # ET


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except AttributeError:
        # Windows: os.kill exists; fallback
        try:
            import ctypes

            k = ctypes.windll.kernel32
            handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED
            if handle:
                k.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False


def acquire_live_lock(mode: str) -> None:
    """
    LIVE-only lockfile. Raises RuntimeError if another live run holds the lock.
    Stale (>2h or dead pid) locks are taken over.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            doc = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        pid = int(doc.get("pid") or 0)
        started = str(doc.get("started_at") or "")
        age = None
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - started_dt.astimezone(timezone.utc)).total_seconds()
        except Exception:
            age = None
        if pid and _pid_alive(pid) and (age is None or age < LOCK_STALE_SEC):
            raise RuntimeError(
                f"live lock held by pid={pid} started_at={started} "
                f"(delete {LOCK_PATH.name} if stuck)"
            )
        print(
            f"[live_forward] taking over stale lock "
            f"(pid={pid} alive={_pid_alive(pid)} age={age})"
        )
    payload = {
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "mode": mode,
    }
    LOCK_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def release_live_lock() -> None:
    try:
        if not LOCK_PATH.exists():
            return
        try:
            doc = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = {}
        if int(doc.get("pid") or 0) not in (0, os.getpid()):
            return
        LOCK_PATH.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[live_forward] WARN: could not release lock ({exc})")


def same_day_entry_blocked(state: dict[str, Any] | None, today: str) -> bool:
    """True if LIVE entry should refuse (status complete for today)."""
    if not state:
        return False
    return (
        str(state.get("flag_date") or "")[:10] == today
        and str(state.get("status") or "") == "complete"
    )


def cache_mtime_et(path: Path) -> datetime | None:
    if not path.exists():
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=ET)
    except OSError:
        return None


def cache_stale_for_settle(path: Path, flag_date: str) -> bool:
    """
    Cache whose mtime is before 16:00 ET on flag_date is stale for settlement
    once that clock time has passed.
    """
    mt = cache_mtime_et(path)
    if mt is None:
        return True
    try:
        fd = date.fromisoformat(flag_date[:10])
    except ValueError:
        return True
    cutoff = datetime.combine(fd, dtime(16, 0), tzinfo=ET)
    now = datetime.now(ET)
    if now < cutoff:
        return False  # morning — cache may still be filling
    return mt < cutoff


def refresh_minute_bars(ticker: str, flag_date: str) -> list[dict]:
    """Fetch full-day 1-min bars from Polygon and overwrite results/bars cache."""
    from entry_study import fetch_minute_bars, load_polygon_key

    api_key = load_polygon_key()
    raw = fetch_minute_bars(ticker.upper(), flag_date[:10], api_key)
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    path = BARS_DIR / f"{ticker.upper()}_{flag_date[:10]}.json"
    bars = list(raw or [])
    path.write_text(
        json.dumps(
            {
                "ticker": ticker.upper(),
                "flag_date": flag_date[:10],
                "bars": bars,
                "refreshed_at": _now_iso(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    time.sleep(0.12)
    return bars


def refresh_daily_bars(ticker: str, flag_date: str) -> list[dict]:
    """Force-refresh daily cache via replay.load_daily_cached(refresh=True)."""
    from replay import load_daily_cached

    return load_daily_cached(ticker.upper(), flag_date[:10], refresh=True)


def trading_days_elapsed(flag_date: str, as_of: date | None = None) -> int:
    """Inclusive weekday count from flag_date through as_of (ET today default)."""
    start = date.fromisoformat(flag_date[:10])
    end = as_of or datetime.now(ET).date()
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def mfe_hold_window_ready(
    flag_date: str,
    daily_bars: list[dict],
    *,
    max_hold_days: int,
    as_of: date | None = None,
) -> bool:
    """
    True when the unrestricted MFE window can be finalized:
    enough post-flag daily bars OR enough trading days have elapsed.
    """
    post = [
        d for d in daily_bars
        if str(d.get("date") or "")[:10] > flag_date[:10]
    ]
    if len(post) >= max(0, max_hold_days - 1):
        return True
    return trading_days_elapsed(flag_date, as_of) >= max_hold_days


def wait_for_0930_bar(
    candidates: list[dict[str, Any]],
    flag_date: str,
    *,
    mode: str,
    load_bars_fn,
    immediate_fn,
) -> tuple[bool, float]:
    """
    Poll until immediate(bars) works for the first candidate, or 10:05 ET.
    Returns (ok, waited_seconds). Replay: single attempt (no sleep).
    """
    if not candidates:
        return False, 0.0
    t0 = time.monotonic()
    first = candidates[0]["ticker"]
    if mode != "live":
        bars = load_bars_fn(first, flag_date, refresh=False)
        ok = immediate_fn(bars) is not None
        return ok, time.monotonic() - t0

    while True:
        now_et = datetime.now(ET)
        bars = load_bars_fn(first, flag_date, refresh=True)
        sig = immediate_fn(bars) if bars else None
        if sig is not None:
            waited = time.monotonic() - t0
            print(
                f"[live_forward] 09:30 bar ready for {first} "
                f"(waited {waited:.0f}s)"
            )
            return True, waited
        if now_et.time() >= WAIT_0930_DEADLINE:
            waited = time.monotonic() - t0
            print(
                f"[live_forward] 09:30 bar TIMEOUT for {first} "
                f"after {waited:.0f}s (deadline {WAIT_0930_DEADLINE})"
            )
            return False, waited
        print(
            f"[live_forward] waiting for 09:30 bar ({first}) … "
            f"now={now_et.strftime('%H:%M:%S')} ET"
        )
        time.sleep(WAIT_0930_POLL_SEC)
