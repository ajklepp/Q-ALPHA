#!/usr/bin/env python3
"""
PAPER-ONLY Peak Hour shadow — B_arm4_lock on the live open book.

Live Peak Hour still runs today's exits (A: T1 bank ~+2% of entry + existing
software trail / structure). This logger records what study mode B_arm4_lock
WOULD have done on the same open entries:

  - Arm trail only after path MFE reaches +4% of entry
  - Lock width = 70% of ticker MAE-p50 (floor 1% off high)
  - Kill ratchets UP with the trail — no hard take-profit / bank ladder

No broker orders. Does not mutate tsd_book_state exit fields, pool, kill,
keep-profit, or entry gates.

Flag
----
PHP_ARM_AFTER_MFE_SHADOW  default ON. Set 0 / false / off / no to disable.
See SHADOW.md and REVERT.md.

Hooks (all paper, try/except so live never fails)
-------------------------------------------------
  - each trail-monitor tick after live save_state
  - optional :15 after 1H LAUNCH (scheduler) — does not change the scan
  - manual:  py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_shadow.py --write

Artifacts: candidates/tsd_scan_pipeline/results/php_arm_after_mfe_shadow_YYYYMMDD.{json,md}
Runtime book (gitignored): candidates/tsd_arm4_lock_shadow_book.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
RESULTS_DIR = PIPELINE_DIR / "results"
PROFILES_DIRS = (
    PIPELINE_DIR / "profiles",
    CANDIDATES_DIR / "profiles",
    ROOT / "profiles",
)

for _p in (str(CANDIDATES_DIR), str(ROOT / "strategy_lab"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ET = pytz.timezone("America/New_York")

# Env flag — default ON for paper logging. Revert: PHP_ARM_AFTER_MFE_SHADOW=0
SHADOW_ENV_FLAG = "PHP_ARM_AFTER_MFE_SHADOW"
SHADOW_BOOK_FILE = "tsd_arm4_lock_shadow_book.json"
# Optional Polygon 1H fetch on CLI / after-scan (never on the 30s trail tick).
SHADOW_FETCH_1H_ENV = "PHP_ARM_SHADOW_FETCH_1H"

from tsd_scan_pipeline.php_early_trail_kill_paper import (  # noqa: E402
    B_ARM4_LOCK_ARM_MFE_PCT,
    B_ARM4_LOCK_MODE,
    COST_PER_TRADE,
    FALLBACK_KILL_PCT,
    LIVE_TRAIL_PCT_OFF_HIGH,
    LOCK_WIDTH_FLOOR,
    LOCK_WIDTH_FRAC,
    b_arm4_lock_paper_kwargs,
    lock_trail_width,
    summarize_closed_state,
    walk_paper_bars,
)


def shadow_enabled(env: dict[str, str] | None = None) -> bool:
    """
    True unless PHP_ARM_AFTER_MFE_SHADOW is an explicit off token.

    Default ON so paper logging starts without a live-config change.
    """
    raw = (env or os.environ).get(SHADOW_ENV_FLAG, "1")
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "n"}


def shadow_book_path() -> Path:
    """Runtime paper book — never the live tsd_book_state.json."""
    from state_paths import state_path

    return state_path(SHADOW_BOOK_FILE)


def empty_shadow_book() -> dict[str, Any]:
    return {
        "version": "1",
        "paper_only": True,
        "mode": B_ARM4_LOCK_MODE,
        "live_exits_changed": False,
        "arm_mfe_pct_of_entry": B_ARM4_LOCK_ARM_MFE_PCT,
        "lock_width_frac_of_mae_p50": LOCK_WIDTH_FRAC,
        "lock_width_floor": LOCK_WIDTH_FLOOR,
        "legs": [],
        "closed": [],
        "updated_at": None,
        "notes": (
            "PAPER ONLY. Live Peak Hour still uses keep-profit T1 bank ~+2% "
            "of entry. This book is B_arm4_lock (arm +4% of entry, lock "
            "width 70% of MAE-p50). No broker orders."
        ),
    }


def load_shadow_book() -> dict[str, Any]:
    path = shadow_book_path()
    if not path.is_file():
        return empty_shadow_book()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_shadow_book()
    if not isinstance(doc, dict):
        return empty_shadow_book()
    doc.setdefault("legs", [])
    doc.setdefault("closed", [])
    doc.setdefault("paper_only", True)
    doc.setdefault("mode", B_ARM4_LOCK_MODE)
    doc.setdefault("live_exits_changed", False)
    return doc


def save_shadow_book(doc: dict[str, Any]) -> Path:
    doc["updated_at"] = datetime.now(ET).isoformat()
    doc["paper_only"] = True
    doc["live_exits_changed"] = False
    path = shadow_book_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path


def _safe_float(val: Any, default: float | None = None) -> float | None:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _iso_date(val: Any) -> str:
    return str(val or "").strip()[:10]


def _leg_key(symbol: str, opened_at: str | None, order_id: Any = None) -> str:
    oid = order_id if order_id is not None else ""
    return f"{str(symbol).upper()}|{opened_at or ''}|{oid}"


def profile_mae_p50(profile: dict[str, Any] | None) -> float | None:
    """MAE-p50 from TSD or analog-finder profile shape (prior analogs only)."""
    if not profile:
        return None
    mae = profile.get("mae") or {}
    if mae.get("p50") is not None:
        return _safe_float(mae.get("p50"))
    pct = profile.get("percentiles") or {}
    mae2 = pct.get("mae") or {}
    return _safe_float(mae2.get("p50"))


def profile_mae_p75(profile: dict[str, Any] | None) -> float | None:
    """MAE-p75 for emergency kill band."""
    if not profile:
        return None
    mae = profile.get("mae") or {}
    if mae.get("p75") is not None:
        return _safe_float(mae.get("p75"))
    pct = profile.get("percentiles") or {}
    mae2 = pct.get("mae") or {}
    return _safe_float(mae2.get("p75"))


def load_profile(symbol: str) -> dict[str, Any] | None:
    """Read-only ticker profile (MAE–MFE widths). Never writes profiles."""
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
    try:
        from tsd_scan_pipeline.tsd_trail import load_tsd_profile

        return load_tsd_profile(symbol)
    except Exception:
        return None


def resolve_b_arm4_lock_widths(
    symbol: str,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    B_arm4_lock widths from prior-only profile MAE (no current-path look-ahead).

    Trail lock = 70% of MAE-p50 (floor 1% off high). Emergency kill = MAE-p75
    in [2%, 6%] else 5% of entry fallback.
    """
    prof = profile if profile is not None else load_profile(symbol)
    mae_p50 = profile_mae_p50(prof)
    mae_p75 = profile_mae_p75(prof)
    kill = FALLBACK_KILL_PCT
    kill_src = "fallback_5pct"
    if mae_p75 is not None and 0.02 <= float(mae_p75) <= 0.06:
        kill = float(mae_p75)
        kill_src = "profile_mae_p75"
    kw = b_arm4_lock_paper_kwargs(mae_p50, emergency_kill_pct=kill)
    return {
        "symbol": str(symbol).upper(),
        "mae_p50": round(mae_p50, 6) if mae_p50 is not None else None,
        "mae_p75": round(mae_p75, 6) if mae_p75 is not None else None,
        "lock_trail_pct_off_high": round(float(kw["trail_pct_off_high"]), 6),
        "trail_arm_mfe_pct": float(kw["trail_arm_mfe_pct"]),
        "emergency_kill_pct": round(kill, 6),
        "kill_source": kill_src,
        "paper_kwargs": kw,
    }


