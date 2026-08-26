"""
strategy_lab/live_forward.py — Monday live-forward paper runner (Polygon-paper).

NO IBKR. NO auto-scheduler — start manually.

CONFIG (locked):
  Entry  = immediate (09:30 first 1-min close)
  Exits  = BOTH pools in parallel:
             Pool A = Strategy A (Trailing)
             Pool B = Strategy B (Target)
           $3000 each, 1% risk, Aaron capacity PER POOL:
             MAX_NEW_ENTRIES_PER_DAY = 3 (candidate list capped top-3
               in existing scan order — already quality/rank_score desc)
             MAX_FULL_SLOTS = 10 where "full" = residual T1/T2/T3 still
               working; T4-only runner does NOT consume a full slot
  Tag    = sweep_reclaim pass/fail (informational only — not a gate)

FLOW (current trading day, or replay of a past date via run_day):
  1. is_trading_day guard (ET weekend/holiday) → "Market closed today" + stop
  2. ~09:35: full-market gap scan (agent Polygon scan) → cap to top 3
     (scan order) → profile → fetch premarket bars for the day
  3. Wait for 09:30 1-min bar (LOAD-BEARING: 15-min delayed feed) → ENTRY ONLY
     (store open_positions; do not run strategies / book P&L)
  4. Intraday --mark (Task ~30m 10:00–16:00 ET) + EOD --settle (~16:20 ET,
     optional 16:40 backup): refresh bars, update marks / residual_tranche_ids,
     book closes, force-upsert Supabase strategy_lab_state
  5. Persist continuously to results/forward_state.json + EOD summary

Feed: Polygon/Massive Stocks Developer — 15-min DELAYED, unlimited REST.

COMPOUNDING (LIVE only):
  $3000 is a one-time start (set by reset_forward.py). Each new live date
  RESUMES pool value, closed-trades history, equity curve, and the prediction
  log from forward_state.json — it does NOT reset to $3000 each morning.
  Replay/dry-run writes forward_state_replay.json and does not clobber the
  live book or Supabase. Same capacity gates as LIVE.

Usage (from repo root):
  py -3 strategy_lab/live_forward.py --replay 2026-08-21   # dry-run
  py -3 strategy_lab/live_forward.py                       # live today (manual)
  py -3 strategy_lab/reset_forward.py                      # wipe live book → $3000
  from live_forward import run_day
  run_day("2026-08-21")
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(CANDIDATES))

from batch_profile import profile_one  # noqa: E402
from entry_models import immediate, sweep_reclaim  # noqa: E402
from fetch_premarket import (  # noqa: E402
    MIN_PM_BARS,
    filter_premarket,
    premarket_stats,
    rth_open_price,
    _slim_bars,
)
from replay import (  # noqa: E402
    MAX_FULL_SLOTS,
    MAX_NEW_ENTRIES_PER_DAY,
    START_POOL_USD,
    bars_path_for,
    full_slots_used,
    limit_candidates_for_entry,
    load_daily_cached,
    pool_stats,
    print_summary,
    run_with_pool_sizing,
    size_for_pool,
    tranche_rows,
)
from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    RISK_FRAC,
    extract_levels,
    load_minute_bars,
    load_profile,
    run_strategy_a,
)
from strategy_b import run_strategy_b  # noqa: E402
from entry_study import fetch_minute_bars, load_polygon_key  # noqa: E402
from full_market_scan import (  # noqa: E402
    OUTPUT_DIR as SCAN_DIR,
    TOP_N_CANDIDATES,
    scan_for_agent,
)
from state_paths import is_trading_day  # noqa: E402
from ticker_profiler import _load_polygon_key as load_profiler_key  # noqa: E402

# Load .env so TELEGRAM_* is available when importing agent's send_telegram.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


def lab_telegram(message: str, *, dry_run: bool = False) -> bool:
    """
    Best-effort Strategy Lab Telegram via autonomous_agent.send_telegram.
    Never raises. Returns True if send was attempted without exception.
    Dry-run / replay messages are prefixed with [DRY-RUN].
    """
    try:
        # Import inside try so a missing agent dep never kills the runner.
        from autonomous_agent import send_telegram

        text = str(message or "").strip()
        if not text:
            return False
        if dry_run and not text.startswith("[DRY-RUN]"):
            text = f"[DRY-RUN] {text}"
        send_telegram(text)
        return True
    except Exception as exc:
        print(f"[live_forward] WARN: Telegram skipped ({exc})")
        return False


def _is_dry_run(mode: str | None = None, state: dict[str, Any] | None = None) -> bool:
    m = (mode or (state or {}).get("mode") or "").lower()
    return m == MODE_REPLAY


ET = ZoneInfo("America/New_York")
STATE_PATH = LAB / "results" / "forward_state.json"
REPLAY_STATE_PATH = LAB / "results" / "forward_state_replay.json"
PREDICTIONS_PATH = LAB / "results" / "forward_predictions.json"
EOD_DIR = LAB / "results"
SETUPS_PATH = LAB / "results" / "setups.json"
PREMARKET_JSON = LAB / "results" / "premarket.json"
SLEEP_SEC = 0.15

ENTRY_MODEL = "immediate"
MODE_LIVE = "live"
MODE_REPLAY = "replay"


# ---------------------------------------------------------------------------
# Forward prediction log + rolling OOS R² (reuses oos_r2 math)
# ---------------------------------------------------------------------------

def load_forward_predictions() -> list[dict[str, Any]]:
    """Growing cross-day log of {ticker, date, predicted_mfe, actual_mfe}."""
    if not PREDICTIONS_PATH.exists():
        return []
    try:
        doc = json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))
        rows = doc.get("predictions") if isinstance(doc, dict) else doc
        return list(rows or [])
    except (OSError, json.JSONDecodeError):
        return []


def save_forward_predictions(rows: list[dict[str, Any]]) -> None:
    """Persist prediction log locally (best-effort)."""
    try:
        PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now_iso(),
            "n": len(rows),
            "predictions": rows,
        }
        PREDICTIONS_PATH.write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )
    except OSError as exc:
        print(f"[live_forward] WARN: could not write predictions ({exc})")


def _prediction_key(ticker: str, flag_date: str) -> str:
    return f"{str(ticker).upper()}|{str(flag_date)[:10]}"


def compute_forward_oos_stats(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Rolling OOS R² across completed forward pairs. True OOS by construction.

    Only rows with both predicted_mfe and actual_mfe count. Display-only
    actual_mfe_provisional is never a completion signal and is ignored here.
    N<2 → collecting_data (R² undefined). Never raises.
    """
    completed = [
        r for r in rows
        if r.get("predicted_mfe") is not None
        and r.get("actual_mfe") is not None
    ]
    n = len(completed)
    base = {
        "n": n,
        "r2": None,
        "correlation": None,
        "rmse_pct": None,
        "mean_predicted_mfe": None,
        "mean_actual_mfe": None,
        "status": "collecting_data",
        "message": f"collecting data (N={n})",
    }
    if n < 2:
        return base
    try:
        from oos_r2 import pearson, rmse, r_squared

        y = [float(r["actual_mfe"]) for r in completed]
        p = [float(r["predicted_mfe"]) for r in completed]
        r2 = r_squared(y, p)
        corr = pearson(y, p)
        err = rmse(y, p)
        base.update({
            "r2": round(r2, 6) if r2 is not None else None,
            "correlation": round(corr, 6) if corr is not None else None,
            "rmse_pct": round(err, 4) if err is not None else None,
            "mean_predicted_mfe": round(sum(p) / n, 4),
            "mean_actual_mfe": round(sum(y) / n, 4),
            "status": "ok" if r2 is not None else "undefined",
            "message": (
                f"forward OOS R²={r2:.4f} (N={n})"
                if r2 is not None
                else f"R² undefined (N={n})"
            ),
        })
        if r2 is not None and r2 < 0:
            base["message"] += (
                " — negative R²: worse than predicting the average MFE"
            )
    except Exception as exc:
        base["status"] = "error"
        base["message"] = f"R² compute skipped ({exc})"
    return base


