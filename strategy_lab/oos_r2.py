"""
strategy_lab/oos_r2.py — out-of-sample R² of profiler MFE p50 vs realized MFE.

Does the ticker profiler's predicted MFE (percentiles.mfe.p50) match ACTUAL
max favorable excursion on setups it did not "see" in the temporal sense?

  predicted = profile MFE p50 (fraction → %)
  actual    = peak high vs entry over the Strategy A hold window
              (flag-day 1-min bars after entry + subsequent daily bars,
               up to MAX_HOLD_TRADING_DAYS) — not truncated by stops.

Temporal split: sort by flag_date; older fraction = in-sample, newer = OOS.
Default 70/30 (parameterized). R² is NOT clamped — OOS R² may be negative.

Does NOT modify agent files.

Usage (from repo root):
  venv\\Scripts\\python.exe strategy_lab\\oos_r2.py
  venv\\Scripts\\python.exe strategy_lab\\oos_r2.py --train-frac 0.7
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))

from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    MAX_HOLD_TRADING_DAYS,
    load_minute_bars,
)

SETUPS_PATH = LAB / "results" / "setups.json"
PROFILES_DIR = LAB / "profiles"
DAILY_CACHE_DIR = LAB / "results" / "daily_cache"
OUT_JSON = LAB / "results" / "oos_r2_backtest.json"

DEFAULT_TRAIN_FRAC = 0.70


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_unique_setups() -> list[dict[str, str]]:
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for s in doc.get("setups") or []:
        t = str(s.get("ticker") or "").upper().strip()
        d = str(s.get("flag_date") or "")[:10]
        if not t or not d or (t, d) in seen:
            continue
        seen.add((t, d))
        out.append({"ticker": t, "flag_date": d})
    out.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    return out


def load_history() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {}
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8")).get("history") or {}


def profile_path(ticker: str, flag_date: str) -> Path:
    return PROFILES_DIR / f"{ticker}_{flag_date}.json"


def bars_path(ticker: str, flag_date: str, hist_row: dict | None) -> Path | None:
    if hist_row:
        rel = hist_row.get("minute_bars_path") or hist_row.get("bars_path")
        if rel:
            p = Path(str(rel))
            p = p if p.is_absolute() else ROOT / p
            if p.exists():
                return p
    p = BARS_DIR / f"{ticker}_{flag_date}.json"
    return p if p.exists() else None


def load_daily(ticker: str, flag_date: str) -> list[dict]:
    cache = DAILY_CACHE_DIR / f"{ticker}_{flag_date}.json"
    if not cache.exists():
        return []
    return list(json.loads(cache.read_text(encoding="utf-8")).get("bars") or [])


def predicted_mfe_pct(profile: dict[str, Any]) -> float | None:
    """Profile MFE p50 as percent. None if missing / insufficient."""
    conf = str(profile.get("confidence") or "").upper()
    if conf == "INSUFFICIENT" and not (
        (profile.get("percentiles") or {}).get("mfe") or {}
    ).get("p50"):
        return None
    mfe = (profile.get("percentiles") or {}).get("mfe") or {}
    p50 = mfe.get("p50")
    if p50 is None:
        return None
    try:
        v = float(p50)
    except (TypeError, ValueError):
        return None
    # Profiler stores fractions (0.05 = 5%). Guard if someone stored % already.
    if abs(v) > 1.5:
        return v  # already looks like percent points
    return v * 100.0


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def actual_mfe_pct(
    *,
    entry_price: float,
    entry_time: str,
    flag_date: str,
    minute_bars: list[dict],
    daily_bars: list[dict],
    max_hold_days: int = MAX_HOLD_TRADING_DAYS,
) -> float | None:
    """
    Realized MFE % = (peak_high - entry) / entry * 100 over the hold window.

    Flag-day: 1-min highs at/after entry. Then subsequent daily highs for up to
    max_hold_days trading days total (same cap as Strategy A). Not truncated
    by kill/trail — this is the excursion the profiler is trying to forecast.
    """
    if entry_price <= 0:
        return None
    peak = float(entry_price)
    entry_ts = _parse_iso(entry_time)
    saw = False

    for b in minute_bars:
        when = str(b.get("t_et") or b.get("t") or "")
        bar_ts = _parse_iso(when) if when else None
        if entry_ts is not None and bar_ts is not None and bar_ts < entry_ts:
            continue
        try:
            h = float(b["h"])
        except (KeyError, TypeError, ValueError):
            continue
        peak = max(peak, h)
        saw = True

    post = [d for d in daily_bars if str(d.get("date") or "")[:10] > flag_date]
    post.sort(key=lambda d: str(d.get("date"))[:10])
    # Day 1 = flag day (minute bars). Remaining hold days from daily.
    remaining = max(0, max_hold_days - 1)
    for dbar in post[:remaining]:
        try:
            h = float(dbar["high"] if "high" in dbar else dbar["h"])
        except (KeyError, TypeError, ValueError):
            continue
        peak = max(peak, h)
        saw = True

    if not saw:
        return None
    return (peak - entry_price) / entry_price * 100.0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def r_squared(y_true: list[float], y_pred: list[float]) -> float | None:
    """
    R² = 1 - SS_res/SS_tot. SS_tot uses mean of ACTUALS.
    Can be negative. None if N < 2 or SS_tot == 0.
    """
    n = len(y_true)
    if n < 2 or n != len(y_pred):
        return None
    y_bar = sum(y_true) / n
    ss_tot = sum((y - y_bar) ** 2 for y in y_true)
    if ss_tot <= 1e-18:
        return None
    ss_res = sum((y - p) ** 2 for y, p in zip(y_true, y_pred))
    return 1.0 - (ss_res / ss_tot)


def pearson(y_true: list[float], y_pred: list[float]) -> float | None:
    n = len(y_true)
    if n < 2 or n != len(y_pred):
        return None
    mx = sum(y_true) / n
    my = sum(y_pred) / n
    num = sum((x - mx) * (y - my) for x, y in zip(y_true, y_pred))
    dx = math.sqrt(sum((x - mx) ** 2 for x in y_true))
    dy = math.sqrt(sum((y - my) ** 2 for y in y_pred))
    if dx <= 1e-18 or dy <= 1e-18:
        return None
    return num / (dx * dy)


def rmse(y_true: list[float], y_pred: list[float]) -> float | None:
    n = len(y_true)
    if n < 1 or n != len(y_pred):
        return None
    return math.sqrt(sum((y - p) ** 2 for y, p in zip(y_true, y_pred)) / n)


def summarize_split(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    actuals = [float(r["actual_mfe_pct"]) for r in rows]
    preds = [float(r["predicted_mfe_pct"]) for r in rows]
    n = len(rows)
    return {
        "label": label,
        "n": n,
        "r2": (
            round(r_squared(actuals, preds), 6)
            if r_squared(actuals, preds) is not None
            else None
        ),
        "correlation": (
            round(pearson(actuals, preds), 6)
            if pearson(actuals, preds) is not None
            else None
        ),
        "rmse_pct": (
            round(rmse(actuals, preds), 4)
            if rmse(actuals, preds) is not None
            else None
        ),
        "mean_actual_mfe_pct": round(sum(actuals) / n, 4) if n else None,
        "mean_predicted_mfe_pct": round(sum(preds) / n, 4) if n else None,
        "flag_dates": sorted({r["flag_date"] for r in rows}),
        "n_unique_dates": len({r["flag_date"] for r in rows}),
    }


def temporal_split(
    rows: list[dict[str, Any]],
    train_frac: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sort by date already assumed; older train_frac → IS, rest → OOS."""
    if not rows:
        return [], []
    frac = min(0.95, max(0.05, float(train_frac)))
    n_train = int(math.floor(len(rows) * frac))
    # Ensure both sides get at least 1 when possible.
    if len(rows) >= 2:
        n_train = max(1, min(len(rows) - 1, n_train))
    return rows[:n_train], rows[n_train:]


