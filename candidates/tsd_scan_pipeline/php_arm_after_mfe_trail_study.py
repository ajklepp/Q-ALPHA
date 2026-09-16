#!/usr/bin/env python3
"""
PAPER-ONLY Peak Hour study — arm-after-MFE trail vs live keep-profit.

HARD CONSTRAINTS
----------------
Research / paper book only. Does NOT edit live trail, kill, T1, gates, or
monitors. Live files are imported read-only for Mode A baseline:

  - tsd_keep_profit.php_process_bar   (Mode A only)
  - tsd_profiler / profiles / book    (MAE–MFE widths, universe)

Do not wire this module into tsd_trail_monitor or live entry.

Why this exists
---------------
PR #16 (`php_early_trail_kill_schedule_study.py`) Mode B trailed MAE-p50
*from entry* and lost on rippers (path MFE≥4%): live-style T1 bank held
the ride; from-entry trail got shaken out. Strategy Finder: do **not**
prefer that B over A for live.

This study tests the next design: trail **arms only after MFE** (~+3% / +4%
of entry). Until then, only the emergency kill floor applies. After arm:
trail under high by ticker MAE-p50 (or a tighter lock width). Kill ratchets
UP with the trail — never a hard take-profit / bank ladder.

Modes (same entries, independent books)
--------------------------------------
  A              live php_keep_profit_v1 (T1 hard-bank +2% of entry)
  B_arm3         arm at +3% of entry, MAE-p50 width, kill follows trail
  B_arm4         arm at +4% of entry, MAE-p50 width, kill follows trail
  B_arm3_lock    arm +3%, tighter lock width (70% of MAE-p50)
  B_arm4_lock    arm +4%, tighter lock width
  C              prior PR #16 Mode B: trail-from-entry MAE-p50 (arm=0)

Language: % of entry and % off high. Never R-multiples.
"+1R" in older notes = +5% of entry (live fallback kill).

Universe (signal-time only)
---------------------------
1. --entries JSON, or recent php_range_replay / php_day_replay results
2. Local tsd_book_state.json legs if present (not committed)
3. EXP-0021 corpus admits (symbol / signal_date / hour / close only)
4. --dry-run fixture when nothing else is available

Bars: Polygon 1H / h1_bar_cache when present; else fixture paths.
2-bar MFE+MAE reconstructions are labeled degrade and do **not** drive
the live-preference recommendation (they cannot show early-chop shakeouts).

Usage
-----
  py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --dry-run
  py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --write
  cd candidates && python -m tsd_scan_pipeline.php_arm_after_mfe_trail_study --dry-run
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
FIXTURE_PATH = PIPELINE_DIR / "fixtures" / "php_arm_after_mfe_trail_fixture.json"
CORPUS = ROOT / "experiments" / "EXP-0021" / "corpus_htf_universe_social.csv"
PROFILES_DIRS = (
    PIPELINE_DIR / "profiles",
    CANDIDATES_DIR / "profiles",
    ROOT / "profiles",
)

for _p in (str(CANDIDATES_DIR), str(ROOT / "strategy_lab"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import asyncio

    try:
        asyncio.get_running_loop()
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
    ARM_AFTER_MFE_GRID,
    COST_PER_TRADE,
    FALLBACK_KILL_PCT,
    LIVE_KILL_TIGHTEN_AFTER_T1,
    LIVE_T1_BANK_PCT,
    LIVE_TRAIL_PCT_OFF_HIGH,
    LOCK_WIDTH_FLOOR,
    LOCK_WIDTH_FRAC,
    RIPPER_MFE_PCT,
    TRAIL_EVERYTHING_ARM_MFE_PCT,
    emergency_kill_schedule,
    lock_trail_width,
    measure_path_giveback,
    path_from_facts,
    percentile,
    replay_paper_path,
    resolve_mae_trail_width,
    score_grid_result,
    summarize_closed_state,
    summarize_slices,
)

# Recent Peak Hour names from LEARNING_AUTOPSY_20260911 (ticker hints only).
RECENT_PHP_TICKERS = (
    "ARQQ", "ATRC", "BETA", "CAI", "CBLL", "CLYM", "CNH", "FGI",
    "FWDI", "METC", "NX", "OCUL", "PURR", "QMCO", "RDW", "SLS",
)

DEFAULT_SHARES = 8  # ≥8 so Mode A gets 4 live tranches (40/30/20/10)
HOLD_SESSIONS = 5  # trading days after entry (signal-time hold window)
DEFAULT_ASOF = "2026-09-16"
# Prefer B for live later only if rippers are not worse than this vs A.
RIPPER_MEAN_TOLERANCE = 0.0  # require B ≥ A on rippers (no give)
GTL_RATE_SLACK = 0.05  # allow +5pp GTL vs A
# 2-bar reconstructions cannot show from-entry chop; exclude from rec.
MIN_BARS_FOR_RECOMMENDATION = 3

MODE_ORDER = (
    "A",
    "B_arm3",
    "B_arm4",
    "B_arm3_lock",
    "B_arm4_lock",
    "C",
)

MODE_LABELS = {
    "A": "live php_keep_profit_v1 (T1 bank +2% of entry)",
    "B_arm3": "arm-after-MFE +3% of entry · MAE-p50 trail · kill follows trail",
    "B_arm4": "arm-after-MFE +4% of entry · MAE-p50 trail · kill follows trail",
    "B_arm3_lock": "arm-after-MFE +3% · lock width (70% of MAE-p50)",
    "B_arm4_lock": "arm-after-MFE +4% · lock width (70% of MAE-p50)",
    "C": "PR #16 Mode B contrast: trail-from-entry MAE-p50 (arm=0)",
}

B_MODES = ("B_arm3", "B_arm4", "B_arm3_lock", "B_arm4_lock")


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


def _pct(x: Any) -> str:
    if x is None:
        return "n/a"
    return f"{float(x) * 100:.2f}%"


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
    """Load a ticker profile if present (read-only; several profile roots)."""
    names = (
        f"{symbol.upper()}_tsd_profile.json",
        f"{symbol.upper()}_profile.json",
    )
    for folder in PROFILES_DIRS:
        for name in names:
            path = folder / name
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
    return None


def _profile_mae_p50(profile: dict[str, Any] | None) -> float | None:
    """MAE-p50 from either TSD or analog-finder profile shape."""
    if not profile:
        return None
    mae = profile.get("mae") or {}
    if mae.get("p50") is not None:
        return _safe_float(mae.get("p50"))
    pct = profile.get("percentiles") or {}
    mae2 = pct.get("mae") or {}
    return _safe_float(mae2.get("p50"))


def _profile_mae_p75(profile: dict[str, Any] | None) -> float | None:
    """MAE-p75 for emergency kill band."""
    if not profile:
        return None
    mae = profile.get("mae") or {}
    if mae.get("p75") is not None:
        return _safe_float(mae.get("p75"))
    pct = profile.get("percentiles") or {}
    mae2 = pct.get("mae") or {}
    return _safe_float(mae2.get("p75"))


def analogs_from_profile_and_corpus(
    symbol: str,
    *,
    before_date: str,
    fixture: dict[str, Any] | None,
    corpus_rows: list[dict[str, Any]],
    profile: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Prior-only analogs for `symbol` (entry_date < before_date)."""
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
        mae = _profile_mae_p50(profile) or 0.0
        mfe = 0.0
        raw_mfe = (profile.get("mfe") or {}).get("p50")
        if raw_mfe is None:
            raw_mfe = ((profile.get("percentiles") or {}).get("mfe") or {}).get("p50")
        mfe = float(raw_mfe or 0.0)
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