def upsert_forward_prediction(
    rows: list[dict[str, Any]],
    *,
    ticker: str,
    flag_date: str,
    predicted_mfe: float | None,
    actual_mfe: float | None,
) -> list[dict[str, Any]]:
    """Insert or update one ticker|date row; return new list."""
    key = _prediction_key(ticker, flag_date)
    out = [r for r in rows if _prediction_key(
        str(r.get("ticker") or ""), str(r.get("date") or r.get("flag_date") or "")
    ) != key]
    out.append({
        "ticker": str(ticker).upper(),
        "date": str(flag_date)[:10],
        "predicted_mfe": (
            round(float(predicted_mfe), 4)
            if predicted_mfe is not None
            else None
        ),
        "actual_mfe": (
            round(float(actual_mfe), 4)
            if actual_mfe is not None
            else None
        ),
        "updated_at": _now_iso(),
    })
    out.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("ticker") or "")))
    return out


def refresh_forward_r2(state: dict[str, Any]) -> None:
    """Recompute rolling R² from state predictions; sync file + state fields."""
    rows = list(state.get("forward_predictions") or [])
    stats = compute_forward_oos_stats(rows)
    state["forward_predictions"] = rows
    state["forward_oos_r2"] = stats.get("r2")
    state["forward_oos_r2_n"] = stats.get("n")
    state["forward_oos_r2_stats"] = stats
    # Replay must not pollute the live prediction log on disk.
    if str(state.get("mode") or "").lower() != MODE_REPLAY:
        save_forward_predictions(rows)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return datetime.now(ET).date().isoformat()


def empty_state(flag_date: str, mode: str) -> dict[str, Any]:
    """
    Brand-new book at START_POOL_USD.

    LIVE production must prefer resume_live_book() — only reset_forward.py
    (or a missing file on first live day) should mint a fresh $3000 book.
    """
    # Replay stays isolated from the live prediction log.
    prior = (
        [] if str(mode).lower() == MODE_REPLAY else load_forward_predictions()
    )
    r2_stats = compute_forward_oos_stats(prior)
    return {
        "updated_at": _now_iso(),
        "mode": mode,
        "flag_date": flag_date,
        "entry_model": ENTRY_MODEL,
        "phase": "init",
        "status": "running",
        "message": "",
        "candidates": [],
        "n_candidates": 0,
        "pool_A_trailing": {
            "label": "Strategy A (Trailing)",
            "value_usd": START_POOL_USD,
            "start_usd": START_POOL_USD,
            "open_positions": {},
            "closed_trades": [],
            "equity_curve": [
                {"t": _now_iso(), "value_usd": START_POOL_USD, "event": "start"}
            ],
            "slots_open": 0,
            "slots_peak": 0,
            "wins": 0,
            "trades_taken": 0,
            "win_rate_pct": None,
        },
        "pool_B_target": {
            "label": "Strategy B (Target)",
            "value_usd": START_POOL_USD,
            "start_usd": START_POOL_USD,
            "open_positions": {},
            "closed_trades": [],
            "equity_curve": [
                {"t": _now_iso(), "value_usd": START_POOL_USD, "event": "start"}
            ],
            "slots_open": 0,
            "slots_peak": 0,
            "wins": 0,
            "trades_taken": 0,
            "win_rate_pct": None,
        },
        "scan": None,
        "premarket": {},
        "eod_summary": None,
        "report": None,
        "forward_predictions": prior,
        "forward_oos_r2": r2_stats.get("r2"),
        "forward_oos_r2_n": r2_stats.get("n"),
        "forward_oos_r2_stats": r2_stats,
    }