def extract_open_live_legs(book: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Open long legs from a live book snapshot (read-only)."""
    if not isinstance(book, dict):
        return []
    out: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        if str(pos.get("status") or "OPEN").upper() != "OPEN":
            continue
        sym = str(pos.get("symbol") or "").upper()
        if not sym:
            continue
        for idx, leg in enumerate(pos.get("legs") or []):
            if str(leg.get("status") or "OPEN").upper() != "OPEN":
                continue
            trail = leg.get("trail") or {}
            px = _safe_float(
                trail.get("entry_price") or leg.get("price") or leg.get("entry_price")
            )
            shares = int(leg.get("shares") or 0)
            if px is None or px <= 0 or shares <= 0:
                continue
            opened = str(
                leg.get("time") or trail.get("opened_at") or pos.get("opened_at") or ""
            )
            out.append({
                "symbol": sym,
                "entry_price": float(px),
                "shares": shares,
                "opened_at": opened,
                "entry_date": _iso_date(opened),
                "entry_hour": int(leg.get("bar_hour") or 0),
                "order_id": leg.get("order_id"),
                "live_leg_key": _leg_key(sym, opened, leg.get("order_id")),
                "leg_index": idx,
                "live_leg": leg,
            })
    return out


def live_remaining_shares(leg: dict[str, Any]) -> int:
    """Shares still open on a live keep-profit trail doc."""
    trail = leg.get("trail") or {}
    rem = 0
    for t in trail.get("tranches") or []:
        if not t.get("closed"):
            rem += int(t.get("shares") or 0)
    if rem > 0:
        return rem
    return int(leg.get("shares") or 0)


def live_t1_banked(leg: dict[str, Any]) -> bool:
    trail = leg.get("trail") or {}
    for t in trail.get("tranches") or []:
        if str(t.get("id") or "") == "T1" and t.get("closed"):
            return True
    return False


def _post_entry_bars_from_frame(
    bars: Any,
    *,
    entry_date: str,
    entry_hour: int,
) -> list[dict[str, Any]]:
    """Slice a 1H DataFrame (or list) to bars after the live entry print."""
    if bars is None:
        return []
    if isinstance(bars, list):
        return [b for b in bars if isinstance(b, dict)]

    out: list[dict[str, Any]] = []
    try:
        for ts, row in bars.iterrows():
            t = ts
            try:
                import pandas as pd

                t = pd.Timestamp(ts)
                t = t.tz_localize(ET) if t.tzinfo is None else t.tz_convert(ET)
            except Exception:
                pass
            d = t.date().isoformat() if hasattr(t, "date") else _iso_date(t)
            hour = int(getattr(t, "hour", 0))
            close_hour = (hour + 1) % 24
            if entry_date and d < entry_date:
                continue
            if entry_date and d == entry_date and close_hour <= int(entry_hour or 0):
                continue
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
                "close": close if close > 0 else (high + low) / 2.0,
                "when": f"{d}T{close_hour:02d}:00:00",
            })
    except Exception:
        return out
    return out


def load_cached_1h(symbol: str) -> Any | None:
    """Disk 1H cache used by tsd_1h_signal (pickle). No Polygon."""
    try:
        from tsd_scan_pipeline.tsd_1h_signal import _disk_get

        return _disk_get(symbol)
    except Exception:
        return None


def maybe_fetch_1h(symbol: str) -> Any | None:
    """
    Optional Polygon 1H. Off by default on trail ticks (rate-limit safe).

    Enable with PHP_ARM_SHADOW_FETCH_1H=1 on CLI / after-scan.
    """
    if str(os.environ.get(SHADOW_FETCH_1H_ENV, "")).strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return None
    try:
        from tsd_scan_pipeline.universe_tsd import load_polygon_key

        key = load_polygon_key()
    except Exception:
        key = os.environ.get("POLYGON_API_KEY")
    if not key:
        return None
    try:
        import time

        from tsd_scan_pipeline.tsd_1h_signal import load_1h_bars

        df = load_1h_bars(symbol, api_key=key)
        time.sleep(0.12)
        return df
    except Exception:
        return None


def quote_to_bar(quote: dict[str, Any] | None, *, when: str) -> dict[str, Any] | None:
    """Turn a trail-monitor quote into a forming 1H-style bar."""
    if not quote:
        return None
    try:
        last = float(quote.get("last") or quote.get("close") or 0)
        high = float(quote.get("high") or last or 0)
        low = float(quote.get("low") or last or 0)
        close = float(quote.get("close") or last or 0)
    except (TypeError, ValueError):
        return None
    if high <= 0 or low <= 0:
        return None
    if close <= 0:
        close = (high + low) / 2.0
    return {"high": high, "low": low, "close": close, "when": when, "source": "quote"}


def bars_for_leg(
    rec: dict[str, Any],
    *,
    quote: dict[str, Any] | None = None,
    when: str | None = None,
    fetch_1h: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """
    Post-entry 1H bars + optional forming quote bar.

    Prefer h1_bar_cache (signal-time / already-closed hours). Quote is the
    in-progress bar so a trail tick can arm/exit without waiting for :00.
    """
    when_s = when or datetime.now(ET).isoformat()
    frame = load_cached_1h(rec["symbol"])
    source = "h1_bar_cache" if frame is not None else "none"
    if frame is None and fetch_1h:
        frame = maybe_fetch_1h(rec["symbol"])
        if frame is not None:
            source = "polygon_1h"
    bars = _post_entry_bars_from_frame(
        frame,
        entry_date=str(rec.get("entry_date") or ""),
        entry_hour=int(rec.get("entry_hour") or 0),
    )
    qbar = quote_to_bar(quote, when=when_s)
    if qbar is not None:
        bars.append(qbar)
        source = source + "+quote" if source != "none" else "quote"
    return bars, source


def paper_trail_stop(state: dict[str, Any]) -> float | None:
    """Current paper trail stop (run_high × (1 − lock width)) once armed."""
    if not state.get("trail_armed"):
        return None
    run_high = float(state.get("run_high") or state.get("peak_high") or 0)
    width = float(state.get("trail_pct_off_high") or 0)
    if run_high <= 0:
        return None
    return round(run_high * (1.0 - width), 6)


def snapshot_from_state(
    rec: dict[str, Any],
    state: dict[str, Any],
    *,
    widths: dict[str, Any],
    bar_source: str,
    when: str,
) -> dict[str, Any]:
    """Compare paper B_arm4_lock path to the live keep-profit leg (read-only)."""
    live = rec.get("live_leg") or {}
    trail = live.get("trail") or {}
    entry = float(rec["entry_price"])
    shares = int(rec["shares"])
    live_rem = live_remaining_shares(live)
    paper_closed = bool(state.get("closed"))
    summary = None
    if paper_closed:
        summary = summarize_closed_state(state, entry_price=entry, shares=shares)
    mfe = float(state.get("mfe_peak_pct") or 0.0)
    mark = _safe_float(state.get("last_close")) or entry
    unreal_pct = ((mark - entry) / entry) if entry > 0 else 0.0
    stop = paper_trail_stop(state)
    return {
        "symbol": rec["symbol"],
        "live_leg_key": rec["live_leg_key"],
        "mode": B_ARM4_LOCK_MODE,
        "paper_only": True,
        "when": when,
        "entry_price": entry,
        "shares": shares,
        "opened_at": rec.get("opened_at"),
        "bar_source": bar_source,
        "mae_p50": widths.get("mae_p50"),
        "lock_trail_pct_off_high": widths.get("lock_trail_pct_off_high"),
        "trail_arm_mfe_pct": B_ARM4_LOCK_ARM_MFE_PCT,
        "paper_mfe_pct_of_entry": round(mfe, 6),
        "paper_mae_pct_of_entry": round(float(state.get("mae_peak_pct") or 0.0), 6),
        "paper_armed": bool(state.get("trail_armed")),
        "paper_closed": paper_closed,
        "paper_remaining": int(state.get("remaining") or 0),
        "paper_kill_price": state.get("kill_price"),
        "paper_trail_stop": stop,
        "paper_mark": mark,
        "paper_unreal_pct_of_entry": round(unreal_pct, 6) if not paper_closed else None,
        "paper_exit_reasons": [str(e.get("reason") or "") for e in (state.get("exits") or [])],
        "paper_pnl_pct_of_entry": None if summary is None else summary.get("pnl_pct_of_entry"),
        "paper_cost": None if summary is None else summary.get("cost"),
        "live_t1_banked": live_t1_banked(live),
        "live_remaining": live_rem,
        "live_kill_pct": _safe_float(trail.get("kill_pct") or live.get("kill_pct")),
        "live_kill_price": _safe_float(trail.get("kill_price")),
        "would_have_exited": paper_closed and live_rem > 0,
        "would_still_be_open": (not paper_closed) and live_rem > 0,
    }


def replay_leg(
    rec: dict[str, Any],
    *,
    quote: dict[str, Any] | None = None,
    when: str | None = None,
    fetch_1h: bool = False,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Walk B_arm4_lock from entry on post-entry bars + optional quote."""
    when_s = when or datetime.now(ET).isoformat()
    widths = resolve_b_arm4_lock_widths(rec["symbol"], profile=profile)
    bars, source = bars_for_leg(rec, quote=quote, when=when_s, fetch_1h=fetch_1h)
    kw = widths["paper_kwargs"]
    state = walk_paper_bars(
        float(rec["entry_price"]),
        bars,
        shares=int(rec["shares"]),
        trail_pct_off_high=float(kw["trail_pct_off_high"]),
        trail_arm_mfe_pct=float(kw["trail_arm_mfe_pct"]),
        kill_schedule=kw["kill_schedule"],
        mode=B_ARM4_LOCK_MODE,
        ratchet_kill_with_trail=True,
    )
    snap = snapshot_from_state(rec, state, widths=widths, bar_source=source, when=when_s)
    snap["n_bars"] = len(bars)
    snap["paper_state"] = {
        "trail_armed": state.get("trail_armed"),
        "closed": state.get("closed"),
        "remaining": state.get("remaining"),
        "peak_high": state.get("peak_high"),
        "run_high": state.get("run_high"),
        "mfe_peak_pct": state.get("mfe_peak_pct"),
        "mae_peak_pct": state.get("mae_peak_pct"),
        "kill_price": state.get("kill_price"),
        "kill_pct": state.get("kill_pct"),
        "trail_pct_off_high": state.get("trail_pct_off_high"),
        "trail_arm_mfe_pct": state.get("trail_arm_mfe_pct"),
        "exits": list(state.get("exits") or []),
        "last_close": state.get("last_close"),
        "last_when": state.get("last_when"),
    }
    return snap


def load_live_book(path: Path | None = None) -> dict[str, Any]:
    """Read-only live book. Never calls save_state."""
    if path is not None and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        from tsd_scan_pipeline.tsd_capacity import load_state

        return load_state()
    except Exception:
        return {"positions": []}


def _material_change(prev: dict[str, Any] | None, snap: dict[str, Any]) -> bool:
    if prev is None:
        return True
    keys = (
        "paper_armed", "paper_closed", "would_have_exited",
        "paper_remaining", "n_bars",
    )
    return any(prev.get(k) != snap.get(k) for k in keys)


def _upsert_leg(book: dict[str, Any], snap: dict[str, Any]) -> None:
    key = snap["live_leg_key"]
    legs = list(book.get("legs") or [])
    closed = list(book.get("closed") or [])
    rest = [l for l in legs if l.get("live_leg_key") != key]
    if snap.get("paper_closed"):
        closed = [c for c in closed if c.get("live_leg_key") != key]
        closed.append(snap)
        book["closed"] = closed
        book["legs"] = rest
    else:
        rest.append(snap)
        book["legs"] = rest


def artifact_paths(asof: str | None = None) -> tuple[Path, Path]:
    day = (asof or datetime.now(ET).strftime("%Y%m%d")).replace("-", "")[:8]
    stem = f"php_arm_after_mfe_shadow_{day}"
    return RESULTS_DIR / f"{stem}.json", RESULTS_DIR / f"{stem}.md"


def _pct(val: Any) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def render_shadow_md(doc: dict[str, Any]) -> str:
    """Human report in % of entry (never R-multiples)."""
    snaps = list(doc.get("snapshots") or [])
    open_n = sum(1 for s in snaps if not s.get("paper_closed"))
    closed_n = sum(1 for s in snaps if s.get("paper_closed"))
    would = [s for s in snaps if s.get("would_have_exited")]
    lines = [
        "# Peak Hour paper shadow — B_arm4_lock",
        "",
        "**PAPER ONLY. Does NOT change live trails, keep-profit, kill, or entries.**",
        "",
        f"- Mode: `{doc.get('mode')}` — arm +{_pct(doc.get('arm_mfe_pct_of_entry'))} of entry, "
        f"lock width {LOCK_WIDTH_FRAC:.0%} of MAE-p50 (floor {_pct(LOCK_WIDTH_FLOOR)} off high)",
        f"- As-of: `{doc.get('asof')}`  updated `{doc.get('updated_at')}`",
        f"- Live exits changed: **{doc.get('live_exits_changed')}**",
        f"- Snapshots this file: {len(snaps)} (paper open {open_n} / paper closed {closed_n})",
        f"- Would-have-exited while live still open: {len(would)}",
        f"- Cost assumed on paper close: {COST_PER_TRADE:.4f} of entry notional",
        "",
        "Disable: `PHP_ARM_AFTER_MFE_SHADOW=0` (see SHADOW.md / REVERT.md).",
        "",
        "| Symbol | Live T1 bank | Live rem | Paper armed | Paper MFE | Lock width | Paper stop | Paper |",
        "|--------|-------------:|---------:|:-----------:|----------:|-----------:|-----------:|-------|",
    ]
    latest_by_key: dict[str, dict[str, Any]] = {}
    for s in snaps:
        latest_by_key[str(s.get("live_leg_key"))] = s
    for s in latest_by_key.values():
        status = "CLOSED " + ",".join(s.get("paper_exit_reasons") or []) if s.get("paper_closed") else "OPEN"
        if s.get("would_have_exited"):
            status = "WOULD EXIT · " + status
        lines.append(
            f"| {s.get('symbol')} | {'yes' if s.get('live_t1_banked') else 'no'} | "
            f"{s.get('live_remaining')} | {'yes' if s.get('paper_armed') else 'no'} | "
            f"{_pct(s.get('paper_mfe_pct_of_entry'))} | {_pct(s.get('lock_trail_pct_off_high'))} | "
            f"{s.get('paper_trail_stop') or '—'} | {status} |"
        )
    lines.extend(["", "## Notes", "",
                  "- Language is **% of entry** / **% off high**. Not R-multiples.",
                  "- Widths come from the ticker profile MAE-p50 (prior analogs), not the live path.",
                  "- Live A still banks T1 at ~+2% of entry. Paper B does not.",
                  ""])
    return "\n".join(lines)


def write_daily_artifact(
    snapshots: list[dict[str, Any]],
    *,
    asof: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Append snapshots to dated JSON + rewrite MD under results/."""
    json_path, md_path = artifact_paths(asof)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if json_path.is_file():
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    prior = list(existing.get("snapshots") or [])
    prior.extend(snapshots)
    # Keep last ~400 ticks so the file stays readable.
    if len(prior) > 400:
        prior = prior[-400:]
    day = (asof or datetime.now(ET).strftime("%Y%m%d")).replace("-", "")[:8]
    doc = {
        "paper_only": True,
        "mode": B_ARM4_LOCK_MODE,
        "asof": day,
        "live_exits_changed": False,
        "arm_mfe_pct_of_entry": B_ARM4_LOCK_ARM_MFE_PCT,
        "lock_width_frac_of_mae_p50": LOCK_WIDTH_FRAC,
        "lock_width_floor": LOCK_WIDTH_FLOOR,
        "cost_per_trade": COST_PER_TRADE,
        "updated_at": datetime.now(ET).isoformat(),
        "n_snapshots": len(prior),
        "snapshots": prior,
        "notes": (
            "PAPER ONLY. Live Peak Hour exits unchanged. B_arm4_lock arm +4% "
            "of entry, lock width 70% of MAE-p50."
        ),
    }
    if extra:
        doc.update(extra)
    json_path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_shadow_md(doc), encoding="utf-8")
    return json_path, md_path


def tick_open_arm_shadows(
    fetch_quote: Callable[[str], dict[str, Any] | None] | None = None,
    *,
    book: dict[str, Any] | None = None,
    when: str | None = None,
    write_artifact: bool = False,
    fetch_1h: bool = False,
) -> list[dict[str, Any]]:
    """
    Advance B_arm4_lock paper state for every open live long.

    Never calls place_tsd_exit / save_state / sync_kill_quantity.
    `book` is read-only. Quotes are optional (forming bar).
    """
    if not shadow_enabled():
        return []
    live = book if book is not None else load_live_book()
    recs = extract_open_live_legs(live)
    if not recs:
        return []
    when_s = when or datetime.now(ET).isoformat()
    shadow = load_shadow_book()
    prev_by_key = {
        str(l.get("live_leg_key")): l
        for l in list(shadow.get("legs") or []) + list(shadow.get("closed") or [])
    }
    snaps: list[dict[str, Any]] = []
    changed = False
    for rec in recs:
        quote = None
        if fetch_quote is not None:
            try:
                quote = fetch_quote(rec["symbol"])
            except Exception:
                quote = None
        snap = replay_leg(rec, quote=quote, when=when_s, fetch_1h=fetch_1h)
        snaps.append(snap)
        if _material_change(prev_by_key.get(snap["live_leg_key"]), snap):
            changed = True
        _upsert_leg(shadow, snap)
        if snap.get("would_have_exited"):
            print(
                f"  SHADOW B_arm4_lock {snap['symbol']}: WOULD EXIT "
                f"{','.join(snap.get('paper_exit_reasons') or [])} "
                f"MFE={snap['paper_mfe_pct_of_entry']:.2%} of entry "
                f"(live still open rem={snap['live_remaining']})"
            )
        elif snap.get("paper_armed") and not (prev_by_key.get(snap["live_leg_key"]) or {}).get("paper_armed"):
            print(
                f"  SHADOW B_arm4_lock {snap['symbol']}: ARMED at "
                f"+{snap['paper_mfe_pct_of_entry']:.2%} of entry "
                f"lock={snap['lock_trail_pct_off_high']:.2%} off high"
            )
    save_shadow_book(shadow)
    if write_artifact or changed:
        write_daily_artifact(snaps)
    return snaps


def run_shadow_pass(
    *,
    book: dict[str, Any] | None = None,
    write: bool = True,
    fetch_1h: bool = False,
    asof: str | None = None,
) -> dict[str, Any]:
    """Manual / after-scan / EOD pass. Paper only."""
    if not shadow_enabled():
        return {"enabled": False, "n": 0, "paper_only": True}
    snaps = tick_open_arm_shadows(
        None,
        book=book,
        write_artifact=write,
        fetch_1h=fetch_1h,
    )
    out: dict[str, Any] = {
        "enabled": True,
        "paper_only": True,
        "live_exits_changed": False,
        "mode": B_ARM4_LOCK_MODE,
        "n": len(snaps),
        "would_have_exited": sum(1 for s in snaps if s.get("would_have_exited")),
        "armed": sum(1 for s in snaps if s.get("paper_armed") and not s.get("paper_closed")),
        "snapshots": snaps,
    }
    if write and snaps:
        jp, mp = write_daily_artifact(snaps, asof=asof)
        out["json_path"] = str(jp)
        out["md_path"] = str(mp)
        print(f"  SHADOW B_arm4_lock wrote {jp.name} ({len(snaps)} open legs)")
    return out


def maybe_run_after_scan() -> dict[str, Any] | None:
    """
    Optional :15-after-scan hook. Does not change launch ranking or entries.

    Safe no-op when the flag is off or the book is flat.
    """
    if not shadow_enabled():
        return None
    try:
        return run_shadow_pass(write=True, fetch_1h=False)
    except Exception as exc:
        print(f"  shadow B_arm4_lock after-scan warn: {exc}")
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PAPER-ONLY B_arm4_lock shadow on the live Peak Hour book",
    )
    p.add_argument("--once", action="store_true", help="One pass (same as --write)")
    p.add_argument("--write", action="store_true", help="Advance + write dated results artifact")
    p.add_argument("--eod", action="store_true", help="EOD snapshot (does not flatten live)")
    p.add_argument("--book", default="", help="Optional live book JSON (read-only)")
    p.add_argument("--asof", default="", help="YYYYMMDD stamp for artifact names")
    p.add_argument(
        "--fetch-1h",
        action="store_true",
        help="Allow Polygon 1H if h1_bar_cache misses (rate-limited 0.12s)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fetch_1h:
        os.environ[SHADOW_FETCH_1H_ENV] = "1"
    book = None
    if args.book:
        book = load_live_book(Path(args.book))
    asof = args.asof or None
    result = run_shadow_pass(
        book=book,
        write=True,
        fetch_1h=bool(args.fetch_1h),
        asof=asof,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "snapshots"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