def estimate_ticker_widths(
    symbol: str,
    analogs: list[dict[str, Any]],
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Per-ticker MAE–MFE widths from prior analogs only (no look-ahead).

    Trail width = MAE-p50 % off high. Lock width = 70% of that (floor 1%).
    Emergency kill = MAE-p75 in [2%, 6%] else 5% fallback.
    """
    mae_vals: list[float] = []
    mfe_vals: list[float] = []
    for a in analogs:
        px = _safe_float(a.get("entry_price") or a.get("close"))
        bars = list(a.get("bars") or [])
        if px and bars:
            gb = measure_path_giveback(px, bars)
            if gb.get("mae_peak_pct") is not None:
                mae_vals.append(float(gb["mae_peak_pct"]))
            if gb.get("mfe_peak_pct") is not None:
                mfe_vals.append(float(gb["mfe_peak_pct"]))
        else:
            mae = _safe_float(a.get("mae_pct") or a.get("mae"))
            mfe = _safe_float(a.get("mfe_pct") or a.get("mfe"))
            if mae is not None:
                mae_vals.append(mae)
            if mfe is not None:
                mfe_vals.append(mfe)

    if profile:
        p50 = _profile_mae_p50(profile)
        p75 = _profile_mae_p75(profile)
        if p50 is not None:
            mae_vals.append(p50)
        if p75 is not None:
            mae_vals.append(p75)

    mae_p50 = percentile(mae_vals, 50) if mae_vals else None
    mae_p75 = percentile(mae_vals, 75) if mae_vals else None
    mfe_p50 = percentile(mfe_vals, 50) if mfe_vals else None
    trail = resolve_mae_trail_width(mae_p50)
    lock = lock_trail_width(mae_p50)
    kill = FALLBACK_KILL_PCT
    kill_src = "fallback_5pct"
    if mae_p75 is not None and 0.02 <= float(mae_p75) <= 0.06:
        kill = float(mae_p75)
        kill_src = "analog_mae_p75"
    return {
        "symbol": symbol.upper(),
        "n_analogs": len(analogs),
        "mae_p50": round(mae_p50, 6) if mae_p50 is not None else None,
        "mae_p75": round(mae_p75, 6) if mae_p75 is not None else None,
        "mfe_p50": round(mfe_p50, 6) if mfe_p50 is not None else None,
        "trail_pct_off_high": round(trail, 6),
        "lock_trail_pct_off_high": round(lock, 6),
        "emergency_kill_pct": round(kill, 6),
        "kill_source": kill_src,
        "status": "OK" if len(analogs) >= 3 else ("HEURISTIC" if analogs else "FALLBACK"),
    }


# ---------------------------------------------------------------------------
# Mode replay
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


def _mode_paper_kwargs(mode: str, widths: dict[str, Any]) -> dict[str, Any]:
    """Paper replay knobs for one named mode."""
    trail = float(widths.get("trail_pct_off_high") or LIVE_TRAIL_PCT_OFF_HIGH)
    lock = float(widths.get("lock_trail_pct_off_high") or lock_trail_width(trail))
    kill = emergency_kill_schedule(widths.get("emergency_kill_pct"))
    if mode == "C":
        return {
            "trail_pct_off_high": trail,
            "trail_arm_mfe_pct": TRAIL_EVERYTHING_ARM_MFE_PCT,
            "kill_schedule": kill,
            "ratchet_kill_with_trail": False,
        }
    arm = 0.03 if "arm3" in mode else 0.04
    width = lock if mode.endswith("_lock") else trail
    return {
        "trail_pct_off_high": width,
        "trail_arm_mfe_pct": arm,
        "kill_schedule": kill,
        "ratchet_kill_with_trail": True,
    }


def replay_paper(
    entries: list[dict[str, Any]],
    mode: str,
    *,
    widths: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Replay one schedule mode across entries. Read-only vs live keep-profit.

    Each entry must already have `bars`. Returns book PnL + slices in % of entry.
    """
    mode = str(mode).strip()
    widths = widths or {}
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
                "left_on_table_pct_of_entry": 0.0,
                "green_then_lost": False,
            })
            continue
        gb = measure_path_giveback(float(ent["entry_price"]), bars)
        w = widths.get(sym) or estimate_ticker_widths(sym, [])
        if mode == "A":
            sim = _replay_mode_a(ent, bars)
        elif mode in MODE_LABELS:
            kw = _mode_paper_kwargs(mode, w)
            sim = replay_paper_path(
                float(ent["entry_price"]),
                bars,
                shares=int(ent.get("shares") or DEFAULT_SHARES),
                mode=mode,
                **kw,
            )
        else:
            raise ValueError(f"unknown mode {mode!r}")
        rows.append({
            "symbol": sym,
            "entry_date": ent.get("entry_date"),
            "entry_hour": ent.get("entry_hour"),
            "source": ent.get("source"),
            "n_bars": gb.get("n_bars"),
            "recommendable": int(gb.get("n_bars") or 0) >= MIN_BARS_FOR_RECOMMENDATION,
            "path": gb,
            **sim,
        })

    scored = score_grid_result([r for r in rows if r.get("status") != "no_bars"])
    slices = summarize_slices(rows)
    book_pnl = round(sum(float(r.get("pnl") or 0.0) for r in rows), 4)
    return {
        "mode": mode,
        "label": MODE_LABELS.get(mode, mode),
        "book_pnl": book_pnl,
        "book_pnl_pct_of_entry_mean": scored.get("mean_pnl_pct_of_entry"),
        "book_pnl_pct_of_entry_median": scored.get("median_pnl_pct_of_entry"),
        "capture_frac_of_mfe_mean": scored.get("mean_capture_frac_of_mfe"),
        "capture_frac_when_mfe_ge_1pct_mean": scored.get(
            "mean_capture_frac_when_mfe_ge_1pct"
        ),
        "left_on_table_pct_of_entry_mean": scored.get("mean_left_on_table_pct_of_entry"),
        "green_then_lost_rate": scored.get("green_then_lost_rate"),
        "score": scored.get("score"),
        "n": scored.get("n"),
        "n_no_bars": sum(1 for r in rows if r.get("status") == "no_bars"),
        "slices": slices,
        "runtime_s": round(time.time() - t0, 3),
        "trades": rows,
    }