def load_state_file() -> dict[str, Any] | None:
    """Load results/forward_state.json. None if missing/unreadable."""
    if not STATE_PATH.exists():
        return None
    try:
        raw = STATE_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def resume_live_book(prior: dict[str, Any], flag_date: str) -> dict[str, Any]:
    """
    Carry pools, closed-trade history, equity curves, and prediction log
    into a new live session day. Does NOT reset value_usd to $3000.
    """
    state = dict(prior)
    state["updated_at"] = _now_iso()
    state["mode"] = MODE_LIVE
    state["flag_date"] = flag_date
    state["entry_model"] = ENTRY_MODEL
    state["phase"] = "init"
    state["status"] = "running"
    state["message"] = (
        f"resumed live book from prior session "
        f"(prior_date={prior.get('flag_date')})"
    )
    state["candidates"] = []
    state["n_candidates"] = 0
    state["scan"] = None
    state["premarket"] = {}
    state["eod_summary"] = None
    state["report"] = None

    for key, label in (
        ("pool_A_trailing", "Strategy A (Trailing)"),
        ("pool_B_target", "Strategy B (Target)"),
    ):
        pool = dict(state.get(key) or {})
        pool.setdefault("label", label)
        pool.setdefault("start_usd", START_POOL_USD)
        pool["value_usd"] = float(pool.get("value_usd") or START_POOL_USD)
        # Preserve overnight opens across live days (settle owns closing them).
        pool["open_positions"] = dict(pool.get("open_positions") or {})
        pool["slots_open"] = full_slots_used(pool)
        pool["n_open_positions"] = len(pool["open_positions"])
        pool.setdefault("closed_trades", [])
        pool.setdefault("equity_curve", [])
        pool.setdefault("slots_peak", 0)
        pool.setdefault("wins", 0)
        pool.setdefault("trades_taken", 0)
        # Mark day boundary on the multi-day equity curve.
        pool["equity_curve"] = list(pool.get("equity_curve") or [])
        pool["equity_curve"].append({
            "t": _now_iso(),
            "value_usd": round(float(pool["value_usd"]), 4),
            "event": "day_open",
            "flag_date": flag_date,
        })
        state[key] = pool

    # Predictions file is source of truth across days; refresh R² fields.
    rows = load_forward_predictions()
    if not rows:
        rows = list(state.get("forward_predictions") or [])
    state["forward_predictions"] = rows
    stats = compute_forward_oos_stats(rows)
    state["forward_oos_r2"] = stats.get("r2")
    state["forward_oos_r2_n"] = stats.get("n")
    state["forward_oos_r2_stats"] = stats
    return state


def begin_day_state(flag_date: str, mode: str) -> dict[str, Any]:
    """
    LIVE: resume persisted book (compound). Missing file → fresh $3000 once.
    REPLAY: isolated empty book (does not load the live multi-day pools).
    """
    if mode == MODE_LIVE:
        prior = load_state_file()
        if prior and isinstance(prior.get("pool_A_trailing"), dict):
            a = float((prior.get("pool_A_trailing") or {}).get("value_usd") or 0)
            b = float((prior.get("pool_B_target") or {}).get("value_usd") or 0)
            n_closed = len(
                (prior.get("pool_A_trailing") or {}).get("closed_trades") or []
            )
            print(
                f"[live_forward] RESUME live book  "
                f"A=${a:.2f}  B=${b:.2f}  "
                f"closed_A={n_closed}  "
                f"prior_date={prior.get('flag_date')}  "
                f"→ session {flag_date}"
            )
            return resume_live_book(prior, flag_date)
        print(
            f"[live_forward] no prior live book — starting fresh "
            f"${START_POOL_USD:.0f} / ${START_POOL_USD:.0f}"
        )
        return empty_state(flag_date, mode)
    # Replay / dry-run: never inherit live compounding pools.
    print(
        f"[live_forward] REPLAY isolated book @ ${START_POOL_USD:.0f} "
        f"(will not overwrite live forward_state.json)"
    )
    return empty_state(flag_date, mode)


def save_state(state: dict[str, Any], *, force_sync: bool = False) -> None:
    """
    Persist continuously so a crash mid-day still leaves a readable snapshot.

    LIVE → results/forward_state.json + Supabase.
    REPLAY → results/forward_state_replay.json only (never clobber the live book
    or Cloud dashboard with dry-run artifacts).
    """
    state["updated_at"] = _now_iso()
    mode = str(state.get("mode") or "").lower()
    path = STATE_PATH if mode != MODE_REPLAY else REPLAY_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    if mode == MODE_REPLAY:
        return

    try:
        from lab_state_sync import upsert_forward_state

        phase = str(state.get("phase") or "")
        status = str(state.get("status") or "")
        force = force_sync or phase in ("eod", "stopped") or status in (
            "complete",
            "market_closed",
            "no_candidates",
            "awaiting_first_live_run",
        )
        upsert_forward_state(state, force=force)
    except Exception as exc:
        print(f"[live_forward] WARN: Supabase sync skipped ({exc})")


def set_phase(state: dict[str, Any], phase: str, message: str = "") -> None:
    state["phase"] = phase
    state["message"] = message
    # Force sync on phase boundaries so Cloud sees scan/profile/trading/eod.
    save_state(state, force_sync=True)
    print(f"[live_forward] phase={phase}" + (f"  {message}" if message else ""))


# ---------------------------------------------------------------------------
# Scan / profile / premarket
# ---------------------------------------------------------------------------

def load_scan_archive(flag_date: str) -> list[dict[str, Any]]:
    """Load candidates/full_scan/scan_{date}.json top_n (replay / dry-run)."""
    path = SCAN_DIR / f"scan_{flag_date}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = doc.get("top_n") or doc.get("candidates_all") or []
    out: list[dict[str, Any]] = []
    for c in raw:
        t = str(c.get("ticker") or "").upper().strip()
        if not t:
            continue
        gap = c.get("gap_pct")
        # Archive stores gap as percent (28.25) OR already fraction — normalize.
        gap_frac = None
        if gap is not None:
            g = float(gap)
            gap_frac = g / 100.0 if g > 1.0 else g
        out.append({
            "ticker": t,
            "flag_date": flag_date,
            "gap_pct": gap_frac,
            "pm_vol_ratio": c.get("pm_vol_ratio") or c.get("rvol"),
            "ref_price": c.get("ref_price") or c.get("last_price"),
            "source": "full_scan_archive",
            "quality_score": c.get("rank_score") or c.get("quality_score"),
        })
    return out


def load_setups_for_day(flag_date: str) -> list[dict[str, Any]]:
    """Fallback candidate list from results/setups.json."""
    if not SETUPS_PATH.exists():
        return []
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for s in doc.get("setups") or []:
        d = str(s.get("flag_date") or "")[:10]
        if d != flag_date:
            continue
        t = str(s.get("ticker") or "").upper().strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({
            "ticker": t,
            "flag_date": d,
            "gap_pct": s.get("gap_pct"),
            "pm_vol_ratio": s.get("vol_ratio"),
            "ref_price": None,
            "source": str(s.get("source") or "setups"),
            "quality_score": s.get("score"),
        })
    out.sort(key=lambda r: r["ticker"])
    return out