# ---------------------------------------------------------------------------
# Build pairs
# ---------------------------------------------------------------------------

def build_pairs() -> tuple[list[dict[str, Any]], dict[str, int]]:
    setups = load_unique_setups()
    history = load_history()
    pairs: list[dict[str, Any]] = []
    skip = Counter()

    for s in setups:
        ticker, flag_date = s["ticker"], s["flag_date"]
        key = f"{ticker}|{flag_date}"
        hist = history.get(key)
        if not hist or hist.get("status") != "ok":
            skip["no_history"] += 1
            continue
        pp = profile_path(ticker, flag_date)
        if not pp.exists():
            skip["no_profile"] += 1
            continue
        profile = json.loads(pp.read_text(encoding="utf-8"))
        pred = predicted_mfe_pct(profile)
        if pred is None:
            skip["no_mfe_p50"] += 1
            continue
        bp = bars_path(ticker, flag_date, hist)
        if bp is None:
            skip["no_bars"] += 1
            continue
        minute_bars = load_minute_bars(bp)
        if not minute_bars:
            skip["empty_bars"] += 1
            continue
        try:
            entry_price = float(hist["entry_price"])
        except (KeyError, TypeError, ValueError):
            skip["bad_entry"] += 1
            continue
        entry_time = str(hist.get("entry_time") or "")
        daily = load_daily(ticker, flag_date)
        actual = actual_mfe_pct(
            entry_price=entry_price,
            entry_time=entry_time,
            flag_date=flag_date,
            minute_bars=minute_bars,
            daily_bars=daily,
        )
        if actual is None:
            skip["no_actual_mfe"] += 1
            continue

        pairs.append({
            "ticker": ticker,
            "flag_date": flag_date,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "predicted_mfe_pct": round(pred, 4),
            "actual_mfe_pct": round(actual, 4),
            "confidence": profile.get("confidence"),
            "error_pct": round(pred - actual, 4),
        })

    pairs.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    return pairs, dict(skip)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(x: float | None, digits: int = 4) -> str:
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def print_report(report: dict[str, Any]) -> None:
    is_ = report["in_sample"]
    oos = report["out_of_sample"]
    gap = report.get("r2_gap_is_minus_oos")

    print()
    print("=" * 78)
    print("PROFILER OOS R² — predicted MFE p50 vs actual MFE")
    print("=" * 78)
    print(
        f"  Usable pairs: {report['n_usable']}  "
        f"(skipped: {report['n_skipped']})  "
        f"train_frac={report['train_frac']:.0%}"
    )
    print(
        f"  Hold window: {report['max_hold_trading_days']} trading days "
        f"(flag-day 1-min + later daily highs)"
    )
    print(
        f"  Date span: {report['date_span']['first']} → "
        f"{report['date_span']['last']}  "
        f"({report['date_span']['n_unique_dates']} unique flag dates)"
    )

    print()
    print("WHAT R² MEANS (plain English)")
    print("-" * 78)
    print(
        "  R² asks: how much of the variation in ACTUAL MFE does the profiler's"
    )
    print(
        "  predicted MFE p50 explain?  1.0 = perfect.  0.0 = no better than"
    )
    print(
        "  always guessing the average actual MFE.  Negative = WORSE than that"
    )
    print(
        "  average guess (the predictions add noise)."
    )
    print(
        "  Negative OOS R² means the profiler predicts worse than just guessing"
    )
    print(
        "  the average MFE."
    )

    print()
    print("IN-SAMPLE (older setups — temporal train slice)")
    print("-" * 78)
    print(
        f"  N={is_['n']}  dates={is_['n_unique_dates']}  "
        f"R²={_fmt(is_['r2'])}  corr={_fmt(is_['correlation'])}  "
        f"RMSE={_fmt(is_['rmse_pct'], 2)}%"
    )
    print(
        f"  mean predicted MFE={_fmt(is_['mean_predicted_mfe_pct'], 2)}%  "
        f"mean actual MFE={_fmt(is_['mean_actual_mfe_pct'], 2)}%"
    )

    print()
    print("OUT-OF-SAMPLE (newer setups — held-out temporal test slice)")
    print("-" * 78)
    print(
        f"  N={oos['n']}  dates={oos['n_unique_dates']}  "
        f"R²={_fmt(oos['r2'])}  corr={_fmt(oos['correlation'])}  "
        f"RMSE={_fmt(oos['rmse_pct'], 2)}%"
    )
    print(
        f"  mean predicted MFE={_fmt(oos['mean_predicted_mfe_pct'], 2)}%  "
        f"mean actual MFE={_fmt(oos['mean_actual_mfe_pct'], 2)}%"
    )

    print()
    print("IN-SAMPLE vs OUT-OF-SAMPLE GAP")
    print("-" * 78)
    print(f"  R²_IS − R²_OOS = {_fmt(gap)}")
    print(f"  {report['gap_interpretation']}")

    print()
    print("SMALL-SAMPLE HEALTH WARNING")
    print("-" * 78)
    for line in report["health_warning_lines"]:
        print(f"  {line}")

    print()
    print("SKIP REASONS")
    print("-" * 78)
    for k, v in sorted((report.get("skip_reasons") or {}).items()):
        print(f"  {k:<20} {v}")
    print("=" * 78)


