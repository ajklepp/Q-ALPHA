#!/usr/bin/env python3
"""
PAPER-ONLY Peak Hour study — per-ticker early-trail + kill-ratchet schedules.

HARD CONSTRAINTS
----------------
Research / paper book only. Does NOT edit live trail, kill, T1, gates, or
monitors. Live files are imported read-only for Mode A baseline and optional
Mode D shadow MT3 contrast:

  - tsd_keep_profit.php_process_bar          (Mode A baseline only)
  - tsd_multi_target.advance_multi_target_leg (Mode D contrast only)
  - tsd_profiler.build_tsd_profile / load     (analogs / mae-mfe drivers)

Do not wire this module into tsd_trail_monitor or live entry.

What this estimates
-------------------
Per ticker, a paper exit schedule in % language (never R-multiples):

  early_trail_pct_off_high     e.g. 0.015–0.025  (% off run-high)
  trail_arm_mfe_pct_of_entry   e.g. 0.02 / 0.03  (% of entry before trail arms)
  kill_schedule                ratchets UP after green MFE milestones
                               kill_pct_below_entry is % of entry

If older notes say "+1R", that is +kill_pct of entry (live fallback 5%).

Modes (same entries, independent books)
--------------------------------------
  A) live php_keep_profit_v1 (T1 hard-bank +2%, 2.5% trail, kill tighten)
  B) paper trail-everything + MAE-p50 trail (no hard bank)
  C) paper per-ticker early-trail + kill schedule from estimator
  D) shadow MT3 hard banks (0.35/0.50/0.90R → 1.75/2.5/4.5% of entry) — contrast

Universe (signal-time only)
---------------------------
1. --entries JSON, or recent php_range_replay / php_day_replay results
2. Local tsd_book_state.json legs if present (not committed)
3. EXP-0021 corpus admits (symbol / signal_date / hour / close only)
4. --dry-run fixture when nothing else is available

Bars: Polygon 1H post-entry when a key or h1_bar_cache exists; else
path-fact reconstruction from analog/corpus MFE+MAE (documented degrade).

Usage
-----
  # Offline / CI (no Polygon):
  py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --dry-run

  # Laptop with .env POLYGON_API_KEY (or cached 1H / profiles):
  py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --write
  python -m tsd_scan_pipeline.php_early_trail_kill_schedule_study --write

  # From candidates/ so -m resolves:
  cd candidates && python -m tsd_scan_pipeline.php_early_trail_kill_schedule_study --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
RESULTS = PIPELINE_DIR / "results"
FIXTURE_PATH = PIPELINE_DIR / "fixtures" / "php_early_trail_kill_schedule_fixture.json"
CORPUS = ROOT / "experiments" / "EXP-0021" / "corpus_htf_universe_social.csv"
PROFILES_DIR = PIPELINE_DIR / "profiles"

for _p in (str(CANDIDATES_DIR), str(ROOT / "strategy_lab"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import asyncio

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
except Exception:
    pass


def _load_env(path: Path) -> None:
    """Load KEY=VAL into os.environ without overwriting (no python-dotenv)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(ROOT / ".env")
_load_env(CANDIDATES_DIR / ".env")

from tsd_scan_pipeline.php_early_trail_kill_paper import (  # noqa: E402
    ARM_MFE_GRID,
    COST_PER_TRADE,
    FALLBACK_KILL_PCT,
    LIVE_KILL_TIGHTEN_AFTER_T1,
    LIVE_T1_BANK_PCT,
    LIVE_TRAIL_PCT_OFF_HIGH,
    TRAIL_EVERYTHING_ARM_MFE_PCT,
    TRAIL_OFF_HIGH_GRID,
    clamp,
    measure_path_giveback,
    normalize_kill_schedule,
    path_from_facts,
    percentile,
    replay_paper_path,
    score_grid_result,
    summarize_closed_state,
)

# Recent Peak Hour names from LEARNING_AUTOPSY_20260911 (ticker hints only).
RECENT_PHP_TICKERS = (
    "ARQQ", "ATRC", "BETA", "CAI", "CBLL", "CLYM", "CNH", "FGI",
    "FWDI", "METC", "NX", "OCUL", "PURR", "QMCO", "RDW", "SLS",
)

DEFAULT_SHARES = 8  # ≥8 so Mode A gets 4 live tranches (40/30/20/10)
HOLD_SESSIONS = 5  # trading days after entry (signal-time hold window)
DEFAULT_ASOF = "2026-09-16"
MIN_ANALOGS_OK = 8
MIN_ANALOGS_HEURISTIC = 3


# ---------------------------------------------------------------------------
# Small IO helpers
# ---------------------------------------------------------------------------