def recommendable_subset(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries with enough bars to judge early-chop vs arm-after-MFE."""
    out = []
    for ent in entries:
        n = len(ent.get("bars") or [])
        if n >= MIN_BARS_FOR_RECOMMENDATION:
            out.append(ent)
    return out


def decide_recommendation(
    rec_modes: dict[str, Any],
    *,
    sample_label: str,
    fixture_only: bool = False,
) -> dict[str, Any]:
    """
    Prefer a B variant for live *later* only if it beats A on all-paths mean
    % of entry AND is not worse on rippers (PR #16 failure mode).

    Returns a structured verdict. Never patches live from this function.
    """
    a = rec_modes.get("A")
    if not a or not a.get("n"):
        return {
            "prefer_b_for_live_later": False,
            "verdict": "INCONCLUSIVE",
            "reason": "Mode A produced no recommendable trades.",
            "best_b_on_rippers": None,
            "sample": sample_label,
            "invite_human_review": True,
        }

    a_all = float(a.get("book_pnl_pct_of_entry_mean") or 0.0)
    a_rip = (a.get("slices") or {}).get("rippers") or {}
    a_rip_mean = a_rip.get("mean_pnl_pct_of_entry")
    a_gtl = float(a.get("green_then_lost_rate") or 0.0)
    a_n_rip = int(a_rip.get("n") or 0)

    ranked: list[dict[str, Any]] = []
    for key in B_MODES:
        m = rec_modes.get(key)
        if not m or not m.get("n"):
            continue
        rip = (m.get("slices") or {}).get("rippers") or {}
        ranked.append({
            "mode": key,
            "label": m.get("label"),
            "all_mean": m.get("book_pnl_pct_of_entry_mean"),
            "all_median": m.get("book_pnl_pct_of_entry_median"),
            "ripper_mean": rip.get("mean_pnl_pct_of_entry"),
            "ripper_left_on_table": rip.get("mean_left_on_table_pct_of_entry"),
            "ripper_n": rip.get("n"),
            "gtl": m.get("green_then_lost_rate"),
        })

    if not ranked:
        return {
            "prefer_b_for_live_later": False,
            "verdict": "INCONCLUSIVE",
            "reason": "No B-mode trades on the recommendable sample.",
            "best_b_on_rippers": None,
            "sample": sample_label,
            "invite_human_review": True,
        }

    def _ripper_key(row: dict[str, Any]) -> tuple[float, float]:
        mean = row["ripper_mean"]
        left = row["ripper_left_on_table"]
        # Highest ripper mean, then least left on table.
        return (
            float(mean) if mean is not None else -999.0,
            -float(left) if left is not None else -999.0,
        )

    ranked.sort(key=_ripper_key, reverse=True)
    best = ranked[0]

    reasons: list[str] = []
    prefer = True
    if best["all_mean"] is None or float(best["all_mean"]) < a_all:
        prefer = False
        reasons.append(
            f"{best['mode']} all-paths mean {_pct(best['all_mean'])} "
            f"< A {_pct(a_all)}"
        )
    if a_rip_mean is None or a_n_rip == 0:
        reasons.append("no ripper slice on A — cannot clear the PR #16 gate")
        prefer = False
    elif best["ripper_mean"] is None:
        prefer = False
        reasons.append(f"{best['mode']} has no ripper mean")
    elif float(best["ripper_mean"]) + RIPPER_MEAN_TOLERANCE < float(a_rip_mean):
        prefer = False
        reasons.append(
            f"{best['mode']} rippers {_pct(best['ripper_mean'])} "
            f"< A rippers {_pct(a_rip_mean)} (PR #16 failure mode)"
        )
    if best["gtl"] is not None and float(best["gtl"]) > a_gtl + GTL_RATE_SLACK:
        prefer = False
        reasons.append(
            f"{best['mode']} green-then-lost {_pct(best['gtl'])} "
            f"> A {_pct(a_gtl)} + {_pct(GTL_RATE_SLACK)}"
        )

    if fixture_only:
        prefer = False
        verdict = "MECHANISM_ONLY"
        reason = (
            "Designed fixture (not live 1H): arm-after-MFE keeps the ripper "
            f"that from-entry loses. Least-bad B on rippers = {best['mode']} "
            f"at {_pct(best['ripper_mean'])} of entry vs A "
            f"{_pct(a_rip_mean)}. Do **not** prefer B for live until a "
            "book / h1_bar_cache / Polygon replay confirms the same gate."
        )
    elif prefer:
        verdict = "WATCH"
        reason = (
            f"{best['mode']} clears A on all-paths and rippers in this sample. "
            "Still paper-only — invite human review before any live patch."
        )
    else:
        verdict = "DO_NOT_PREFER_B_FOR_LIVE"
        reason = (
            "Do not prefer arm-after-MFE over live A yet. "
            + ("; ".join(reasons) if reasons else "B did not beat A.")
        )

    return {
        "prefer_b_for_live_later": prefer,
        "verdict": verdict,
        "reason": reason,
        "best_b_on_rippers": best,
        "a_all_mean_pct_of_entry": a_all,
        "a_ripper_mean_pct_of_entry": a_rip_mean,
        "a_green_then_lost_rate": a_gtl,
        "ranked_b_on_rippers": ranked,
        "sample": sample_label,
        "invite_human_review": True,
        "live_patch_authorized": False,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _slice_row(mode_key: str, mode: dict[str, Any], slice_name: str) -> str:
    sl = (mode.get("slices") or {}).get(slice_name) or {}
    return (
        f"| {mode_key} | {sl.get('n') if sl.get('n') is not None else 0} | "
        f"{_pct(sl.get('mean_pnl_pct_of_entry'))} | "
        f"{_pct(sl.get('median_pnl_pct_of_entry'))} | "
        f"{_pct(sl.get('green_then_lost_rate'))} | "
        f"{_pct(sl.get('mean_left_on_table_pct_of_entry'))} |"
    )


def write_markdown(doc: dict[str, Any], path: Path) -> None:
    """Ops-facing summary next to the JSON dump."""
    modes = doc.get("modes") or {}
    rec = doc.get("recommendation") or {}
    best = rec.get("best_b_on_rippers") or {}
    lines = [
        f"# Peak Hour arm-after-MFE trail study ({doc.get('asof')})",
        "",
        "PAPER-ONLY. Zero edits to live `tsd_keep_profit` / `tsd_trail` / "
        "`tsd_trail_monitor` / `tsd_kill` / entry gates.",
        "",
        f"- Universe: **{doc.get('universe_source')}** · n={doc.get('n_entries')} "
        f"entries / {doc.get('n_tickers')} tickers",
        f"- Bar source: **{doc.get('bar_source')}**",
        f"- Recommendable sample (n_bars≥{MIN_BARS_FOR_RECOMMENDATION}): "
        f"**{doc.get('n_recommendable')}** · {doc.get('recommendation_sample')}",
        f"- Runtime: **{doc.get('runtime_s')}s**",
        f"- Language: % of entry and % off high. "
        f"“+1R” in older notes = +{_pct(FALLBACK_KILL_PCT)} of entry "
        f"(live fallback kill).",
        f"- Prior paper: PR #16 trail-from-entry MAE-p50 lost on rippers — "
        f"do not prefer that B over A. This study arms **after** MFE.",
        "",
        "## Recommendation (live later — not a patch)",
        "",
        f"- **Verdict:** `{rec.get('verdict')}`",
        f"- **Prefer B for live later:** **{rec.get('prefer_b_for_live_later')}**",
        f"- **Least-bad B on rippers:** `{best.get('mode')}` "
        f"(rippers mean {_pct(best.get('ripper_mean'))}, "
        f"left-on-table {_pct(best.get('ripper_left_on_table'))})",
        f"- A all-paths mean: {_pct(rec.get('a_all_mean_pct_of_entry'))} · "
        f"A rippers: {_pct(rec.get('a_ripper_mean_pct_of_entry'))}",
        f"- Why: {rec.get('reason')}",
        f"- **Invite human review before any live Peak Hour patch.**",
        "",
        "## Mode book (same entries)",
        "",
        "| Mode | Book $ | Mean % of entry | Median % | GTL | Left on table vs MFE | Score |",
        "|------|-------:|----------------:|---------:|----:|---------------------:|------:|",
    ]
    for key in MODE_ORDER:
        m = modes.get(key)
        if not m:
            continue
        lines.append(
            f"| {key} {m.get('label', '')} | {m.get('book_pnl'):+.2f} | "
            f"{_pct(m.get('book_pnl_pct_of_entry_mean'))} | "
            f"{_pct(m.get('book_pnl_pct_of_entry_median'))} | "
            f"{_pct(m.get('green_then_lost_rate'))} | "
            f"{_pct(m.get('left_on_table_pct_of_entry_mean'))} | "
            f"{m.get('score')} |"
        )

    for slice_name, title in (
        ("all", "All paths"),
        ("rippers", f"Rippers (path MFE≥{_pct(RIPPER_MFE_PCT)})"),
        ("grinders", f"Grinders (path MFE<{_pct(RIPPER_MFE_PCT)})"),
        ("green_then_lost", "Green-then-lost"),
    ):
        lines += [
            "",
            f"## Slice — {title}",
            "",
            "| Mode | n | Mean % of entry | Median % | GTL | Left on table |",
            "|------|--:|----------------:|---------:|----:|--------------:|",
        ]
        for key in MODE_ORDER:
            m = modes.get(key)
            if not m:
                continue
            lines.append(_slice_row(key, m, slice_name))

    lines += [
        "",
        "## Per-ticker MAE–MFE widths (prior analogs only)",
        "",
        "| Ticker | Status | n analogs | MAE-p50 | Trail % off high | Lock % off high | Kill floor |",
        "|--------|--------|----------:|--------:|-----------------:|----------------:|-----------:|",
    ]
    for w in doc.get("widths") or []:
        lines.append(
            f"| {w.get('symbol')} | {w.get('status')} | {w.get('n_analogs')} | "
            f"{_pct(w.get('mae_p50'))} | {_pct(w.get('trail_pct_off_high'))} | "
            f"{_pct(w.get('lock_trail_pct_off_high'))} | "
            f"{_pct(w.get('emergency_kill_pct'))} |"
        )
    lines += [
        "",
        "## How to re-run",
        "",
        "```text",
        "py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --write",
        "py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --dry-run",
        "cd candidates && python -m tsd_scan_pipeline.php_arm_after_mfe_trail_study --dry-run",
        "```",
        "",
        "Needs `POLYGON_API_KEY` in `.env` for fresh 1H bars. Without a key the",
        "script uses fixture / cached `results/h1_bar_cache` / profiles /",
        "EXP-0021 path facts (MFE+MAE 2-bar reconstruction — degrade).",
        "",
        f"Degrade notes: {doc.get('degrade_notes') or 'none'}",
        "",
        "Fixture / dry-run / 2-bar numbers are not a live Sharpe claim.",
        "Do not change live kill/trail gates from this file.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_novelty_note(doc: dict[str, Any], path: Path) -> None:
    """
    Short novelty note vs PR #16.

    No BestStrategyFinder path existed on main or in PR #16; this is the
    paper-study equivalent so a later finder pass can ingest it.
    """
    rec = doc.get("recommendation") or {}
    best = rec.get("best_b_on_rippers") or {}
    text = "\n".join([
        "# Novelty — arm-after-MFE trail (vs PR #16)",
        "",
        "PR #16 Mode B trailed MAE-p50 **from entry**. Slightly better overall",
        "in some samples, but **lost on rippers** (MFE≥4% of entry). Strategy",
        "Finder: do not prefer that B over live A.",
        "",
        "This note is the next design: trail **arms only after +3% / +4% MFE**.",
        "Widths from per-ticker prior MAE–MFE (p50 / lock=70% of p50). Kill",
        "ratchets up with the trail. No hard bank. No live patch.",
        "",
        f"- asof: {doc.get('asof')}",
        f"- verdict: {rec.get('verdict')}",
        f"- prefer_b_for_live_later: {rec.get('prefer_b_for_live_later')}",
        f"- least-bad B on rippers: {best.get('mode')} "
        f"(mean {_pct(best.get('ripper_mean'))})",
        f"- sample: {rec.get('sample')}",
        "",
        "Invite human review before any live Peak Hour change.",
        "",
    ])
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_study(args: argparse.Namespace) -> dict[str, Any]:
    """Full study: universe → widths → A / B-arm / C replay → artifacts."""
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

    modes_wanted = [m.strip() for m in str(args.modes).split(",") if m.strip()]
    for m in modes_wanted:
        if m not in MODE_LABELS:
            raise SystemExit(f"unknown mode {m!r} (use {','.join(MODE_ORDER)})")

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

    for ent in entries:
        if ent.get("bars"):
            continue
        if fixture:
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

    widths: dict[str, dict[str, Any]] = {}
    for i, sym in enumerate(symbols, 1):
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
        widths[sym] = estimate_ticker_widths(
            sym, analogs, profile=profiles.get(sym),
        )
        if i % 25 == 0 or i == len(symbols):
            print(f"  widths {i}/{len(symbols)}  last={sym} {widths[sym]['status']}")

    mode_docs: dict[str, Any] = {}
    for mode in modes_wanted:
        print(f"Replaying mode {mode}...")
        mode_docs[mode] = replay_paper(entries, mode, widths=widths)
        m = mode_docs[mode]
        print(
            f"  {mode}  book=${m['book_pnl']:+.2f}  "
            f"mean%={_pct(m['book_pnl_pct_of_entry_mean'])}  "
            f"rip%={_pct(((m.get('slices') or {}).get('rippers') or {}).get('mean_pnl_pct_of_entry'))}  "
            f"GTL={_pct(m['green_then_lost_rate'])}"
        )

    rec_entries = recommendable_subset(entries)
    rec_sample = (
        f"n_bars>={MIN_BARS_FOR_RECOMMENDATION} ({len(rec_entries)}/{len(entries)})"
    )
    if rec_entries and len(rec_entries) < len(entries):
        rec_modes = {
            mode: replay_paper(rec_entries, mode, widths=widths)
            for mode in modes_wanted
        }
        degrade.append(
            f"recommendation uses {len(rec_entries)} multi-bar paths; "
            f"{len(entries) - len(rec_entries)} 2-bar fact paths excluded"
        )
    elif rec_entries:
        rec_modes = mode_docs
    else:
        rec_modes = {}
        rec_sample = "none — only 2-bar path facts (inconclusive for live)"
        degrade.append(
            "no multi-bar paths; recommendation is INCONCLUSIVE for live"
        )

    fixture_only = bool(args.dry_run) or uni_src == "fixture"
    if rec_modes:
        recommendation = decide_recommendation(
            rec_modes, sample_label=rec_sample, fixture_only=fixture_only,
        )
    else:
        recommendation = decide_recommendation(
            {}, sample_label=rec_sample, fixture_only=fixture_only,
        )

    runtime = round(time.time() - t0, 3)
    doc = {
        "paper_only": True,
        "asof": asof,
        "universe_source": uni_src,
        "bar_source": bar_source,
        "n_entries": len(entries),
        "n_tickers": len(symbols),
        "n_recommendable": len(rec_entries),
        "recommendation_sample": rec_sample,
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
            "lock_width_frac_of_mae_p50": LOCK_WIDTH_FRAC,
            "lock_width_floor": LOCK_WIDTH_FLOOR,
            "ripper_mfe_pct_of_entry": RIPPER_MFE_PCT,
            "arm_after_mfe_grid": list(ARM_AFTER_MFE_GRID),
        },
        "widths": [widths[s] for s in symbols],
        "modes": mode_docs,
        "recommendation": recommendation,
        "degrade_notes": "; ".join(degrade) if degrade else "none",
        "runtime_s": runtime,
        "live_files_edited": [],
        "note": (
            "PAPER-ONLY. Mode A calls live php_process_bar read-only. "
            "B/C use php_early_trail_kill_paper.paper_process_bar. "
            "Do not change live Peak Hour gates from these numbers."
        ),
    }
    return doc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PAPER-ONLY Peak Hour arm-after-MFE trail study",
    )
    p.add_argument("--asof", default=DEFAULT_ASOF, help="YYYY-MM-DD as-of (signal-time cut)")
    p.add_argument("--dry-run", action="store_true", help="Use bundled fixture; no Polygon")
    p.add_argument("--fixture", default="", help="Optional fixture JSON path")
    p.add_argument("--entries", default="", help="Optional entries / replay JSON")
    p.add_argument(
        "--modes",
        default=",".join(MODE_ORDER),
        help=f"Comma list of {','.join(MODE_ORDER)}",
    )
    p.add_argument("--max-entries", type=int, default=48, help="Cap replay entries")
    p.add_argument("--write", action="store_true", default=True, help="Write results JSON+MD")
    p.add_argument("--no-write", action="store_true", help="Skip results artifacts")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_write:
        args.write = False
    print("=" * 72)
    print("PHP ARM-AFTER-MFE TRAIL STUDY  (PAPER-ONLY)")
    print(f"asof={args.asof}  dry_run={bool(args.dry_run)}  modes={args.modes}")
    print("=" * 72)
    doc = run_study(args)
    stamp = _asof_stamp(doc["asof"])
    if args.dry_run:
        stamp = f"{stamp}_dryrun"
    json_path = RESULTS / f"php_arm_after_mfe_trail_{stamp}.json"
    md_path = RESULTS / f"php_arm_after_mfe_trail_{stamp}.md"
    novelty_path = RESULTS / f"novelty_php_arm_after_mfe_{stamp}.md"
    if args.write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        write_markdown(doc, md_path)
        write_novelty_note(doc, novelty_path)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(f"Wrote {novelty_path}")
    rec = doc.get("recommendation") or {}
    print(
        f"Runtime {doc['runtime_s']}s  universe={doc['universe_source']}  "
        f"bars={doc['bar_source']}  verdict={rec.get('verdict')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