def run(train_frac: float = DEFAULT_TRAIN_FRAC) -> dict[str, Any]:
    pairs, skip = build_pairs()
    is_rows, oos_rows = temporal_split(pairs, train_frac)
    is_stats = summarize_split(is_rows, label="in_sample")
    oos_stats = summarize_split(oos_rows, label="out_of_sample")

    r2_is = is_stats["r2"]
    r2_oos = oos_stats["r2"]
    gap = None
    if r2_is is not None and r2_oos is not None:
        gap = round(r2_is - r2_oos, 6)

    if gap is None:
        gap_interp = "Cannot interpret gap — need R² on both splits (N≥2 each)."
    elif gap > 0.25:
        gap_interp = (
            "BIG DROP from IS → OOS: classic overfitting signal (Narang warning) "
            "— the profiler looks better on older data than on newer held-out days."
        )
    elif gap > 0.10:
        gap_interp = (
            "Moderate IS→OOS drop: some optimism on the older slice; treat OOS "
            "as the honest number."
        )
    elif abs(gap) <= 0.10:
        gap_interp = (
            "IS and OOS R² are similar: the (weak or strong) relationship "
            "generalizes across the temporal split — less evidence of "
            "date-specific overfitting."
        )
    else:
        gap_interp = (
            "OOS R² is HIGHER than IS (gap negative): unusual; often noise "
            "on small N — do not celebrate without more days."
        )

    all_dates = sorted({r["flag_date"] for r in pairs})
    date_counts = Counter(r["flag_date"] for r in pairs)
    top_cluster = date_counts.most_common(1)
    cluster_date, cluster_n = top_cluster[0] if top_cluster else ("—", 0)

    health = [
        f"N_usable={len(pairs)} setups across only {len(all_dates)} unique "
        f"flag dates (full setups.json has ~21 unique days historically).",
        f"Heavy clustering: {cluster_date} alone has {cluster_n}/{len(pairs)} "
        f"usable pairs — one day can dominate either split.",
        "This backtest R² is a NOISY SANITY FLOOR, not a trustworthy estimate "
        "of live predictive power.",
        "The real test is a FORWARD ROLLING R²: each new trading day, score "
        "yesterday's predictions vs realized MFE and accumulate — that is the "
        "number that earns trust.",
    ]
    if r2_oos is not None and r2_oos < 0:
        health.append(
            f"OOS R²={r2_oos:.4f} is negative: on held-out dates the profiler "
            "is worse than predicting the average actual MFE."
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "metric": "R2(predicted_mfe_p50, actual_mfe)",
        "predicted": "profile.percentiles.mfe.p50 → percent",
        "actual": (
            "peak high vs entry over flag-day 1-min (post-entry) + "
            f"up to {MAX_HOLD_TRADING_DAYS - 1} subsequent daily highs "
            f"(MAX_HOLD_TRADING_DAYS={MAX_HOLD_TRADING_DAYS})"
        ),
        "train_frac": train_frac,
        "max_hold_trading_days": MAX_HOLD_TRADING_DAYS,
        "n_usable": len(pairs),
        "n_skipped": sum(skip.values()),
        "skip_reasons": skip,
        "date_span": {
            "first": all_dates[0] if all_dates else None,
            "last": all_dates[-1] if all_dates else None,
            "n_unique_dates": len(all_dates),
            "per_date_counts": dict(sorted(date_counts.items())),
        },
        "in_sample": is_stats,
        "out_of_sample": oos_stats,
        "r2_gap_is_minus_oos": gap,
        "gap_interpretation": gap_interp,
        "health_warning_lines": health,
        "plain_english": {
            "r2": (
                "Share of actual-MFE variance explained by predicted MFE p50. "
                "1=perfect, 0=no better than mean, <0=worse than mean."
            ),
            "negative_oos_r2": (
                "Negative OOS R² means the profiler predicts worse than just "
                "guessing the average MFE."
            ),
            "gap": gap_interp,
        },
        "pairs": pairs,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profiler MFE p50 out-of-sample R² backtest",
    )
    parser.add_argument(
        "--train-frac",
        type=float,
        default=DEFAULT_TRAIN_FRAC,
        help="Fraction of chronologically sorted setups used as in-sample "
             f"(default {DEFAULT_TRAIN_FRAC})",
    )
    args = parser.parse_args()

    print(
        f"Building predicted vs actual MFE pairs "
        f"(train_frac={args.train_frac})..."
    )
    report = run(train_frac=args.train_frac)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"Wrote {OUT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