def merge_candidates(
    primary: list[dict[str, Any]],
    extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Primary first; append extras not already present (ticker-unique)."""
    seen = {c["ticker"] for c in primary}
    out = list(primary)
    for c in extra:
        if c["ticker"] in seen:
            continue
        seen.add(c["ticker"])
        out.append(c)
    return out


def run_scan_phase(flag_date: str, mode: str) -> list[dict[str, Any]]:
    """
    Live: call full_market_scan.scan_for_agent().
    Replay: load archived scan; merge setups so lab-known names still trade.
    """
    print(f"[live_forward] SCAN  date={flag_date}  mode={mode}")
    if mode == MODE_LIVE:
        raw = scan_for_agent(top_n=TOP_N_CANDIDATES)
        cands = []
        for c in raw:
            cands.append({
                "ticker": str(c["ticker"]).upper(),
                "flag_date": flag_date,
                "gap_pct": c.get("gap_pct"),
                "pm_vol_ratio": c.get("pm_vol_ratio"),
                "ref_price": c.get("last_price"),
                "source": "full_market_scan",
                "quality_score": c.get("quality_score"),
            })
        print(f"[live_forward] scan done — {len(cands)} candidates")
        return cands

    archived = load_scan_archive(flag_date)
    setups = load_setups_for_day(flag_date)
    cands = merge_candidates(archived, setups)
    print(
        f"[live_forward] scan done (replay) — "
        f"{len(archived)} from archive + {len(setups)} setups "
        f"→ {len(cands)} unique: {[c['ticker'] for c in cands]}"
    )
    return cands


def run_profile_phase(candidates: list[dict[str, Any]], flag_date: str) -> None:
    """Batch-profile today's candidates into strategy_lab/profiles/."""
    print(f"[live_forward] PROFILE  {len(candidates)} candidates → lab profiles/")
    if not candidates:
        return
    api_key = load_profiler_key()
    for i, c in enumerate(candidates, start=1):
        t = c["ticker"]
        print(f"  [{i}/{len(candidates)}] profile {t}|{flag_date}", flush=True)
        _profile, status = profile_one(t, flag_date, api_key, force=False)
        print(f"    → {status}", flush=True)


def _update_premarket_json(flag_date: str, rows: dict[str, dict[str, Any]]) -> None:
    """Merge today's PM stats into results/premarket.json (accumulate)."""
    doc: dict[str, Any] = {}
    if PREMARKET_JSON.exists():
        try:
            doc = json.loads(PREMARKET_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = {}
    pm = dict(doc.get("premarket") or {})
    pm.update(rows)
    doc["premarket"] = pm
    doc["generated_at"] = _now_iso()
    doc["informational_only"] = True
    doc.setdefault("window_et", "04:00-09:30")
    doc.setdefault("min_pm_bars", MIN_PM_BARS)
    PREMARKET_JSON.parent.mkdir(parents=True, exist_ok=True)
    PREMARKET_JSON.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def run_premarket_phase(
    candidates: list[dict[str, Any]],
    flag_date: str,
    *,
    mode: str,
) -> dict[str, dict[str, Any]]:
    """
    Fetch / reuse premarket 1-min bars for RSI/gap-regime accumulation.
    Replay prefers existing *_premarket.json / full bars; live always fetches.
    """
    print(f"[live_forward] PREMARKET  fetching/reusing PM bars for {flag_date}")
    api_key = load_polygon_key()
    out: dict[str, dict[str, Any]] = {}

    for i, c in enumerate(candidates, start=1):
        ticker = c["ticker"]
        key = f"{ticker}|{flag_date}"
        print(f"  [{i}/{len(candidates)}] PM {key}", flush=True)

        pm_path = BARS_DIR / f"{ticker}_{flag_date}_premarket.json"
        full_path = BARS_DIR / f"{ticker}_{flag_date}.json"
        raw: list[dict] = []

        if mode == MODE_REPLAY and pm_path.exists():
            pm_slim = list(
                json.loads(pm_path.read_text(encoding="utf-8")).get("bars") or []
            )
            open_0930 = None
            if full_path.exists():
                full = load_minute_bars(full_path)
                open_0930 = rth_open_price(
                    [
                        {
                            "t": b["t"], "o": b["o"], "h": b["h"],
                            "l": b["l"], "c": b["c"], "v": b.get("v") or 0,
                        }
                        for b in full
                    ]
                ) if full else None
            stats = premarket_stats(pm_slim)
            stats["open_0930"] = (
                round(open_0930, 4) if open_0930 is not None else None
            )
            stats["ticker"] = ticker
            stats["flag_date"] = flag_date
            stats["premarket_bars_path"] = str(
                pm_path.relative_to(ROOT)
            ).replace("\\", "/")
            out[key] = stats
            print(
                f"    reuse file  available={stats['premarket_available']}  "
                f"bars={stats['n_premarket_bars']}"
            )
            continue

        # Live or missing cache: Polygon fetch.
        try:
            minute_raw = fetch_minute_bars(ticker, flag_date, api_key)
            time.sleep(SLEEP_SEC)
        except Exception as exc:
            out[key] = {
                "ticker": ticker,
                "flag_date": flag_date,
                "premarket_available": False,
                "n_premarket_bars": 0,
                "reason": f"fetch_error: {exc}",
                "premarket_median": None,
                "premarket_vwap": None,
                "open_0930": None,
            }
            print(f"    fetch error: {exc}")
            continue

        pm_raw = filter_premarket(minute_raw)
        pm_slim = _slim_bars(pm_raw)
        stats = premarket_stats(pm_slim)
        open_0930 = rth_open_price(minute_raw)
        BARS_DIR.mkdir(parents=True, exist_ok=True)
        pm_path.write_text(
            json.dumps({
                "ticker": ticker,
                "flag_date": flag_date,
                "session": "premarket",
                "window_et": "04:00-09:30",
                "n_bars": len(pm_slim),
                "bars": pm_slim,
            }, indent=2),
            encoding="utf-8",
        )
        # Also refresh full-day bars cache used by strategies.
        if minute_raw:
            full_path.write_text(
                json.dumps({
                    "ticker": ticker,
                    "flag_date": flag_date,
                    "n_bars": len(minute_raw),
                    "bars": _slim_bars(minute_raw),
                }, indent=2),
                encoding="utf-8",
            )

        stats["ticker"] = ticker
        stats["flag_date"] = flag_date
        stats["open_0930"] = (
            round(open_0930, 4) if open_0930 is not None else None
        )
        stats["premarket_bars_path"] = str(
            pm_path.relative_to(ROOT)
        ).replace("\\", "/")
        out[key] = stats
        print(
            f"    ok  available={stats['premarket_available']}  "
            f"bars={stats['n_premarket_bars']}  "
            f"med={stats.get('premarket_median')}  "
            f"vwap={stats.get('premarket_vwap')}"
        )

    _update_premarket_json(flag_date, out)
    n_ok = sum(1 for r in out.values() if r.get("premarket_available"))
    print(f"[live_forward] premarket done — usable={n_ok}/{len(out)}")
    return out


# ---------------------------------------------------------------------------
# Dual-pool execution (reuse replay sizing / strategy runners)
# ---------------------------------------------------------------------------

def _ensure_bars(
    ticker: str,
    flag_date: str,
    hist_row: dict | None,
) -> Path | None:
    path = bars_path_for(ticker, flag_date, hist_row)
    if path.exists():
        return path
    # Try lab default path.
    alt = BARS_DIR / f"{ticker}_{flag_date}.json"
    return alt if alt.exists() else None


def _equity_append(pool: dict[str, Any], value: float, event: str, ticker: str) -> None:
    pool["equity_curve"].append({
        "t": _now_iso(),
        "value_usd": round(value, 4),
        "event": event,
        "ticker": ticker,
    })


def _win_rate(pool: dict[str, Any]) -> None:
    n = int(pool.get("trades_taken") or 0)
    w = int(pool.get("wins") or 0)
    pool["win_rate_pct"] = round(100.0 * w / n, 2) if n else None



def execute_dual_pools(
    candidates: list[dict[str, Any]],
    flag_date: str,
    state: dict[str, Any],
    *,
    settle_after: bool = False,
    close_at_data_end: bool = True,
    refresh_bars: bool | None = None,
) -> dict[str, Any]:
    """
    ENTRY-only dual-pool open (no sync strategy run / no P and L).

    If settle_after=True (replay / tests): immediately settle with full data
    so one-shot results match the pre-split regression baseline.
    LIVE morning entry uses settle_after=False; --settle (and morning
    auto-settle of overnight opens) books P and L later.
    """
    from forward_book import entry_open_positions, settle_open_positions, settle_ticker

    mode = str(state.get("mode") or MODE_LIVE).lower()
    if refresh_bars is None:
        refresh_bars = mode == MODE_LIVE

    def _after(ticker: str) -> None:
        if not settle_after:
            return
        settle_ticker(
            state,
            ticker,
            close_at_data_end=close_at_data_end,
            refresh_data=False,
            upsert_prediction=upsert_forward_prediction,
            refresh_r2=refresh_forward_r2,
            allow_finalize_mfe=(mode != MODE_REPLAY),
        )
        save_state(state)

    entry_report = entry_open_positions(
        candidates,
        flag_date,
        state,
        refresh_bars=refresh_bars,
        lab_telegram=lab_telegram,
        dry_run=_is_dry_run(state=state),
        save_state=save_state,
        upsert_prediction=upsert_forward_prediction,
        refresh_r2=refresh_forward_r2,
        after_ticker=_after if settle_after else None,
    )

    settle_report = None
    if settle_after:
        # Per-ticker settle already ran; summarize remaining opens (should be 0).
        settle_report = {
            "mode": "per_ticker_sequential",
            "open_A": list((state["pool_A_trailing"].get("open_positions") or {}).keys()),
            "open_B": list((state["pool_B_target"].get("open_positions") or {}).keys()),
            "pool_A_usd": float(state["pool_A_trailing"]["value_usd"]),
            "pool_B_usd": float(state["pool_B_target"]["value_usd"]),
        }
        save_state(state)

    from replay import pool_stats

    day_start_a = float(
        entry_report.get("day_start_A_usd")
        or state["pool_A_trailing"].get("start_usd")
        or START_POOL_USD
    )
    day_start_b = float(
        entry_report.get("day_start_B_usd")
        or state["pool_B_target"].get("start_usd")
        or START_POOL_USD
    )
    pool_a = float(state["pool_A_trailing"]["value_usd"])
    pool_b = float(state["pool_B_target"]["value_usd"])
    trades_a = [
        t for t in (state["pool_A_trailing"].get("closed_trades") or [])
        if str(t.get("flag_date") or flag_date)[:10] == flag_date and t.get("taken")
    ]
    trades_b = [
        t for t in (state["pool_B_target"].get("closed_trades") or [])
        if str(t.get("flag_date") or flag_date)[:10] == flag_date and t.get("taken")
    ]
    peak_a = int(state["pool_A_trailing"].get("slots_peak") or 0)
    peak_b = int(state["pool_B_target"].get("slots_peak") or 0)

    report = {
        "generated_at": _now_iso(),
        "mode": state.get("mode"),
        "flag_date": flag_date,
        "entry_model": ENTRY_MODEL,
        "start_pool_usd": float(
            state["pool_A_trailing"].get("start_usd") or START_POOL_USD
        ),
        "day_start_A_usd": round(day_start_a, 4),
        "day_start_B_usd": round(day_start_b, 4),
        "max_slots": MAX_FULL_SLOTS,
        "max_full_slots": MAX_FULL_SLOTS,
        "max_new_entries_per_day": MAX_NEW_ENTRIES_PER_DAY,
        "n_setups": len(candidates),
        "tickers": entry_report.get("tickers") or [c["ticker"] for c in candidates],
        "per_ticker": entry_report.get("per_ticker") or [],
        "entry": entry_report,
        "settle": settle_report,
        "pool_A_trailing": {
            **pool_stats(trades_a, day_start_a, pool_a, peak_a),
            "lifetime_start_usd": float(
                state["pool_A_trailing"].get("start_usd") or START_POOL_USD
            ),
            "trades": trades_a,
            "open_positions": list(
                (state["pool_A_trailing"].get("open_positions") or {}).keys()
            ),
        },
        "pool_B_target": {
            **pool_stats(trades_b, day_start_b, pool_b, peak_b),
            "lifetime_start_usd": float(
                state["pool_B_target"].get("start_usd") or START_POOL_USD
            ),
            "trades": trades_b,
            "open_positions": list(
                (state["pool_B_target"].get("open_positions") or {}).keys()
            ),
        },
    }
    if pool_a > pool_b:
        winner, margin = "A_trailing", pool_a - pool_b
    elif pool_b > pool_a:
        winner, margin = "B_target", pool_b - pool_a
    else:
        winner, margin = "tie", 0.0
    report["winner"] = {
        "pool": winner,
        "margin_usd": round(margin, 4),
        "A_end_usd": round(pool_a, 4),
        "B_end_usd": round(pool_b, 4),
    }
    return report


def run_settle(
    *,
    close_at_data_end: bool = False,
    refresh_data: bool = True,
    quiet: bool = False,
    label: str = "settle",
) -> dict[str, Any]:
    """
    Settle/mark pass: refresh bars and re-run strategies on open_positions.
    LIVE default: close_at_data_end=False (no phantom time_cap at last print).

    quiet=True (--mark): skip routine Telegram; still alert if positions close.
    Weekend/holiday: ET is_trading_day guard (same calendar as run_day entry).
    """
    from forward_book import settle_open_positions
    from forward_runtime import acquire_live_lock, release_live_lock

    today_et = datetime.now(ET).date()
    if not is_trading_day(today_et):
        msg = (
            f"market closed ({today_et.isoformat()}) — nothing to {label}"
        )
        print(f"[live_forward] {label}: {msg}")
        if not quiet:
            lab_telegram(
                f"🧪 Strategy Lab {label.title()} — market closed, nothing to do",
                dry_run=False,
            )
        return {
            "status": "market_closed",
            "flag_date": today_et.isoformat(),
            "message": msg,
        }

    state = load_state_file()
    if not state:
        print(f"[live_forward] {label}: no forward_state.json — nothing to do")
        return {"status": "no_state"}

    mode = str(state.get("mode") or MODE_LIVE).lower()
    dry = mode == MODE_REPLAY
    locked = False
    try:
        if mode == MODE_LIVE:
            acquire_live_lock(label)
            locked = True
        set_phase(state, label, f"{label} open positions")
        result = settle_open_positions(
            state,
            close_at_data_end=close_at_data_end,
            refresh_data=refresh_data and mode == MODE_LIVE,
            upsert_prediction=upsert_forward_prediction,
            refresh_r2=refresh_forward_r2,
            allow_finalize_mfe=(mode != MODE_REPLAY),
        )
        state["phase"] = f"{label}_done"
        n_open = int(result.get("open_positions") or 0)
        if n_open:
            state["status"] = "open_positions"
        elif str(state.get("status")) in ("running", "open_positions"):
            state["status"] = "complete"
        stamp = {
            "at": _now_iso(),
            **{
                k: result[k]
                for k in (
                    "closed_A", "closed_B", "pool_A_usd", "pool_B_usd",
                    "open_positions", "open_A", "open_B",
                )
                if k in result
            },
        }
        state["last_settle"] = stamp
        if quiet or label == "mark":
            state["last_mark"] = stamp
        # Always force-push so Cloud Strategy Lab updated_at advances.
        save_state(state, force_sync=(mode == MODE_LIVE))
        a = float(result.get("pool_A_usd") or 0)
        b = float(result.get("pool_B_usd") or 0)
        closed_a = int(result.get("closed_A") or 0)
        closed_b = int(result.get("closed_B") or 0)
        open_a = int(result.get("open_A") or 0)
        open_b = int(result.get("open_B") or 0)
        msg = (
            f"🧪 {label.upper()}: Pool A ${a:.2f}, Pool B ${b:.2f}, "
            f"closed A={closed_a} B={closed_b}, "
            f"open A={open_a} B={open_b} "
            f"(names, not combined slots). "
            f"Forward R² N={int(state.get('forward_oos_r2_n') or 0)}."
        )
        if quiet:
            # Intraday marks: only Telegram when something actually closed.
            if closed_a or closed_b:
                lab_telegram(msg, dry_run=dry)
        else:
            lab_telegram(msg, dry_run=dry)
        print(f"[live_forward] {label} done: {result}")
        return {"status": "ok", **result}
    finally:
        if locked:
            release_live_lock()


# run_day — public API (replay past day OR drive live today)
# ---------------------------------------------------------------------------


def run_day(
    day: str | None = None,
    *,
    mode: str | None = None,
    force_rerun: bool = False,
) -> dict[str, Any]:
    """
    Scan → profile → premarket → ENTRY (open positions only).

    LIVE: does not settle same morning (use --mark / --settle tasks). Auto-runs
    settle first for any overnight opens. REPLAY: entry then settle inline
    (close_at_data_end=True) for regression parity.
    """
    from forward_runtime import (
        acquire_live_lock,
        release_live_lock,
        same_day_entry_blocked,
        wait_for_0930_bar,
    )
    from forward_book import load_bars_for_entry
    from entry_models import immediate as _immediate

    flag_date = str(day or _today_et())[:10]
    session_day = datetime.strptime(flag_date, "%Y-%m-%d").date()
    today = datetime.now(ET).date()
    if mode is None:
        mode = MODE_REPLAY if session_day != today else MODE_LIVE
    mode = str(mode).lower().strip()
    if mode not in (MODE_LIVE, MODE_REPLAY):
        raise ValueError(f"mode must be '{MODE_LIVE}' or '{MODE_REPLAY}'")

    print("=" * 78)
    print(f"Q-ALPHA LIVE FORWARD  |  {flag_date}  |  mode={mode}")
    print("Polygon-paper  |  NO IBKR  |  entry/settle split")
    print(
        f"Entry={ENTRY_MODEL}  |  exits=A Trailing + B Target  |  "
        f"${START_POOL_USD:.0f} × 2 pools  |  "
        f"max {MAX_NEW_ENTRIES_PER_DAY}/day  |  "
        f"max {MAX_FULL_SLOTS} full slots each (T1–T3)"
    )
    print("=" * 78)

    dry = _is_dry_run(mode)
    locked = False

    try:
        if mode == MODE_LIVE:
            acquire_live_lock("entry")
            locked = True
            prior = load_state_file()
            if same_day_entry_blocked(prior, flag_date) and not force_rerun:
                msg = (
                    f"same-day guard: status=complete for {flag_date} — "
                    "refusing re-entry (pass --force-rerun to override)"
                )
                print(f"[live_forward] {msg}")
                lab_telegram(f"🧪 Strategy Lab — {msg}", dry_run=False)
                return {"status": "skipped_same_day", "flag_date": flag_date, "message": msg}
            # Auto-settle overnight opens before new entries.
            n_open_prior = 0
            if prior:
                n_open_prior = (
                    len((prior.get("pool_A_trailing") or {}).get("open_positions") or {})
                    + len((prior.get("pool_B_target") or {}).get("open_positions") or {})
                )
            if n_open_prior:
                print(f"[live_forward] auto-settle {n_open_prior} overnight open(s) first")
                from forward_book import settle_open_positions
                # Already hold entry lock — settle inline (do not nest run_settle lock).
                prior = load_state_file() or prior
                settle_open_positions(
                    prior,
                    close_at_data_end=False,
                    refresh_data=True,
                    upsert_prediction=upsert_forward_prediction,
                    refresh_r2=refresh_forward_r2,
                    allow_finalize_mfe=True,
                )
                save_state(prior, force_sync=True)

        lab_telegram(
            f"🧪 Strategy Lab STARTED — {flag_date}, entry={ENTRY_MODEL}, "
            f"dual ${START_POOL_USD:.0f} pools.",
            dry_run=dry,
        )

        if not is_trading_day(session_day):
            msg = "Market closed today"
            print(f"[live_forward] {msg} ({flag_date})")
            lab_telegram(
                "🧪 Strategy Lab — market closed today, not running.",
                dry_run=dry,
            )
            state = begin_day_state(flag_date, mode)
            state["status"] = "market_closed"
            state["phase"] = "stopped"
            state["message"] = msg
            save_state(state, force_sync=(mode == MODE_LIVE))
            return {
                "status": "market_closed",
                "flag_date": flag_date,
                "mode": mode,
                "message": msg,
            }

        state = begin_day_state(flag_date, mode)
        try:
            from lab_state_sync import reset_throttle
            reset_throttle()
        except Exception:
            pass
        save_state(state, force_sync=(mode == MODE_LIVE))

        set_phase(state, "scan", "running full-market gap scan")
        candidates = run_scan_phase(flag_date, mode)
        n_scan = len(candidates)
        candidates = limit_candidates_for_entry(candidates)
        if n_scan > len(candidates):
            print(
                f"[live_forward] entry list capped {n_scan} → {len(candidates)} "
                f"(preserve scan order; MAX_NEW_ENTRIES_PER_DAY="
                f"{MAX_NEW_ENTRIES_PER_DAY})"
            )
        state["candidates"] = candidates
        state["n_candidates"] = len(candidates)
        tickers = [c["ticker"] for c in candidates]
        state["scan"] = {
            "n_candidates": len(candidates),
            "n_scan_before_cap": n_scan,
            "tickers": tickers,
            "sources": sorted({str(c.get("source")) for c in candidates}),
        }
        save_state(state)
        print(f"[live_forward] {len(candidates)} candidates ready (entry cap)")
        lab_telegram(
            f"🧪 Scan complete — {len(candidates)} candidates: "
            f"{', '.join(tickers) if tickers else '(none)'}.",
            dry_run=dry,
        )

        if not candidates:
            set_phase(state, "eod", "no candidates — nothing to trade")
            state["status"] = "no_candidates"
            state["eod_summary"] = {"n_candidates": 0, "message": "no candidates"}
            save_state(state)
            lab_telegram(
                f"🧪 EOD: Pool A ${float(state['pool_A_trailing']['value_usd']):.2f} "
                f"(0.00%), Pool B ${float(state['pool_B_target']['value_usd']):.2f} "
                f"(0.00%), trades today: 0, winner: tie. "
                f"open A=0 B=0. Forward R² N={int(state.get('forward_oos_r2_n') or 0)}.",
                dry_run=dry,
            )
            return {
                "status": "no_candidates",
                "flag_date": flag_date,
                "mode": mode,
                "n_candidates": 0,
            }

        set_phase(state, "profile", f"profiling {len(candidates)} candidates")
        run_profile_phase(candidates, flag_date)
        save_state(state)

        set_phase(state, "premarket", "fetching/reusing premarket bars")
        pm = run_premarket_phase(candidates, flag_date, mode=mode)
        state["premarket"] = {
            k: {
                "premarket_available": v.get("premarket_available"),
                "premarket_median": v.get("premarket_median"),
                "premarket_vwap": v.get("premarket_vwap"),
                "open_0930": v.get("open_0930"),
                "n_premarket_bars": v.get("n_premarket_bars"),
            }
            for k, v in pm.items()
        }
        save_state(state)

        # W1 — LOAD-BEARING wait for 09:30 bar under 15-min delayed feed
        # (bar visible ~09:45–09:50 ET). LIVE polls; replay single-shot.
        def _load_bars(t: str, d: str, refresh: bool = False):
            return load_bars_for_entry(t, d, refresh=refresh, hist_row=None)

        ok_0930, waited = wait_for_0930_bar(
            candidates,
            flag_date,
            mode=mode,
            load_bars_fn=_load_bars,
            immediate_fn=_immediate,
        )
        if not ok_0930:
            state["status"] = "no_0930_bar_timeout"
            state["message"] = f"no_0930_bar_timeout waited={waited:.0f}s"
            save_state(state, force_sync=(mode == MODE_LIVE))
            lab_telegram(
                f"🧪 Strategy Lab — no 09:30 bar (timeout {waited:.0f}s), not entering.",
                dry_run=dry,
            )
            return {
                "status": "no_0930_bar_timeout",
                "flag_date": flag_date,
                "waited_sec": waited,
            }

        set_phase(
            state,
            "trading",
            f"entering {len(candidates)} names @ immediate (entry-only)",
        )
        # Replay: settle inline with close_at_data_end=True (full history).
        # Live: leave opens for --settle / 16:40.
        settle_after = mode == MODE_REPLAY
        report = execute_dual_pools(
            candidates,
            flag_date,
            state,
            settle_after=settle_after,
            close_at_data_end=True,
            refresh_bars=(mode == MODE_LIVE),
        )
        try:
            print_summary(report)
        except Exception as exc:
            print(f"[live_forward] WARN: print_summary skipped ({exc})")

        n_open_a = len(state["pool_A_trailing"].get("open_positions") or {})
        n_open_b = len(state["pool_B_target"].get("open_positions") or {})
        n_open = n_open_a + n_open_b
        set_phase(state, "eod", "writing end-of-day / entry summary")
        if mode == MODE_LIVE and n_open:
            state["status"] = "open_positions"
        else:
            state["status"] = "complete"
        try:
            refresh_forward_r2(state)
        except Exception as exc:
            print(f"[live_forward] WARN: forward R² refresh skipped ({exc})")
        r2s = state.get("forward_oos_r2_stats") or {}
        state["eod_summary"] = {
            "n_candidates": len(candidates),
            "tickers": report.get("tickers"),
            "winner": report.get("winner"),
            "open_positions": n_open,
            "open_A": n_open_a,
            "open_B": n_open_b,
            "pool_A": {
                "end_usd": report["pool_A_trailing"]["end_usd"],
                "return_pct": report["pool_A_trailing"]["return_pct"],
                "trades_taken": report["pool_A_trailing"]["trades_taken"],
            },
            "pool_B": {
                "end_usd": report["pool_B_target"]["end_usd"],
                "return_pct": report["pool_B_target"]["return_pct"],
                "trades_taken": report["pool_B_target"]["trades_taken"],
            },
            "forward_oos_r2": state.get("forward_oos_r2"),
            "forward_oos_r2_n": state.get("forward_oos_r2_n"),
            "forward_oos_r2_message": r2s.get("message"),
        }
        state["report"] = {
            "per_ticker": report.get("per_ticker"),
            "winner": report.get("winner"),
            "pool_A_trailing": {
                k: report["pool_A_trailing"][k]
                for k in (
                    "start_usd", "end_usd", "realized_pnl_usd", "return_pct",
                    "trades_taken", "win_rate_pct", "slots_used_peak",
                )
                if k in report["pool_A_trailing"]
            },
            "pool_B_target": {
                k: report["pool_B_target"][k]
                for k in (
                    "start_usd", "end_usd", "realized_pnl_usd", "return_pct",
                    "trades_taken", "win_rate_pct", "slots_used_peak",
                )
                if k in report["pool_B_target"]
            },
        }
        save_state(state, force_sync=(mode == MODE_LIVE))

        a_end = float(report["pool_A_trailing"]["end_usd"])
        b_end = float(report["pool_B_target"]["end_usd"])
        a_ret = report["pool_A_trailing"].get("return_pct")
        b_ret = report["pool_B_target"].get("return_pct")
        n_trades = int(report["pool_A_trailing"].get("trades_taken") or 0)
        w_pool = (report.get("winner") or {}).get("pool") or "tie"
        winner_label = (
            "A" if w_pool == "A_trailing"
            else "B" if w_pool == "B_target"
            else "tie"
        )
        a_ret_s = f"{float(a_ret):+.2f}%" if a_ret is not None else "—"
        b_ret_s = f"{float(b_ret):+.2f}%" if b_ret is not None else "—"
        lab_telegram(
            f"🧪 EOD: Pool A ${a_end:.2f} ({a_ret_s}), "
            f"Pool B ${b_end:.2f} ({b_ret_s}), "
            f"trades today: {n_trades}, winner: {winner_label}, "
            f"open A={n_open_a} B={n_open_b}. "
            f"Forward R² N={int(state.get('forward_oos_r2_n') or 0)}.",
            dry_run=dry,
        )

        eod_path = EOD_DIR / f"forward_{flag_date}.json"
        eod_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        state_written = STATE_PATH if mode != MODE_REPLAY else REPLAY_STATE_PATH
        print(f"\n[live_forward] Wrote {state_written.relative_to(ROOT)}")
        print(f"[live_forward] Wrote {eod_path.relative_to(ROOT)}")
        return report
    finally:
        if locked:
            release_live_lock()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Q-Alpha live-forward paper runner (Polygon-paper, no IBKR)",
    )
    parser.add_argument(
        "--replay",
        metavar="YYYY-MM-DD",
        help="Dry-run / replay a past flag_date (scan archive + cached bars)",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Alias for run_day(date); live if date==today else replay",
    )
    parser.add_argument(
        "--settle",
        action="store_true",
        help="EOD/overnight settle (refresh bars, re-run strategies, Telegram)",
    )
    parser.add_argument(
        "--mark",
        action="store_true",
        help="Intraday mark pass (same engine as settle; quiet Telegram; force sync)",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Override same-day complete guard on LIVE entry",
    )
    args = parser.parse_args()
    dry = bool(args.replay) or (
        bool(args.date) and str(args.date)[:10] != _today_et()
    )

    try:
        if args.mark:
            run_settle(close_at_data_end=False, refresh_data=True, quiet=True, label="mark")
        elif args.settle:
            run_settle(close_at_data_end=False, refresh_data=True, quiet=False, label="settle")
        elif args.replay:
            run_day(args.replay, mode=MODE_REPLAY)
        elif args.date:
            run_day(args.date, force_rerun=args.force_rerun)
        else:
            run_day(_today_et(), mode=MODE_LIVE, force_rerun=args.force_rerun)
        return 0
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        if len(reason) > 180:
            reason = reason[:177] + "..."
        lab_telegram(
            f"🧪 Strategy Lab ERROR: {reason}",
            dry_run=dry,
        )
        print(f"[live_forward] FATAL: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
