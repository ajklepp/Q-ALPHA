"""
strategy_lab/live_forward.py — Monday live-forward paper runner (Polygon-paper).

NO IBKR. NO auto-scheduler — start manually.

CONFIG (locked):
  Entry  = immediate (09:30 first 1-min close)
  Exits  = BOTH pools in parallel:
             Pool A = Strategy A (Trailing)
             Pool B = Strategy B (Target)
           $3000 each, 1% risk, max 10 concurrent slots/pool
  Tag    = sweep_reclaim pass/fail (informational only — not a gate)

FLOW (current trading day, or replay of a past date via run_day):
  1. is_trading_day guard (ET weekend/holiday) → "Market closed today" + stop
  2. ~09:35: full-market gap scan (agent Polygon scan) → profile candidates
     into strategy_lab/profiles/ → fetch premarket bars for the day
  3. ~09:40+: enter at first available 09:30 1-min close; fork into A and B;
     manage exits on 1-min bars (full-bar sim in replay / available bars live)
  4. Persist continuously to results/forward_state.json + EOD summary

Usage (from repo root):
  py -3 strategy_lab/live_forward.py --replay 2026-08-21   # dry-run
  py -3 strategy_lab/live_forward.py                       # live today (manual)
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
    MAX_SLOTS,
    START_POOL_USD,
    bars_path_for,
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

ET = ZoneInfo("America/New_York")
STATE_PATH = LAB / "results" / "forward_state.json"
EOD_DIR = LAB / "results"
SETUPS_PATH = LAB / "results" / "setups.json"
PREMARKET_JSON = LAB / "results" / "premarket.json"
SLEEP_SEC = 0.15

ENTRY_MODEL = "immediate"
MODE_LIVE = "live"
MODE_REPLAY = "replay"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return datetime.now(ET).date().isoformat()


def empty_state(flag_date: str, mode: str) -> dict[str, Any]:
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
    }


def save_state(state: dict[str, Any], *, force_sync: bool = False) -> None:
    """
    Persist continuously so a crash mid-day still leaves a readable snapshot.

    Also best-effort upserts to Supabase (strategy_lab_state) for Cloud
    dashboard — throttled ~45s unless force_sync (use at EOD / terminal).
    Local file write always happens; Supabase failure never raises.
    """
    state["updated_at"] = _now_iso()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    try:
        from lab_state_sync import upsert_forward_state

        phase = str(state.get("phase") or "")
        status = str(state.get("status") or "")
        force = force_sync or phase in ("eod", "stopped") or status in (
            "complete",
            "market_closed",
            "no_candidates",
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
) -> dict[str, Any]:
    """
    Shared immediate entry → fork Strategy A + Strategy B.
    sweep_reclaim is logged as a quality tag only (never blocks entry).
    """
    hist_doc = {}
    if HISTORY_PATH.exists():
        hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    pool_a = float(state["pool_A_trailing"]["value_usd"])
    pool_b = float(state["pool_B_target"]["value_usd"])
    slots_a = 0
    slots_b = 0
    peak_a = int(state["pool_A_trailing"].get("slots_peak") or 0)
    peak_b = int(state["pool_B_target"].get("slots_peak") or 0)

    per_ticker: list[dict[str, Any]] = []
    trades_a: list[dict[str, Any]] = []
    trades_b: list[dict[str, Any]] = []

    print()
    print("=" * 78)
    print(f"LIVE FORWARD — ENTER + FORK  {flag_date}")
    print(f"Entry model: {ENTRY_MODEL}  |  sweep_reclaim = quality tag only")
    print(
        f"Pools: A Trailing + B Target  |  ${START_POOL_USD:.0f} each  |  "
        f"cap {MAX_SLOTS} slots/pool"
    )
    print(f"Candidates: {len(candidates)} → {[c['ticker'] for c in candidates]}")
    print("=" * 78)

    for c in candidates:
        ticker = c["ticker"]
        key = f"{ticker}|{flag_date}"
        hist_row = history.get(key)
        print(f"\n--- entering {ticker} | {flag_date} ---")

        bars_path = _ensure_bars(ticker, flag_date, hist_row)
        if bars_path is None:
            print("  SKIP — missing bars")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "missing_bars",
                "sweep_reclaim": None,
            })
            save_state(state)
            continue

        minute_bars = load_minute_bars(bars_path)
        if not minute_bars:
            print("  SKIP — empty bars")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "empty_bars",
                "sweep_reclaim": None,
            })
            save_state(state)
            continue

        sig = immediate(minute_bars)
        if sig is None:
            print("  SKIP — no immediate (09:30) bar yet")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "no_0930_bar",
                "sweep_reclaim": None,
            })
            save_state(state)
            continue

        entry_price = float(sig.entry_price)
        entry_time = str(sig.entry_time)

        # Quality tag — informational only.
        sw = sweep_reclaim(minute_bars)
        sweep_tag = "pass" if sw is not None else "fail"
        print(
            f"  SHARED ENTRY  ${entry_price:.4f} @ {entry_time}  "
            f"[immediate]  sweep_reclaim={sweep_tag}"
            + (
                f" (would @{sw.entry_time} ${sw.entry_price:.4f})"
                if sw is not None
                else ""
            )
        )

        profile_path = LAB / "profiles" / f"{ticker}_{flag_date}.json"
        if profile_path.exists():
            profile = load_profile(ticker, flag_date)
        else:
            print("  WARN — missing profile; INSUFFICIENT fallbacks")
            profile = {
                "ticker": ticker,
                "as_of_date": flag_date,
                "confidence": "INSUFFICIENT",
                "stats_meaningful": False,
                "bracket": {},
                "percentiles": {},
            }

        levels = extract_levels(profile)
        kill_pct = float(levels["kill_pct"])
        confidence = str(
            profile.get("confidence") or levels.get("confidence") or "?"
        )
        daily_bars = load_daily_cached(ticker, flag_date)
        print(
            f"  kill={kill_pct * 100:.2f}%  conf={confidence}  "
            f"levels={levels.get('source')}"
        )

        # ---- Pool A ----
        a_rec: dict[str, Any] = {
            "pool": "A_trailing",
            "ticker": ticker,
            "taken": False,
            "sweep_reclaim": sweep_tag,
            "entry_model": ENTRY_MODEL,
            "entry_price": entry_price,
            "entry_time": entry_time,
        }
        shares_a = size_for_pool(pool_a, entry_price, kill_pct)
        if slots_a >= MAX_SLOTS:
            a_rec["skip_reason"] = "slots_full"
            print(f"  A  SKIP slots full ({slots_a}/{MAX_SLOTS})")
        elif shares_a < 1:
            a_rec["skip_reason"] = "size_lt_1"
            print(f"  A  SKIP size < 1 (pool=${pool_a:.2f})")
        else:
            slots_a += 1
            peak_a = max(peak_a, slots_a)
            state["pool_A_trailing"]["open_positions"][ticker] = {
                "entry_price": entry_price,
                "entry_time": entry_time,
                "shares": shares_a,
                "sweep_reclaim": sweep_tag,
            }
            state["pool_A_trailing"]["slots_open"] = slots_a
            state["pool_A_trailing"]["slots_peak"] = peak_a
            state["pool_A_trailing"]["value_usd"] = round(pool_a, 4)
            save_state(state)
            print(
                f"  A  OPEN  slots {slots_a}/{MAX_SLOTS}  "
                f"size={shares_a} sh  pool=${pool_a:.2f}  "
                f"risk=${pool_a * RISK_FRAC:.2f}"
            )
            res_a = run_with_pool_sizing(
                run_strategy_a,
                pool_value=pool_a,
                ticker=ticker,
                flag_date=flag_date,
                entry_price=entry_price,
                entry_time=entry_time,
                minute_bars=minute_bars,
                daily_bars=daily_bars,
                profile=profile,
            )
            state["pool_A_trailing"]["open_positions"].pop(ticker, None)
            if res_a.get("status") != "ok":
                a_rec["skip_reason"] = (
                    res_a.get("skip_reason") or str(res_a.get("status"))
                )
                print(f"  A  SKIP run status={res_a.get('status')}")
                slots_a = max(0, slots_a - 1)
            else:
                pnl_a = float(res_a.get("total_pnl_usd") or 0.0)
                pool_a += pnl_a
                slots_a = max(0, slots_a - 1)
                a_rec.update({
                    "taken": True,
                    "shares": int(res_a.get("n_shares") or shares_a),
                    "pnl_usd": round(pnl_a, 4),
                    "return_pct": res_a.get("total_return_pct"),
                    "days_held": res_a.get("days_held"),
                    "exit_reason_counts": res_a.get("exit_reason_counts"),
                    "mfe_pct": res_a.get("mfe_pct"),
                    "pool_after_usd": round(pool_a, 4),
                    "tranches": tranche_rows(res_a),
                })
                pa = state["pool_A_trailing"]
                pa["value_usd"] = round(pool_a, 4)
                pa["slots_open"] = slots_a
                pa["trades_taken"] = int(pa.get("trades_taken") or 0) + 1
                if pnl_a > 0:
                    pa["wins"] = int(pa.get("wins") or 0) + 1
                _win_rate(pa)
                pa["closed_trades"].append(a_rec)
                _equity_append(pa, pool_a, "close", ticker)
                print(
                    f"  A  CLOSE ret={res_a.get('total_return_pct'):+.2f}%  "
                    f"pnl=${pnl_a:+.2f}  pool->${pool_a:.2f}  "
                    f"slots {slots_a}/{MAX_SLOTS}  "
                    f"exits={res_a.get('exit_reason_counts')}"
                )
            state["pool_A_trailing"]["slots_open"] = slots_a
            state["pool_A_trailing"]["value_usd"] = round(pool_a, 4)
            save_state(state)
        trades_a.append(a_rec)

        # ---- Pool B ----
        b_rec: dict[str, Any] = {
            "pool": "B_target",
            "ticker": ticker,
            "taken": False,
            "sweep_reclaim": sweep_tag,
            "entry_model": ENTRY_MODEL,
            "entry_price": entry_price,
            "entry_time": entry_time,
        }
        shares_b = size_for_pool(pool_b, entry_price, kill_pct)
        if slots_b >= MAX_SLOTS:
            b_rec["skip_reason"] = "slots_full"
            print(f"  B  SKIP slots full ({slots_b}/{MAX_SLOTS})")
        elif shares_b < 1:
            b_rec["skip_reason"] = "size_lt_1"
            print(f"  B  SKIP size < 1 (pool=${pool_b:.2f})")
        else:
            slots_b += 1
            peak_b = max(peak_b, slots_b)
            state["pool_B_target"]["open_positions"][ticker] = {
                "entry_price": entry_price,
                "entry_time": entry_time,
                "shares": shares_b,
                "sweep_reclaim": sweep_tag,
            }
            state["pool_B_target"]["slots_open"] = slots_b
            state["pool_B_target"]["slots_peak"] = peak_b
            state["pool_B_target"]["value_usd"] = round(pool_b, 4)
            save_state(state)
            print(
                f"  B  OPEN  slots {slots_b}/{MAX_SLOTS}  "
                f"size={shares_b} sh  pool=${pool_b:.2f}  "
                f"risk=${pool_b * RISK_FRAC:.2f}"
            )
            res_b = run_with_pool_sizing(
                run_strategy_b,
                pool_value=pool_b,
                ticker=ticker,
                flag_date=flag_date,
                entry_price=entry_price,
                entry_time=entry_time,
                minute_bars=minute_bars,
                daily_bars=daily_bars,
                profile=profile,
            )
            state["pool_B_target"]["open_positions"].pop(ticker, None)
            if res_b.get("status") != "ok":
                b_rec["skip_reason"] = (
                    res_b.get("skip_reason") or str(res_b.get("status"))
                )
                print(f"  B  SKIP run status={res_b.get('status')}")
                slots_b = max(0, slots_b - 1)
            else:
                pnl_b = float(res_b.get("total_pnl_usd") or 0.0)
                pool_b += pnl_b
                slots_b = max(0, slots_b - 1)
                b_rec.update({
                    "taken": True,
                    "shares": int(res_b.get("n_shares") or shares_b),
                    "pnl_usd": round(pnl_b, 4),
                    "return_pct": res_b.get("total_return_pct"),
                    "days_held": res_b.get("days_held"),
                    "exit_reason_counts": res_b.get("exit_reason_counts"),
                    "mfe_pct": res_b.get("mfe_pct"),
                    "pool_after_usd": round(pool_b, 4),
                    "tranches": tranche_rows(res_b),
                })
                pb = state["pool_B_target"]
                pb["value_usd"] = round(pool_b, 4)
                pb["slots_open"] = slots_b
                pb["trades_taken"] = int(pb.get("trades_taken") or 0) + 1
                if pnl_b > 0:
                    pb["wins"] = int(pb.get("wins") or 0) + 1
                _win_rate(pb)
                pb["closed_trades"].append(b_rec)
                _equity_append(pb, pool_b, "close", ticker)
                print(
                    f"  B  CLOSE ret={res_b.get('total_return_pct'):+.2f}%  "
                    f"pnl=${pnl_b:+.2f}  pool->${pool_b:.2f}  "
                    f"slots {slots_b}/{MAX_SLOTS}  "
                    f"exits={res_b.get('exit_reason_counts')}"
                )
            state["pool_B_target"]["slots_open"] = slots_b
            state["pool_B_target"]["value_usd"] = round(pool_b, 4)
            save_state(state)
        trades_b.append(b_rec)

        print(
            f"  pool values → A ${pool_a:.2f}  |  B ${pool_b:.2f}  "
            f"| sweep_reclaim={sweep_tag}"
        )

        per_ticker.append({
            "ticker": ticker,
            "flag_date": flag_date,
            "skipped": False,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "entry_model": ENTRY_MODEL,
            "sweep_reclaim": sweep_tag,
            "kill_pct": round(kill_pct, 6),
            "confidence": confidence,
            "levels_source": levels.get("source"),
            "candidate_source": c.get("source"),
            "A": a_rec,
            "B": b_rec,
        })
        save_state(state)

    report = {
        "generated_at": _now_iso(),
        "mode": state["mode"],
        "flag_date": flag_date,
        "entry_model": ENTRY_MODEL,
        "start_pool_usd": START_POOL_USD,
        "max_slots": MAX_SLOTS,
        "n_setups": len(candidates),
        "tickers": [c["ticker"] for c in candidates],
        "per_ticker": per_ticker,
        "pool_A_trailing": {
            **pool_stats(trades_a, START_POOL_USD, pool_a, peak_a),
            "trades": trades_a,
        },
        "pool_B_target": {
            **pool_stats(trades_b, START_POOL_USD, pool_b, peak_b),
            "trades": trades_b,
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


# ---------------------------------------------------------------------------
# run_day — public API (replay past day OR drive live today)
# ---------------------------------------------------------------------------

def run_day(
    day: str | None = None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Full scan → profile → premarket → immediate entry → dual-pool fork.

    Pass a past YYYY-MM-DD for replay/dry-run (uses scan archive + cached
    profiles/bars). Omit day (or pass today) for live Polygon-paper mode.
    """
    flag_date = str(day or _today_et())[:10]
    session_day = datetime.strptime(flag_date, "%Y-%m-%d").date()

    # Infer mode: past date → replay; today → live (unless overridden).
    today = datetime.now(ET).date()
    if mode is None:
        mode = MODE_REPLAY if session_day != today else MODE_LIVE
    mode = str(mode).lower().strip()
    if mode not in (MODE_LIVE, MODE_REPLAY):
        raise ValueError(f"mode must be '{MODE_LIVE}' or '{MODE_REPLAY}'")

    print("=" * 78)
    print(f"Q-ALPHA LIVE FORWARD  |  {flag_date}  |  mode={mode}")
    print("Polygon-paper  |  NO IBKR  |  NO auto-scheduler")
    print(
        f"Entry={ENTRY_MODEL}  |  exits=A Trailing + B Target  |  "
        f"${START_POOL_USD:.0f} × 2 pools  |  max {MAX_SLOTS} slots each"
    )
    print("=" * 78)

    # Weekend / holiday guard (ET calendar).
    if not is_trading_day(session_day):
        msg = "Market closed today"
        print(f"[live_forward] {msg} ({flag_date})")
        state = empty_state(flag_date, mode)
        state["status"] = "market_closed"
        state["phase"] = "stopped"
        state["message"] = msg
        save_state(state)
        return {
            "status": "market_closed",
            "flag_date": flag_date,
            "mode": mode,
            "message": msg,
        }

    state = empty_state(flag_date, mode)
    try:
        from lab_state_sync import reset_throttle
        reset_throttle()
    except Exception:
        pass
    save_state(state, force_sync=True)

    # --- 1) Scan ---
    set_phase(state, "scan", "running full-market gap scan")
    candidates = run_scan_phase(flag_date, mode)
    state["candidates"] = candidates
    state["n_candidates"] = len(candidates)
    state["scan"] = {
        "n_candidates": len(candidates),
        "tickers": [c["ticker"] for c in candidates],
        "sources": sorted({str(c.get("source")) for c in candidates}),
    }
    save_state(state)
    print(f"[live_forward] {len(candidates)} candidates ready")

    if not candidates:
        set_phase(state, "eod", "no candidates — nothing to trade")
        state["status"] = "no_candidates"
        state["eod_summary"] = {"n_candidates": 0, "message": "no candidates"}
        save_state(state)
        print("[live_forward] No candidates — stopping.")
        return {
            "status": "no_candidates",
            "flag_date": flag_date,
            "mode": mode,
            "n_candidates": 0,
        }

    # --- 2) Profile ---
    set_phase(state, "profile", f"profiling {len(candidates)} candidates")
    run_profile_phase(candidates, flag_date)
    save_state(state)

    # --- 3) Premarket ---
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

    # --- 4) Enter + fork dual pools ---
    set_phase(
        state,
        "trading",
        f"entering {len(candidates)} names @ immediate; forking A+B",
    )
    print(
        f"[live_forward] entering {len(candidates)} candidates  |  "
        f"pool A=${state['pool_A_trailing']['value_usd']:.2f}  "
        f"pool B=${state['pool_B_target']['value_usd']:.2f}"
    )
    report = execute_dual_pools(candidates, flag_date, state)
    print_summary(report)

    # --- 5) EOD ---
    set_phase(state, "eod", "writing end-of-day summary")
    state["status"] = "complete"
    state["eod_summary"] = {
        "n_candidates": len(candidates),
        "tickers": report["tickers"],
        "winner": report["winner"],
        "pool_A": {
            "end_usd": report["pool_A_trailing"]["end_usd"],
            "return_pct": report["pool_A_trailing"]["return_pct"],
            "trades_taken": report["pool_A_trailing"]["trades_taken"],
            "win_rate_pct": report["pool_A_trailing"]["win_rate_pct"],
        },
        "pool_B": {
            "end_usd": report["pool_B_target"]["end_usd"],
            "return_pct": report["pool_B_target"]["return_pct"],
            "trades_taken": report["pool_B_target"]["trades_taken"],
            "win_rate_pct": report["pool_B_target"]["win_rate_pct"],
        },
        "sweep_reclaim_tags": {
            row["ticker"]: row.get("sweep_reclaim")
            for row in report["per_ticker"]
            if not row.get("skipped")
        },
    }
    state["report"] = {
        "per_ticker": report["per_ticker"],
        "winner": report["winner"],
        "pool_A_trailing": {
            k: report["pool_A_trailing"][k]
            for k in (
                "start_usd", "end_usd", "realized_pnl_usd", "return_pct",
                "trades_taken", "win_rate_pct", "slots_used_peak",
            )
        },
        "pool_B_target": {
            k: report["pool_B_target"][k]
            for k in (
                "start_usd", "end_usd", "realized_pnl_usd", "return_pct",
                "trades_taken", "win_rate_pct", "slots_used_peak",
            )
        },
    }
    save_state(state)

    eod_path = EOD_DIR / f"forward_{flag_date}.json"
    eod_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n[live_forward] Wrote {STATE_PATH.relative_to(ROOT)}")
    print(f"[live_forward] Wrote {eod_path.relative_to(ROOT)}")
    return report


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
    args = parser.parse_args()

    if args.replay:
        run_day(args.replay, mode=MODE_REPLAY)
    elif args.date:
        run_day(args.date)
    else:
        # Manual Monday start — live today. No sleep/scheduler loop.
        run_day(_today_et(), mode=MODE_LIVE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