def _truthy(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return str(val).strip().lower() in {"1", "true", "yes", "y"}


def _safe_float(val: Any, default: float | None = None) -> float | None:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _iso_date(val: Any) -> str:
    text = str(val or "").strip()
    if not text:
        return ""
    return text[:10]


def _try_polygon_key() -> str | None:
    """Return POLYGON_API_KEY or None — never raise (graceful degrade)."""
    key = os.environ.get("POLYGON_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        from tsd_scan_pipeline.universe_tsd import load_polygon_key

        return load_polygon_key()
    except Exception:
        return None


def _asof_stamp(asof: str) -> str:
    return _iso_date(asof).replace("-", "") or datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def _entries_from_replay_doc(doc: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
    """Normalize range/day-replay JSON trades into study entries."""
    out: list[dict[str, Any]] = []
    for t in doc.get("trades") or []:
        sym = str(t.get("symbol") or "").upper()
        px = _safe_float(t.get("entry_price") or t.get("entry") or t.get("close"))
        if not sym or px is None or px <= 0:
            continue
        out.append({
            "symbol": sym,
            "entry_price": px,
            "shares": int(t.get("shares") or DEFAULT_SHARES),
            "entry_date": _iso_date(t.get("entry_date") or t.get("date")),
            "entry_hour": int(t.get("entry_hour") or t.get("hour") or 0),
            "source": source,
        })
    return out


def _load_replay_results() -> list[dict[str, Any]]:
    """Newest php_range_replay / php_day_replay JSON trades if present."""
    if not RESULTS.is_dir():
        return []
    files = sorted(
        list(RESULTS.glob("php_range_replay_*.json"))
        + list(RESULTS.glob("php_day_replay_*.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = _entries_from_replay_doc(doc, source=path.name)
        if rows:
            return rows
    return []


def _load_book_entries() -> list[dict[str, Any]]:
    """Open + closed Peak Hour book legs (local state only; never committed)."""
    paths: list[Path] = []
    try:
        from state_paths import state_path

        paths.append(state_path("tsd_book_state.json"))
    except Exception:
        pass
    paths.extend([
        CANDIDATES_DIR / "tsd_book_state.json",
        ROOT / "uploads" / "live_tsd_book_state.json",
    ])
    book = None
    for path in paths:
        if path.is_file():
            try:
                book = json.loads(path.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    if not isinstance(book, dict):
        return []
    out: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        if not sym:
            continue
        for leg in pos.get("legs") or []:
            trail = leg.get("trail") or {}
            px = _safe_float(
                trail.get("entry_price") or leg.get("price") or leg.get("entry_price")
            )
            if px is None or px <= 0:
                continue
            opened = str(leg.get("time") or trail.get("opened_at") or pos.get("opened_at") or "")
            out.append({
                "symbol": sym,
                "entry_price": px,
                "shares": int(leg.get("shares") or DEFAULT_SHARES),
                "entry_date": _iso_date(opened),
                "entry_hour": int(leg.get("bar_hour") or 0),
                "source": "tsd_book_state",
            })
    return out


def _load_corpus_rows() -> list[dict[str, Any]]:
    """EXP-0021 corpus as dict rows (csv — no pandas required)."""
    if not CORPUS.is_file():
        return []
    with CORPUS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _corpus_admitted(row: dict[str, Any]) -> bool:
    """Signal-time admit: Peak Hour list flags only (no MFE/MAE peek)."""
    if _truthy(row.get("php_v0_admit")):
        return True
    if _truthy(row.get("all_hours_admit")) and _truthy(row.get("peak_hour")):
        return True
    return False


def _entries_from_corpus(
    rows: list[dict[str, Any]],
    *,
    asof: str,
    max_entries: int,
) -> list[dict[str, Any]]:
    """Most recent admitted Peak Hour signals on or before asof (signal-time)."""
    asof_d = _iso_date(asof)
    cands: list[dict[str, Any]] = []
    for row in rows:
        if not _corpus_admitted(row):
            continue
        d = _iso_date(row.get("signal_date"))
        if not d or (asof_d and d > asof_d):
            continue
        sym = str(row.get("symbol") or "").upper()
        px = _safe_float(row.get("close"))
        if not sym or px is None or px <= 0:
            continue
        cands.append({
            "symbol": sym,
            "entry_price": px,
            "shares": DEFAULT_SHARES,
            "entry_date": d,
            "entry_hour": int(_safe_float(row.get("hour"), 0) or 0),
            "source": "exp0021_corpus",
            # Path facts stored for degrade replay / analogs; NOT used to pick universe.
            "_mfe": _safe_float(row.get("mfe")),
            "_mae": _safe_float(row.get("mae")),
            "_killed": _truthy(row.get("killed")),
        })
    cands.sort(key=lambda r: (r["entry_date"], r["entry_hour"], r["symbol"]))
    return cands[-max_entries:] if max_entries else cands


def load_universe(
    *,
    asof: str,
    max_entries: int,
    entries_path: Path | None,
    fixture: dict[str, Any] | None,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], str]:
    """
    Resolve replay entries. Returns (entries, source_label).

    Never uses future path labels to decide membership.
    """
    if fixture is not None and (dry_run or fixture.get("entries")):
        rows = []
        for t in fixture.get("entries") or []:
            sym = str(t.get("symbol") or "").upper()
            px = _safe_float(t.get("entry_price"))
            if not sym or px is None or px <= 0:
                continue
            rec = {
                "symbol": sym,
                "entry_price": px,
                "shares": int(t.get("shares") or DEFAULT_SHARES),
                "entry_date": _iso_date(t.get("entry_date")),
                "entry_hour": int(t.get("entry_hour") or 0),
                "source": "fixture",
                "bars": list(t.get("bars") or []),
            }
            rows.append(rec)
        if rows:
            return rows[:max_entries] if max_entries else rows, "fixture"

    if entries_path and entries_path.is_file():
        doc = json.loads(entries_path.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            doc = {"trades": doc}
        rows = _entries_from_replay_doc(doc, source=str(entries_path.name))
        if rows:
            return rows[:max_entries] if max_entries else rows, f"entries:{entries_path.name}"

    replay = _load_replay_results()
    if replay:
        return replay[:max_entries] if max_entries else replay, replay[0]["source"]

    book = _load_book_entries()
    if book:
        return book[:max_entries] if max_entries else book, "tsd_book_state"

    corpus = _entries_from_corpus(_load_corpus_rows(), asof=asof, max_entries=max_entries)
    if corpus:
        return corpus, "exp0021_corpus"

    return [], "empty"


# ---------------------------------------------------------------------------
# Bars + analogs
# ---------------------------------------------------------------------------

def _post_entry_bars_from_frame(
    bars: Any,
    *,
    entry_date: str,
    entry_hour: int,
    hold_sessions: int = HOLD_SESSIONS,
) -> list[dict[str, Any]]:
    """Slice a 1H DataFrame (or list) to post-entry bars within hold_sessions."""
    if bars is None:
        return []
    # List-of-dict path (fixture / already sliced).
    if isinstance(bars, list):
        return [b for b in bars if isinstance(b, dict)]

    out: list[dict[str, Any]] = []
    try:
        sessions: set[str] = set()
        for ts, row in bars.iterrows():
            t = ts
            try:
                import pandas as pd
                import pytz

                et = pytz.timezone("America/New_York")
                t = pd.Timestamp(ts)
                t = t.tz_localize(et) if t.tzinfo is None else t.tz_convert(et)
            except Exception:
                pass
            d = t.date().isoformat() if hasattr(t, "date") else _iso_date(t)
            hour = int(getattr(t, "hour", 0))
            close_hour = (hour + 1) % 24
            if entry_date and d < entry_date:
                continue
            if entry_date and d == entry_date and close_hour <= int(entry_hour or 0):
                continue
            if entry_date and d > entry_date:
                sessions.add(d)
                if len(sessions) > hold_sessions:
                    break
            high = float(row.get("high") or 0)
            low = float(row.get("low") or 0)
            close = float(row.get("close") or 0)
            if high <= 0 or low <= 0:
                continue
            out.append({
                "date": d,
                "hour": close_hour,
                "high": high,
                "low": low,
                "close": close,
                "when": f"{d}T{close_hour:02d}:00:00",
            })
    except Exception:
        return out
    return out


def _load_cached_1h(symbol: str) -> Any | None:
    """Disk 1H cache used by tsd_1h_signal (pickle)."""
    try:
        from tsd_scan_pipeline.tsd_1h_signal import _disk_get

        return _disk_get(symbol)
    except Exception:
        return None


def _fetch_1h(symbol: str, api_key: str) -> Any | None:
    """Polygon 1H via existing helper; rate-limit 0.12s after the call."""
    try:
        from tsd_scan_pipeline.tsd_1h_signal import load_1h_bars

        df = load_1h_bars(symbol, api_key=api_key)
        time.sleep(0.12)
        return df
    except Exception:
        try:
            from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon

            df = _bars_1h_polygon(symbol, api_key=api_key)
            time.sleep(0.12)
            return df
        except Exception:
            return None


def load_profile(symbol: str) -> dict[str, Any] | None:
    """Load profiles/{SYM}_tsd_profile.json if present (read-only)."""
    path = PROFILES_DIR / f"{symbol.upper()}_tsd_profile.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def analogs_from_profile_and_corpus(
    symbol: str,
    *,
    before_date: str,
    fixture: dict[str, Any] | None,
    corpus_rows: list[dict[str, Any]],
    profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Prior-only analogs for `symbol` (entry_date < before_date).

    Prefer fixture analog paths, then corpus path facts, then profile percentiles
    (no individual paths — estimator falls back to heuristic).
    """
    sym = symbol.upper()
    out: list[dict[str, Any]] = []

    if fixture:
        for a in fixture.get("analogs", {}).get(sym, fixture.get("analogs", {}).get(symbol, [])):
            d = _iso_date(a.get("entry_date"))
            if before_date and d and d >= before_date:
                continue
            px = _safe_float(a.get("entry_price") or a.get("close"))
            if px is None or px <= 0:
                continue
            bars = list(a.get("bars") or [])
            if not bars:
                mfe = _safe_float(a.get("mfe_pct") or a.get("mfe"), 0.0) or 0.0
                mae = _safe_float(a.get("mae_pct") or a.get("mae"), 0.0) or 0.0
                bars = path_from_facts(px, mfe_pct=mfe, mae_pct=mae, killed=_truthy(a.get("killed")))
            out.append({
                "symbol": sym,
                "entry_price": px,
                "entry_date": d,
                "bars": bars,
                "source": "fixture",
            })

    for row in corpus_rows:
        if str(row.get("symbol") or "").upper() != sym:
            continue
        if not _corpus_admitted(row):
            continue
        d = _iso_date(row.get("signal_date"))
        if not d or (before_date and d >= before_date):
            continue
        px = _safe_float(row.get("close"))
        if px is None or px <= 0:
            continue
        mfe = _safe_float(row.get("mfe"), 0.0) or 0.0
        mae = _safe_float(row.get("mae"), 0.0) or 0.0
        out.append({
            "symbol": sym,
            "entry_price": px,
            "entry_date": d,
            "bars": path_from_facts(
                px, mfe_pct=mfe, mae_pct=mae, killed=_truthy(row.get("killed")),
            ),
            "mfe_pct": mfe,
            "mae_pct": mae,
            "source": "exp0021_corpus",
        })

    if not out and profile:
        # Percentile-only analog: one synthetic path at p50 so heuristic has drivers.
        mae = float((profile.get("mae") or {}).get("p50") or 0.0)
        mfe = float((profile.get("mfe") or {}).get("p50") or 0.0)
        if mae > 0 or mfe > 0:
            out.append({
                "symbol": sym,
                "entry_price": 10.0,
                "entry_date": "",
                "bars": path_from_facts(10.0, mfe_pct=mfe, mae_pct=mae, killed=False),
                "mfe_pct": mfe,
                "mae_pct": mae,
                "source": "profile_p50",
            })
    return out


def kill_schedule_from_drivers(
    *,
    mae_p50: float | None,
    mae_p75: float | None,
    fade_p75_off_high_after_3pct: float | None,
    template: str = "mae_driven",
) -> list[dict[str, float]]:
    """
    Build a 0/2/3/4% MFE kill ratchet from analog MAE / fade drivers.

    kill_pct_below_entry is % of entry. Schedule only tightens after green.
    """
    initial = clamp(float(mae_p75 or FALLBACK_KILL_PCT), 0.02, 0.06)
    p50 = clamp(float(mae_p50 or 0.03), 0.015, initial)
    fade = fade_p75_off_high_after_3pct

    if template == "live_like":
        return normalize_kill_schedule([
            {"after_mfe_pct": 0.00, "kill_pct_below_entry": FALLBACK_KILL_PCT},
            {"after_mfe_pct": 0.02, "kill_pct_below_entry": LIVE_KILL_TIGHTEN_AFTER_T1},
            {"after_mfe_pct": 0.03, "kill_pct_below_entry": LIVE_KILL_TIGHTEN_AFTER_T1},
            {"after_mfe_pct": 0.04, "kill_pct_below_entry": 0.015},
        ])
    if template == "tight":
        return normalize_kill_schedule([
            {"after_mfe_pct": 0.00, "kill_pct_below_entry": FALLBACK_KILL_PCT},
            {"after_mfe_pct": 0.02, "kill_pct_below_entry": 0.025},
            {"after_mfe_pct": 0.03, "kill_pct_below_entry": 0.015},
            {"after_mfe_pct": 0.04, "kill_pct_below_entry": 0.010},
        ])

    after2 = min(initial, max(0.025, p50))
    after3 = min(after2, 0.025)
    after4 = min(after3, 0.015)
    if fade is not None and fade < 0.02:
        after3 = min(after3, 0.020)
        after4 = min(after4, 0.012)
    return normalize_kill_schedule([
        {"after_mfe_pct": 0.00, "kill_pct_below_entry": initial},
        {"after_mfe_pct": 0.02, "kill_pct_below_entry": after2},
        {"after_mfe_pct": 0.03, "kill_pct_below_entry": after3},
        {"after_mfe_pct": 0.04, "kill_pct_below_entry": after4},
    ])


def heuristic_schedule(drivers: dict[str, Any], *, n_analogs: int) -> dict[str, Any]:
    """Schedule from percentiles only (no path grid). Status HEURISTIC / INSUFFICIENT."""
    mae_p50 = drivers.get("mae_p50")
    mae_p75 = drivers.get("mae_p75")
    mfe_p50 = drivers.get("mfe_p50")
    fade = drivers.get("fade_p75_off_high_after_3pct")
    trail = clamp(float(fade if fade is not None else LIVE_TRAIL_PCT_OFF_HIGH), 0.015, 0.025)
    arm = 0.02 if (mfe_p50 is not None and float(mfe_p50) >= 0.03) else 0.015
    status = "HEURISTIC" if n_analogs >= MIN_ANALOGS_HEURISTIC else "INSUFFICIENT"
    return {
        "early_trail_pct_off_high": round(trail, 6),
        "trail_arm_mfe_pct_of_entry": round(arm, 6),
        "kill_schedule": kill_schedule_from_drivers(
            mae_p50=mae_p50, mae_p75=mae_p75, fade_p75_off_high_after_3pct=fade,
        ),
        "drivers": drivers,
        "n_analogs": n_analogs,
        "status": status,
        "grid_winner": None,
        "note": "percentile heuristic — no path grid (need analog bars or path facts)",
    }


def estimate_ticker_exit_schedule(
    symbol: str,
    analogs: list[dict[str, Any]],
    bars: Any = None,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Estimate paper early-trail % off high + kill ratchet from prior analogs.

    `bars` is optional symbol-level 1H history used only when an analog has
    entry_date but no pre-sliced path. Never uses the replay entry's own path.
    """
    sym = str(symbol).upper()
    paths: list[dict[str, Any]] = []
    for a in analogs:
        px = _safe_float(a.get("entry_price") or a.get("close"))
        if px is None or px <= 0:
            continue
        path = list(a.get("bars") or [])
        if not path and bars is not None and a.get("entry_date"):
            path = _post_entry_bars_from_frame(
                bars,
                entry_date=_iso_date(a.get("entry_date")),
                entry_hour=int(a.get("entry_hour") or 0),
            )
        if not path:
            mfe = _safe_float(a.get("mfe_pct") or a.get("mfe"))
            mae = _safe_float(a.get("mae_pct") or a.get("mae"))
            if mfe is None and mae is None:
                continue
            path = path_from_facts(
                px,
                mfe_pct=mfe or 0.0,
                mae_pct=mae or 0.0,
                killed=_truthy(a.get("killed")),
            )
        gb = measure_path_giveback(px, path)
        paths.append({"entry_price": px, "bars": path, **gb, "shares": int(a.get("shares") or DEFAULT_SHARES)})

    mae_vals = [p["mae_peak_pct"] for p in paths if p.get("mae_peak_pct") is not None]
    mfe_vals = [p["mfe_peak_pct"] for p in paths if p.get("mfe_peak_pct") is not None]
    fade_vals = [
        p["giveback_after_3pct_off_high"]
        for p in paths
        if p.get("touched_plus_3pct")
    ]
    # Mix in saved profiler percentiles when present (prior analogs, not live trail).
    if profile:
        mae = profile.get("mae") or {}
        mfe = profile.get("mfe") or {}
        if mae.get("p50") is not None:
            mae_vals.append(float(mae["p50"]))
        if mae.get("p75") is not None:
            mae_vals.append(float(mae["p75"]))
        if mfe.get("p50") is not None:
            mfe_vals.append(float(mfe["p50"]))

    drivers = {
        "mae_p50": round(percentile(mae_vals, 50) or 0.0, 6) if mae_vals else None,
        "mae_p75": round(percentile(mae_vals, 75) or 0.0, 6) if mae_vals else None,
        "mfe_p50": round(percentile(mfe_vals, 50) or 0.0, 6) if mfe_vals else None,
        "fade_p75_off_high_after_3pct": (
            round(percentile(fade_vals, 75) or 0.0, 6) if fade_vals else None
        ),
        "n_touched_plus_3pct": len(fade_vals),
        "n_faded_after_3pct": sum(1 for p in paths if p.get("faded_after_3pct")),
        "n_faded_after_4pct": sum(1 for p in paths if p.get("faded_after_4pct")),
    }
    n = len(paths)
    if n < MIN_ANALOGS_HEURISTIC:
        out = heuristic_schedule(drivers, n_analogs=n)
        out["symbol"] = sym
        return out

    templates = ("mae_driven", "live_like", "tight")
    best: dict[str, Any] | None = None
    grid_rows: list[dict[str, Any]] = []
    for trail in TRAIL_OFF_HIGH_GRID:
        for arm in ARM_MFE_GRID:
            for tmpl in templates:
                sched = kill_schedule_from_drivers(
                    mae_p50=drivers["mae_p50"],
                    mae_p75=drivers["mae_p75"],
                    fade_p75_off_high_after_3pct=drivers["fade_p75_off_high_after_3pct"],
                    template=tmpl,
                )
                replays = [
                    replay_paper_path(
                        p["entry_price"],
                        p["bars"],
                        shares=int(p.get("shares") or DEFAULT_SHARES),
                        trail_pct_off_high=trail,
                        trail_arm_mfe_pct=arm,
                        kill_schedule=sched,
                        mode="C_grid",
                    )
                    for p in paths
                ]
                scored = score_grid_result(replays)
                cand = {
                    "early_trail_pct_off_high": trail,
                    "trail_arm_mfe_pct_of_entry": arm,
                    "kill_template": tmpl,
                    **scored,
                }
                grid_rows.append(cand)
                if scored["score"] is None:
                    continue
                if best is None or (scored["score"], scored["mean_capture_frac_of_mfe"] or 0) > (
                    best["score"], best.get("mean_capture_frac_of_mfe") or 0
                ):
                    best = {**cand, "kill_schedule": sched}

    if best is None:
        out = heuristic_schedule(drivers, n_analogs=n)
        out["symbol"] = sym
        return out

    return {
        "symbol": sym,
        "early_trail_pct_off_high": best["early_trail_pct_off_high"],
        "trail_arm_mfe_pct_of_entry": best["trail_arm_mfe_pct_of_entry"],
        "kill_schedule": best["kill_schedule"],
        "drivers": drivers,
        "n_analogs": n,
        "status": "OK" if n >= MIN_ANALOGS_OK else "HEURISTIC",
        "grid_winner": {
            "early_trail_pct_off_high": best["early_trail_pct_off_high"],
            "trail_arm_mfe_pct_of_entry": best["trail_arm_mfe_pct_of_entry"],
            "kill_template": best["kill_template"],
            "score": best["score"],
            "mean_pnl_pct_of_entry": best["mean_pnl_pct_of_entry"],
            "mean_capture_frac_of_mfe": best["mean_capture_frac_of_mfe"],
            "green_then_lost_rate": best["green_then_lost_rate"],
            "n": best["n"],
        },
        "grid_n_combos": len(grid_rows),
        "note": (
            "grid = trail%off-high × arm MFE%of-entry × kill template; "
            "scored on prior analogs only"
        ),
    }


# ---------------------------------------------------------------------------
# Mode A / D wrappers (live modules imported read-only)
# ---------------------------------------------------------------------------

def _replay_mode_a(entry: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Baseline: live php_keep_profit_v1 process_bar (read-only import)."""
    from tsd_scan_pipeline.tsd_keep_profit import (
        init_php_trail_state,
        php_process_bar,
    )

    entry_px = float(entry["entry_price"])
    shares = int(entry.get("shares") or DEFAULT_SHARES)
    trail = init_php_trail_state(
        entry_px, shares, kill_pct=FALLBACK_KILL_PCT,
    )
    realized = 0.0
    exits: list[dict[str, Any]] = []
    peak = entry_px
    mae_peak = 0.0
    last_close = entry_px
    last_when = "hold_end"
    for i, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar.get("close") or (high + low) / 2.0)
        when = str(bar.get("when") or f"bar{i}")
        peak = max(peak, high)
        mae_peak = max(mae_peak, (entry_px - low) / entry_px if entry_px else 0.0)
        last_close = close
        last_when = when
        trail, new_ex = php_process_bar(
            trail,
            high=high, low=low, close=close, when=when,
            be_lock_after_t1=False,
            kill_tighten_after_t1=LIVE_KILL_TIGHTEN_AFTER_T1,
        )
        for e in new_ex:
            realized += (float(e["exit_price"]) - entry_px) * int(e["shares"])
            exits.append(e)
        rem = sum(
            int(t["shares"]) for t in (trail.get("tranches") or []) if not t.get("closed")
        )
        if rem <= 0:
            break
    rem = sum(int(t["shares"]) for t in (trail.get("tranches") or []) if not t.get("closed"))
    if rem > 0:
        realized += (last_close - entry_px) * rem
        exits.append({
            "tranche_id": "REMAINING",
            "shares": rem,
            "exit_price": last_close,
            "reason": "hold_end",
            "when": last_when,
        })
    state = {
        "mode": "A",
        "realized_gross": realized,
        "exits": exits,
        "peak_high": peak,
        "mfe_peak_pct": max(0.0, (peak - entry_px) / entry_px) if entry_px else 0.0,
        "mae_peak_pct": mae_peak,
        "kill_pct": trail.get("kill_pct"),
        "trail_armed": True,
    }
    return summarize_closed_state(state, entry_price=entry_px, shares=shares)


def _replay_mode_d(entry: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Optional contrast: shadow MT3 hard banks (read-only multi-target)."""
    from tsd_scan_pipeline.tsd_multi_target import (
        LADDER_MT3,
        advance_multi_target_leg,
        mark_flat_leg,
        open_multi_target_leg,
    )

    entry_px = float(entry["entry_price"])
    shares = int(entry.get("shares") or DEFAULT_SHARES)
    leg = open_multi_target_leg(
        symbol=str(entry["symbol"]),
        entry_price=entry_px,
        shares=shares,
        ladder=LADDER_MT3,
    )
    peak = entry_px
    mae_peak = 0.0
    last_close = entry_px
    last_when = "hold_end"
    for i, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar.get("close") or (high + low) / 2.0)
        when = str(bar.get("when") or f"bar{i}")
        peak = max(peak, high)
        mae_peak = max(mae_peak, (entry_px - low) / entry_px if entry_px else 0.0)
        last_close = close
        last_when = when
        leg, _, flat = advance_multi_target_leg(leg, high=high, low=low, when=when)
        if flat:
            break
    if str(leg.get("status") or "").upper() != "CLOSED":
        leg = mark_flat_leg(leg, mark=last_close, when=last_when, reason="hold_end")
    state = {
        "mode": "D",
        "realized_gross": float(leg.get("realized_gross") or 0.0),
        "exits": list(leg.get("exits") or []),
        "peak_high": peak,
        "mfe_peak_pct": max(0.0, (peak - entry_px) / entry_px) if entry_px else 0.0,
        "mae_peak_pct": mae_peak,
        "kill_pct": leg.get("kill_pct"),
        "trail_armed": False,
    }
    return summarize_closed_state(state, entry_price=entry_px, shares=shares)


def _replay_mode_b(
    entry: dict[str, Any],
    bars: list[dict[str, Any]],
    *,
    mae_p50: float | None,
) -> dict[str, Any]:
    """Paper trail-everything: MAE-p50 off high, no hard bank, no kill ratchet."""
    trail = clamp(float(mae_p50 or LIVE_TRAIL_PCT_OFF_HIGH), 0.010, 0.050)
    return replay_paper_path(
        float(entry["entry_price"]),
        bars,
        shares=int(entry.get("shares") or DEFAULT_SHARES),
        trail_pct_off_high=trail,
        trail_arm_mfe_pct=TRAIL_EVERYTHING_ARM_MFE_PCT,
        kill_schedule=[{"after_mfe_pct": 0.0, "kill_pct_below_entry": FALLBACK_KILL_PCT}],
        mode="B",
    )


def _replay_mode_c(
    entry: dict[str, Any],
    bars: list[dict[str, Any]],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    """Paper per-ticker early-trail + kill schedule."""
    return replay_paper_path(
        float(entry["entry_price"]),
        bars,
        shares=int(entry.get("shares") or DEFAULT_SHARES),
        trail_pct_off_high=float(schedule.get("early_trail_pct_off_high") or LIVE_TRAIL_PCT_OFF_HIGH),
        trail_arm_mfe_pct=float(schedule.get("trail_arm_mfe_pct_of_entry") or LIVE_T1_BANK_PCT),
        kill_schedule=schedule.get("kill_schedule"),
        mode="C",
    )


def replay_paper(
    entries: list[dict[str, Any]],
    schedule_mode: str,
    *,
    schedules: dict[str, dict[str, Any]] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Replay one schedule mode across entries. Read-only vs live keep-profit.

    Each entry must already have `bars` (1H or path-fact). Returns book PnL
    and giveback stats in % of entry / % off high.
    """
    mode = str(schedule_mode).upper().strip()
    schedules = schedules or {}
    profiles = profiles or {}
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for ent in entries:
        sym = str(ent["symbol"]).upper()
        bars = list(ent.get("bars") or [])
        if not bars:
            rows.append({
                "symbol": sym,
                "entry_date": ent.get("entry_date"),
                "status": "no_bars",
                "pnl_pct_of_entry": 0.0,
                "capture_frac_of_mfe": 0.0,
                "green_then_lost": False,
            })
            continue
        gb = measure_path_giveback(float(ent["entry_price"]), bars)
        if mode == "A":
            sim = _replay_mode_a(ent, bars)
        elif mode == "B":
            mae_p50 = None
            if schedules.get(sym, {}).get("drivers", {}).get("mae_p50") is not None:
                mae_p50 = float(schedules[sym]["drivers"]["mae_p50"])
            elif (profiles.get(sym) or {}).get("mae", {}).get("p50") is not None:
                mae_p50 = float(profiles[sym]["mae"]["p50"])
            sim = _replay_mode_b(ent, bars, mae_p50=mae_p50)
        elif mode == "C":
            sim = _replay_mode_c(ent, bars, schedules.get(sym) or heuristic_schedule({}, n_analogs=0))
        elif mode == "D":
            sim = _replay_mode_d(ent, bars)
        else:
            raise ValueError(f"unknown schedule_mode {schedule_mode!r}")
        rows.append({
            "symbol": sym,
            "entry_date": ent.get("entry_date"),
            "entry_hour": ent.get("entry_hour"),
            "source": ent.get("source"),
            "path": gb,
            **sim,
        })

    scored = score_grid_result([
        r for r in rows if r.get("status") != "no_bars"
    ])
    book_pnl = round(sum(float(r.get("pnl") or 0.0) for r in rows), 4)
    return {
        "mode": mode,
        "label": {
            "A": "live php_keep_profit_v1 (T1 bank +2% of entry)",
            "B": "paper trail-everything + MAE-p50 trail (no hard bank)",
            "C": "paper per-ticker early-trail + kill schedule",
            "D": "shadow MT3 hard banks (1.75/2.5/4.5% of entry; +1R=+5%)",
        }.get(mode, mode),
        "book_pnl": book_pnl,
        "book_pnl_pct_of_entry_mean": scored.get("mean_pnl_pct_of_entry"),
        "capture_frac_of_mfe_mean": scored.get("mean_capture_frac_of_mfe"),
        "green_then_lost_rate": scored.get("green_then_lost_rate"),
        "score": scored.get("score"),
        "n": scored.get("n"),
        "n_no_bars": sum(1 for r in rows if r.get("status") == "no_bars"),
        "runtime_s": round(time.time() - t0, 3),
        "trades": rows,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _pct(x: Any) -> str:
    if x is None:
        return "n/a"
    return f"{float(x) * 100:.2f}%"


def write_markdown(doc: dict[str, Any], path: Path) -> None:
    """Short ops-facing summary next to the JSON dump."""
    modes = doc.get("modes") or {}
    lines = [
        f"# Peak Hour early-trail / kill-schedule study ({doc.get('asof')})",
        "",
        "PAPER-ONLY. Zero edits to live `tsd_keep_profit` / `tsd_trail` / "
        "`tsd_trail_monitor` / `tsd_kill` / entry gates.",
        "",
        f"- Universe: **{doc.get('universe_source')}** · n={doc.get('n_entries')} "
        f"entries / {doc.get('n_tickers')} tickers",
        f"- Bar source: **{doc.get('bar_source')}**",
        f"- Runtime: **{doc.get('runtime_s')}s**",
        f"- Language: % of entry and % off high. "
        f"“+1R” in older notes = +{ _pct(FALLBACK_KILL_PCT) } of entry "
        f"(live fallback kill).",
        "",
        "## Mode book (same entries)",
        "",
        "| Mode | Book $ | Mean % of entry | Capture of MFE | Green-then-lost | Score |",
        "|------|-------:|----------------:|---------------:|----------------:|------:|",
    ]
    for key in ("A", "B", "C", "D"):
        m = modes.get(key)
        if not m:
            continue
        lines.append(
            f"| {key} {m.get('label', '')} | {m.get('book_pnl'):+.2f} | "
            f"{_pct(m.get('book_pnl_pct_of_entry_mean'))} | "
            f"{_pct(m.get('capture_frac_of_mfe_mean'))} | "
            f"{_pct(m.get('green_then_lost_rate'))} | "
            f"{m.get('score')} |"
        )
    lines += [
        "",
        "## Per-ticker proposed schedule (Mode C)",
        "",
        "| Ticker | Status | n analogs | Trail % off high | Arm MFE % of entry | Kill 0/2/3/4 |",
        "|--------|--------|----------:|-----------------:|-------------------:|--------------|",
    ]
    for sched in doc.get("schedules") or []:
        ks = sched.get("kill_schedule") or []
        kill_txt = "/".join(
            f"{float(s['kill_pct_below_entry']) * 100:.1f}%" for s in ks
        )
        lines.append(
            f"| {sched.get('symbol')} | {sched.get('status')} | "
            f"{sched.get('n_analogs')} | "
            f"{_pct(sched.get('early_trail_pct_off_high'))} | "
            f"{_pct(sched.get('trail_arm_mfe_pct_of_entry'))} | {kill_txt} |"
        )
    lines += [
        "",
        "## How to re-run (laptop)",
        "",
        "```text",
        "py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --write",
        "py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --dry-run",
        "cd candidates && python -m tsd_scan_pipeline.php_early_trail_kill_schedule_study --dry-run",
        "```",
        "",
        "Needs `POLYGON_API_KEY` in `.env` for fresh 1H bars. Without a key the",
        "script uses fixture / cached `results/h1_bar_cache` / `profiles/*_tsd_profile.json`",
        "/ EXP-0021 path facts (MFE+MAE reconstruction).",
        "",
        f"Degrade notes: {doc.get('degrade_notes') or 'none'}",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_study(args: argparse.Namespace) -> dict[str, Any]:
    """Full study: universe → schedules → A/B/C/(D) replay → artifacts."""
    t0 = time.time()
    asof = args.asof
    fixture: dict[str, Any] | None = None
    fixture_path = Path(args.fixture) if args.fixture else FIXTURE_PATH
    if args.dry_run or args.fixture:
        if fixture_path.is_file():
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        elif args.dry_run:
            raise SystemExit(f"dry-run fixture missing: {fixture_path}")

    entries, uni_src = load_universe(
        asof=asof,
        max_entries=int(args.max_entries),
        entries_path=Path(args.entries) if args.entries else None,
        fixture=fixture,
        dry_run=bool(args.dry_run),
    )
    if not entries:
        raise SystemExit(
            "No Peak Hour entries found. Pass --dry-run, --entries JSON, "
            "or drop a php_range_replay_*.json under results/."
        )

    modes_wanted = [m.strip().upper() for m in str(args.modes).split(",") if m.strip()]
    for m in modes_wanted:
        if m not in {"A", "B", "C", "D"}:
            raise SystemExit(f"unknown mode {m!r} (use A,B,C,D)")

    key = None if args.dry_run else _try_polygon_key()
    corpus_rows = [] if args.dry_run else _load_corpus_rows()
    degrade: list[str] = []
    if args.dry_run:
        degrade.append("dry-run fixture (no Polygon)")
    elif not key:
        degrade.append("POLYGON_API_KEY missing — cached bars / path facts only")

    bar_source = "fixture" if (fixture and args.dry_run) else "unknown"
    bar_cache: dict[str, Any] = {}
    profiles: dict[str, dict[str, Any]] = {}
    symbols = sorted({str(e["symbol"]).upper() for e in entries})

    for i, sym in enumerate(symbols, 1):
        prof = None
        if fixture and (fixture.get("profiles") or {}).get(sym):
            prof = fixture["profiles"][sym]
        else:
            prof = load_profile(sym)
        if prof:
            profiles[sym] = prof
        if fixture and args.dry_run:
            continue
        cached = _load_cached_1h(sym)
        if cached is not None:
            bar_cache[sym] = cached
            bar_source = "h1_bar_cache"
        elif key:
            fetched = _fetch_1h(sym, key)
            if fetched is not None:
                bar_cache[sym] = fetched
                bar_source = "polygon_1h"
        if i % 25 == 0 or i == len(symbols):
            print(f"  bars/profiles {i}/{len(symbols)}")

    if bar_source == "unknown":
        bar_source = "path_facts_mfe_mae"
        degrade.append("no 1H bars — reconstructed 2-bar MFE-then-MAE paths")

    # Attach bars to each entry (fixture bars win).
    for ent in entries:
        if ent.get("bars"):
            continue
        if fixture:
            # Named fixture paths keyed by symbol+date.
            for fe in fixture.get("entries") or []:
                if str(fe.get("symbol") or "").upper() == ent["symbol"] and (
                    not ent.get("entry_date")
                    or _iso_date(fe.get("entry_date")) == ent["entry_date"]
                ):
                    if fe.get("bars"):
                        ent["bars"] = list(fe["bars"])
                        break
        if ent.get("bars"):
            continue
        frame = bar_cache.get(ent["symbol"])
        sliced = _post_entry_bars_from_frame(
            frame,
            entry_date=str(ent.get("entry_date") or ""),
            entry_hour=int(ent.get("entry_hour") or 0),
        )
        if sliced:
            ent["bars"] = sliced
            continue
        mfe = _safe_float(ent.get("_mfe"))
        mae = _safe_float(ent.get("_mae"))
        if mfe is not None or mae is not None:
            ent["bars"] = path_from_facts(
                float(ent["entry_price"]),
                mfe_pct=mfe or 0.0,
                mae_pct=mae or 0.0,
                killed=_truthy(ent.get("_killed")),
            )

    schedules: dict[str, dict[str, Any]] = {}
    for i, sym in enumerate(symbols, 1):
        # Strictly prior analogs vs the earliest replay entry for this ticker.
        before = min(
            (_iso_date(e.get("entry_date")) or "9999-99-99")
            for e in entries
            if e["symbol"] == sym
        )
        analogs = analogs_from_profile_and_corpus(
            sym,
            before_date=before,
            fixture=fixture,
            corpus_rows=corpus_rows,
            profile=profiles.get(sym),
        )
        schedules[sym] = estimate_ticker_exit_schedule(
            sym,
            analogs,
            bar_cache.get(sym),
            profile=profiles.get(sym),
        )
        if i % 25 == 0 or i == len(symbols):
            print(f"  schedules {i}/{len(symbols)}  last={sym} {schedules[sym]['status']}")

    mode_docs: dict[str, Any] = {}
    for mode in modes_wanted:
        print(f"Replaying mode {mode}...")
        mode_docs[mode] = replay_paper(
            entries, mode, schedules=schedules, profiles=profiles,
        )
        m = mode_docs[mode]
        print(
            f"  {mode}  book=${m['book_pnl']:+.2f}  "
            f"mean%={_pct(m['book_pnl_pct_of_entry_mean'])}  "
            f"capture={_pct(m['capture_frac_of_mfe_mean'])}  "
            f"GTL={_pct(m['green_then_lost_rate'])}"
        )

    runtime = round(time.time() - t0, 3)
    doc = {
        "paper_only": True,
        "asof": asof,
        "universe_source": uni_src,
        "bar_source": bar_source,
        "n_entries": len(entries),
        "n_tickers": len(symbols),
        "symbols": symbols,
        "recent_php_ticker_hints": list(RECENT_PHP_TICKERS),
        "modes_run": modes_wanted,
        "cost_per_trade": COST_PER_TRADE,
        "language": {
            "units": "pct_of_entry_and_pct_off_high",
            "plus_1r_means": (
                f"+kill_pct of entry (fallback {FALLBACK_KILL_PCT:.2%} of entry)"
            ),
            "live_t1_bank_pct_of_entry": LIVE_T1_BANK_PCT,
            "live_trail_pct_off_high": LIVE_TRAIL_PCT_OFF_HIGH,
        },
        "grid": {
            "trail_off_high": list(TRAIL_OFF_HIGH_GRID),
            "arm_mfe_pct_of_entry": list(ARM_MFE_GRID),
            "kill_milestones_mfe_pct_of_entry": [0.00, 0.02, 0.03, 0.04],
            "score": "mean_pnl_pct_of_entry - 0.01 * green_then_lost_rate",
        },
        "schedules": [schedules[s] for s in symbols],
        "modes": mode_docs,
        "degrade_notes": "; ".join(degrade) if degrade else "none",
        "runtime_s": runtime,
        "live_files_edited": [],
        "note": (
            "PAPER-ONLY. Mode A calls live php_process_bar read-only. "
            "Modes B/C use php_early_trail_kill_paper.paper_process_bar. "
            "Mode D calls tsd_multi_target read-only."
        ),
    }
    return doc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PAPER-ONLY Peak Hour early-trail + kill-schedule study",
    )
    p.add_argument("--asof", default=DEFAULT_ASOF, help="YYYY-MM-DD as-of (signal-time cut)")
    p.add_argument("--dry-run", action="store_true", help="Use bundled fixture; no Polygon")
    p.add_argument("--fixture", default="", help="Optional fixture JSON path")
    p.add_argument("--entries", default="", help="Optional entries / replay JSON")
    p.add_argument("--modes", default="A,B,C,D", help="Comma list of A,B,C,D")
    p.add_argument("--max-entries", type=int, default=24, help="Cap replay entries")
    p.add_argument("--write", action="store_true", default=True, help="Write results JSON+MD")
    p.add_argument("--no-write", action="store_true", help="Skip results artifacts")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_write:
        args.write = False
    print("=" * 72)
    print("PHP EARLY-TRAIL / KILL-SCHEDULE STUDY  (PAPER-ONLY)")
    print(f"asof={args.asof}  dry_run={bool(args.dry_run)}  modes={args.modes}")
    print("=" * 72)
    doc = run_study(args)
    stamp = _asof_stamp(doc["asof"])
    if args.dry_run:
        stamp = f"{stamp}_dryrun"
    json_path = RESULTS / f"php_early_trail_kill_schedule_{stamp}.json"
    md_path = RESULTS / f"php_early_trail_kill_schedule_{stamp}.md"
    if args.write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        write_markdown(doc, md_path)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
    print(f"Runtime {doc['runtime_s']}s  universe={doc['universe_source']}  bars={doc['bar_source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
