"""
strategy_lab/batch_profile.py

For each unique (ticker, flag_date) in strategy_lab/results/setups.json,
run candidates/ticker_profiler.build_ticker_profile(..., as_of_date=flag_date)
and cache to strategy_lab/profiles/{TICKER}_{flag_date}.json.

Reuses the live profiler unchanged. Does NOT write to profiles/ or touch agents.

Usage (from repo root):
  py -3 strategy_lab/batch_profile.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from ticker_profiler import (  # noqa: E402
    _load_polygon_key,
    build_ticker_profile,
)

SETUPS_PATH = Path(__file__).resolve().parent / "results" / "setups.json"
LAB_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
INDEX_PATH = RESULTS_DIR / "batch_profiles_index.json"


def cache_path(ticker: str, flag_date: str) -> Path:
    """strategy_lab/profiles/{TICKER}_{YYYY-MM-DD}.json"""
    return LAB_PROFILES_DIR / f"{ticker.upper()}_{flag_date}.json"


def load_setups(path: Path) -> list[dict[str, str]]:
    """Unique (ticker, flag_date) pairs from setups.json, sorted."""
    data = json.loads(path.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for s in data.get("setups") or []:
        ticker = str(s.get("ticker") or "").upper().strip()
        flag_date = str(s.get("flag_date") or "")[:10]
        if not ticker or not flag_date:
            continue
        key = (ticker, flag_date)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": ticker, "flag_date": flag_date})
    out.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    return out


def insufficient_stub(
    ticker: str,
    flag_date: str,
    reason: str,
) -> dict[str, Any]:
    """Minimal INSUFFICIENT profile when profiling crashes or has no data."""
    return {
        "ticker": ticker.upper(),
        "as_of_date": flag_date,
        "informational_only": True,
        "stats_meaningful": False,
        "confidence": "INSUFFICIENT",
        "history_flag": "**",
        "analog_count": 0,
        "n_analogs_finder": 0,
        "n_analogs_measured": 0,
        "flag": "INSUFFICIENT_HISTORY",
        "percentiles": {},
        "bracket": {},
        "outcomes": {
            "win_rate": None,
            "reward_risk": None,
            "note": reason,
        },
        "error": reason,
        "lab_source": "strategy_lab.batch_profile",
    }


def summarize_fields(profile: dict[str, Any]) -> dict[str, Any]:
    """Pull the live-system fields used for lab tables / indexing."""
    pct = profile.get("percentiles") or {}
    mae = pct.get("mae") or {}
    mfe = pct.get("mfe") or {}
    bracket = profile.get("bracket") or {}
    outcomes = profile.get("outcomes") or {}
    n_analogs = (
        profile.get("analog_count")
        if profile.get("analog_count") is not None
        else profile.get("n_analogs_measured")
    )
    return {
        "ticker": profile.get("ticker"),
        "as_of_date": profile.get("as_of_date"),
        "confidence": profile.get("confidence"),
        "history_flag": profile.get("history_flag"),
        "n_analogs": n_analogs,
        "safe_max_stop_pct": bracket.get("safe_max_stop_pct"),
        "mfe_p25": mfe.get("p25"),  # live profiler currently has p50/p75/p90 only
        "mfe_p50": mfe.get("p50"),
        "mfe_p75": mfe.get("p75"),
        "mfe_p90": mfe.get("p90"),
        "mae_p50": mae.get("p50"),
        "mae_p75": mae.get("p75"),
        "mae_p90": mae.get("p90"),
        "win_rate": outcomes.get("win_rate"),
        "reward_risk": outcomes.get("reward_risk"),
        "stats_meaningful": profile.get("stats_meaningful"),
        "error": profile.get("error"),
    }


def _fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_rr(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "—"


def profile_one(
    ticker: str,
    flag_date: str,
    api_key: str,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], str]:
    """
    Load cache or build profile as_of flag_date.

    Returns (profile, status) where status is
    'cached' | 'ok' | 'insufficient' | 'error'.
    """
    path = cache_path(ticker, flag_date)
    if path.exists() and not force:
        profile = json.loads(path.read_text(encoding="utf-8"))
        conf = str(profile.get("confidence") or "INSUFFICIENT")
        if conf == "INSUFFICIENT" or not profile.get("stats_meaningful", True):
            return profile, "cached_insufficient"
        return profile, "cached"

    try:
        # Quieter batch: suppress per-analog progress from the profiler.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            profile = build_ticker_profile(
                ticker,
                flag_date,
                api_key=api_key,
            )
    except Exception as exc:
        profile = insufficient_stub(ticker, flag_date, str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return profile, "error"

    profile["lab_source"] = "strategy_lab.batch_profile"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    conf = str(profile.get("confidence") or "INSUFFICIENT")
    if conf == "INSUFFICIENT" or not profile.get("stats_meaningful", False):
        return profile, "insufficient"
    return profile, "ok"


def main() -> None:
    force = "--force" in sys.argv

    if not SETUPS_PATH.exists():
        raise SystemExit(f"Missing {SETUPS_PATH} — run collect_setups.py first")

    setups = load_setups(SETUPS_PATH)
    api_key = _load_polygon_key()

    LAB_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(setups)} unique setups from {SETUPS_PATH.relative_to(ROOT)}")
    print(f"Cache dir: {LAB_PROFILES_DIR.relative_to(ROOT)}")
    if force:
        print("(--force: rebuilding all profiles)")
    print("Profiling as_of each flag_date via ticker_profiler.build_ticker_profile…\n")

    index: dict[str, Any] = {}
    n_ok = 0
    n_insufficient = 0
    n_error = 0
    n_cached = 0
    conf_counts: Counter[str] = Counter()

    for i, setup in enumerate(setups, start=1):
        ticker = setup["ticker"]
        flag_date = setup["flag_date"]
        key = f"{ticker}|{flag_date}"
        print(f"[{i}/{len(setups)}] {key}", flush=True)

        profile, status = profile_one(
            ticker, flag_date, api_key, force=force,
        )
        summary = summarize_fields(profile)
        index[key] = {
            **summary,
            "cache_path": str(cache_path(ticker, flag_date).relative_to(ROOT)),
            "status": status,
        }

        conf = str(summary.get("confidence") or "INSUFFICIENT")
        conf_counts[conf] += 1

        if status in ("ok", "cached"):
            n_ok += 1
            if status == "cached":
                n_cached += 1
            print(
                f"    {status} conf={conf}  n={summary.get('n_analogs')}  "
                f"safe={_fmt_pct(summary.get('safe_max_stop_pct'))}  "
                f"wr={_fmt_pct(summary.get('win_rate'))}  "
                f"rr={_fmt_rr(summary.get('reward_risk'))}",
                flush=True,
            )
        elif status in ("insufficient", "cached_insufficient"):
            n_insufficient += 1
            if status == "cached_insufficient":
                n_cached += 1
            print(
                f"    {status} conf={conf}  n={summary.get('n_analogs')}  "
                f"({summary.get('error') or 'insufficient analogs/history'})",
                flush=True,
            )
        else:
            n_error += 1
            print(f"    ERROR → INSUFFICIENT  {summary.get('error')}", flush=True)

    INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")

    n_skip = n_insufficient + n_error
    print()
    print("=" * 68)
    print("STRATEGY LAB — BATCH PROFILE SUMMARY")
    print("=" * 68)
    print(f"  Setups total              : {len(setups)}")
    print(f"  Profiled successfully     : {n_ok}")
    print(f"  INSUFFICIENT / skipped    : {n_skip}  "
          f"(insufficient={n_insufficient}, errors={n_error})")
    print(f"  From cache (no rebuild)   : {n_cached}")
    print("-" * 68)
    print("  Confidence distribution")
    for label in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"):
        print(f"    {label:<14}: {conf_counts.get(label, 0)}")
    extra = sorted(k for k in conf_counts if k not in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"))
    for label in extra:
        print(f"    {label:<14}: {conf_counts[label]}")
    print("-" * 68)
    print("  Sample of successful profiles (up to 10)")
    print(
        f"  {'Ticker':<8} {'Flag':<12} {'Conf':<12} {'n':>3}  "
        f"{'Safe%':>7} {'WR%':>6} {'R:R':>5}"
    )
    shown = 0
    for key, row in index.items():
        if row.get("confidence") == "INSUFFICIENT":
            continue
        if not row.get("stats_meaningful", True):
            continue
        print(
            f"  {row.get('ticker'):<8} {str(row.get('as_of_date')):<12} "
            f"{str(row.get('confidence')):<12} {int(row.get('n_analogs') or 0):>3}  "
            f"{_fmt_pct(row.get('safe_max_stop_pct')):>7} "
            f"{_fmt_pct(row.get('win_rate')):>6} "
            f"{_fmt_rr(row.get('reward_risk')):>5}"
        )
        shown += 1
        if shown >= 10:
            break
    if shown == 0:
        print("  (none)")
    print("=" * 68)
    print(f"Wrote {INDEX_PATH.relative_to(ROOT)}")
    print(f"Profiles under {LAB_PROFILES_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
