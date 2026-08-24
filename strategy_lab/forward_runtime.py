"""
strategy_lab/forward_runtime.py — LIVE entry/settle helpers (lock, wait, cache).

Used by live_forward.py. Reads holiday calendar from candidates/state_paths
(import only — does not modify candidates/).
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
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

# Feed-lag probe: expected ~15 min by entitlement; >20 min ⇒ WARN (degradation).
FEED_LAG_CSV = LAB / "results" / "feed_lag.csv"
FEED_LAG_WARN_SEC = 20 * 60

# Windows process-query constants for _pid_alive.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _freshest_bar_utc(minute_bars: list[dict]) -> datetime | None:
    """Best-effort UTC timestamp of the newest minute bar."""
    best: datetime | None = None
    for b in minute_bars or []:
        ts = b.get("t")
        dt: datetime | None = None
        if isinstance(ts, (int, float)):
            # Polygon ms epoch
            sec = float(ts) / 1000.0 if float(ts) > 1e12 else float(ts)
            dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        elif b.get("t_et"):
            try:
                raw = str(b["t_et"]).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ET)
                dt = dt.astimezone(timezone.utc)
            except Exception:
                dt = None
        if dt is not None and (best is None or dt > best):
            best = dt
    return best


def log_feed_lag(ticker: str, flag_date: str, minute_bars: list[dict]) -> float | None:
    """
    Pure logging: observed_lag = now_utc - freshest_bar_timestamp.

    Expected lag ~15 min by Stocks Developer entitlement; this probe detects
    degradation. INFO when lag <= 20 min; WARN when lag > 20 min.
    Appends results/feed_lag.csv. Never mutates trading state. Never raises.
    """
    try:
        freshest = _freshest_bar_utc(minute_bars)
        if freshest is None:
            print(f"  feed_lag {ticker}: no bar timestamps — skip probe")
            return None
        now = datetime.now(timezone.utc)
        lag_sec = (now - freshest).total_seconds()
        level = "INFO" if lag_sec <= FEED_LAG_WARN_SEC else "WARN"
        print(
            f"  feed_lag {level} {ticker}|{flag_date[:10]}  "
            f"lag={lag_sec / 60.0:.1f} min  "
            f"(expected ~15 min delayed entitlement)"
        )
        FEED_LAG_CSV.parent.mkdir(parents=True, exist_ok=True)
        new_file = not FEED_LAG_CSV.exists()
        with FEED_LAG_CSV.open("a", encoding="utf-8") as fh:
            if new_file:
                fh.write("date,ticker,lag_seconds,level,freshest_bar_utc,observed_at_utc\n")
            fh.write(
                f"{flag_date[:10]},{str(ticker).upper()},{lag_sec:.1f},"
                f"{level},{freshest.isoformat()},{now.isoformat()}\n"
            )
        return lag_sec
    except Exception as exc:
        print(f"  feed_lag WARN probe skipped ({exc})")
        return None


def _pid_alive(pid: int) -> bool:
    """
    True if pid looks like a live process.

    Windows: OpenProcess + GetExitCodeProcess (STILL_ACTIVE=259).
    Never use os.kill(pid, 0) on Windows — that maps to CTRL_C_EVENT.
    POSIX: os.kill(pid, 0); PermissionError means the process exists.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        k = ctypes.windll.kernel32
        handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == _STILL_ACTIVE
        finally:
            k.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not signalable by this user
    except OSError:
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
    once that clock time has passed (W4 — morning caches must be overwritten).
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
    # No sleep: Stocks Developer plan = unlimited REST (15-min delayed).
    return bars


def refresh_daily_bars(ticker: str, flag_date: str) -> list[dict]:
    """Force-refresh daily cache via replay.load_daily_cached(refresh=True)."""
    from replay import load_daily_cached

    return load_daily_cached(ticker.upper(), flag_date[:10], refresh=True)


def _is_session_day(d: date) -> bool:
    """Weekday and not on the NYSE holiday list (via candidates/state_paths)."""
    if str(ROOT / "candidates") not in sys.path:
        sys.path.insert(0, str(ROOT / "candidates"))
    from state_paths import is_trading_day

    return bool(is_trading_day(d))


def trading_days_elapsed(flag_date: str, as_of: date | None = None) -> int:
    """
    Inclusive session-day count from flag_date through as_of (ET today default).

    Holiday-aware via state_paths.is_trading_day (2026 list; weekends excluded).
    """
    start = date.fromisoformat(flag_date[:10])
    end = as_of or datetime.now(ET).date()
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if _is_session_day(cur):
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
    True when the unrestricted MFE window can be finalized.

    Prefer the daily-bars count when post-flag bars exist (authoritative).
    Elapsed-days fallback is holiday-aware (not raw weekdays).
    """
    post = [
        d for d in daily_bars
        if str(d.get("date") or "")[:10] > flag_date[:10]
    ]
    need_post = max(0, max_hold_days - 1)
    if post and len(post) >= need_post:
        return True
    if post and len(post) < need_post:
        # Bars present but window incomplete — do not finalize early via calendar.
        return False
    # No usable post-flag dailies yet: calendar fallback (holiday-aware).
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
    Poll until immediate(bars) works for ANY candidate, or 10:05 ET.

    LOAD-BEARING (not insurance): Polygon/Massive Stocks Developer is a
    15-MINUTE DELAYED feed. The 09:30 1-min bar typically becomes visible
    only ~09:45–09:50 ET. Without this wait, live entry sees no open bar
    and/or truncated morning data. REST calls are unlimited — poll freely;
    do not add throttle/backoff here.

    Polls ALL candidates each cycle so a halted top gapper cannot block the
    whole day (per-candidate entry still skips names with no_0930_bar).
    Replay: single pass over all candidates (no sleep).

    Returns (ok, waited_seconds).
    """
    if not candidates:
        return False, 0.0
    t0 = time.monotonic()
    tickers = [str(c["ticker"]).upper() for c in candidates]

    def _any_ready(*, refresh: bool) -> str | None:
        for t in tickers:
            bars = load_bars_fn(t, flag_date, refresh=refresh)
            if bars and immediate_fn(bars) is not None:
                return t
        return None

    if mode != "live":
        hit = _any_ready(refresh=False)
        return hit is not None, time.monotonic() - t0

    # LIVE: keep refetching until the delayed 09:30 bar appears on any name.
    while True:
        now_et = datetime.now(ET)
        hit = _any_ready(refresh=True)
        if hit is not None:
            waited = time.monotonic() - t0
            print(
                f"[live_forward] 09:30 bar ready for {hit} "
                f"(waited {waited:.0f}s; polled {len(tickers)} names)  "
                f"[15-min delayed feed]"
            )
            return True, waited
        if now_et.time() >= WAIT_0930_DEADLINE:
            waited = time.monotonic() - t0
            print(
                f"[live_forward] 09:30 bar TIMEOUT after {waited:.0f}s "
                f"(deadline {WAIT_0930_DEADLINE}; tried {tickers})"
            )
            return False, waited
        print(
            f"[live_forward] waiting for 09:30 bar among {tickers} … "
            f"now={now_et.strftime('%H:%M:%S')} ET  "
            f"(delayed feed; bar expected ~09:45–09:50)"
        )
        time.sleep(WAIT_0930_POLL_SEC)
