"""
strategy_lab/entry_edge_study.py — univariate ENTRY FILTER edge study.

Fixed exit = Strategy A (Trailing) so we isolate ENTRY-condition effects.
Does NOT search filter combinations (overfitting risk). Does NOT modify agent files.

For each setup (immediate entry × Strategy A outcome from matrix.json):
  compute features at/near the open, then measure PASS vs FAIL for each
  single filter: avg return, win rate, kill rate, lift, and a
  2026-08-20-cluster vs rest robustness split.

Writes results/entry_edge_study.json and prints a ranked lift table.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "candidates"))

from entry_models import (  # noqa: E402
    PREMARKET_START_MIN,
    RTH_OPEN_MIN,
    _mod,
    _ohlcv,
    rth_start_index,
    run_all_models,
)
from entry_study import Bar, running_vwap  # noqa: E402

SETUPS_PATH = LAB / "results" / "setups.json"
MATRIX_JSON = LAB / "results" / "matrix.json"
BARS_DIR = LAB / "results" / "bars"
PROFILES_DIR = LAB / "profiles"
OUT_JSON = LAB / "results" / "entry_edge_study.json"

CLUSTER_DATE = "2026-08-20"
MIN_N = 15  # below this → "insufficient / likely noise"
DEFAULT_VOL_MULT = 1.75
RSI_PERIOD = 14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_unique_setups() -> list[dict[str, Any]]:
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for s in doc.get("setups") or []:
        t = str(s.get("ticker") or "").upper().strip()
        d = str(s.get("flag_date") or "")[:10]
        if not t or not d or (t, d) in seen:
            continue
        seen.add((t, d))
        out.append({
            "ticker": t,
            "flag_date": d,
            "gap_pct": s.get("gap_pct"),
            "vol_ratio": s.get("vol_ratio"),
            "regime": s.get("regime"),
            "score": s.get("score"),
        })
    out.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    return out


def load_matrix_immediate_a() -> dict[tuple[str, str], dict[str, Any]]:
    """Immediate-entry × Strategy A outcomes from matrix.json (fixed exit)."""
    doc = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in doc.get("per_setup") or []:
        t = str(row["ticker"]).upper()
        d = str(row["flag_date"])[:10]
        cell = (row.get("by_entry") or {}).get("immediate") or {}
        a = cell.get("A_trailing") or {}
        if cell.get("status") != "entered" or a.get("status") != "ok":
            continue
        if a.get("return_pct") is None:
            continue
        out[(t, d)] = {
            "return_pct": float(a["return_pct"]),
            "killed": bool(a.get("killed")),
            "days_held": a.get("days_held"),
            "mfe_pct": a.get("mfe_pct"),
            "entry_price": cell.get("entry_price"),
            "entry_time": cell.get("entry_time"),
            "confidence": row.get("confidence"),
        }
    return out


def load_bars(ticker: str, flag_date: str) -> list[dict]:
    path = BARS_DIR / f"{ticker}_{flag_date}.json"
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("bars") or [])


def load_profile_vol_mult(ticker: str, flag_date: str) -> float:
    path = PROFILES_DIR / f"{ticker}_{flag_date}.json"
    if not path.exists():
        return DEFAULT_VOL_MULT
    p = json.loads(path.read_text(encoding="utf-8"))
    stats = (p.get("analog_finder") or {}).get("stats") or {}
    try:
        return float(stats.get("vol_mult") or DEFAULT_VOL_MULT)
    except (TypeError, ValueError):
        return DEFAULT_VOL_MULT


def rsi_wilder(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder RSI at the last close. None if insufficient history."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def gap_bucket(gap_pct: float | None) -> str:
    if gap_pct is None:
        return "unknown"
    g = float(gap_pct)
    # setups store gap as fraction (0.10 = 10%)
    if g < 0.10:
        return "<10%"
    if g < 0.25:
        return "10-25%"
    return ">25%"


def rsi_bucket(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi < 30:
        return "<30"
    if rsi <= 70:
        return "30-70"
    return ">70"


# ---------------------------------------------------------------------------
# Feature computation (at/near immediate open)
# ---------------------------------------------------------------------------

def compute_features(
    bars: list[dict],
    *,
    gap_pct: float | None,
    vol_ratio: float | None,
    regime: Any,
    vol_mult: float,
) -> dict[str, Any]:
    """
    Features at/near the immediate (09:30) entry bar.

    Volume surge: first 5 RTH minutes vs morning-PM avg 1-min vol × 5,
    threshold = profile vol_mult (fallback 1.75). Falls back to setups
    vol_ratio when PM baseline is unavailable.
    """
    i0 = rth_start_index(bars)
    if i0 is None:
        return {"ok": False, "reason": "no_rth"}

    # --- opening 5-min volume surge ---
    open5 = bars[i0 : i0 + 5]
    open5_vol = sum(_ohlcv(b)[4] for b in open5)
    pm_vols = [
        _ohlcv(b)[4]
        for b in bars[:i0]
        if PREMARKET_START_MIN <= _mod(b) < RTH_OPEN_MIN
    ]
    volume_surge: bool | None
    open5_vs_baseline: float | None
    if pm_vols and sum(pm_vols) > 0:
        pm_avg = sum(pm_vols) / len(pm_vols)
        baseline_5 = pm_avg * 5.0
        open5_vs_baseline = (open5_vol / baseline_5) if baseline_5 > 0 else None
        volume_surge = (
            open5_vs_baseline is not None and open5_vs_baseline >= vol_mult
        )
        vol_source = "pm_baseline"
    elif vol_ratio is not None:
        open5_vs_baseline = float(vol_ratio)
        volume_surge = float(vol_ratio) >= vol_mult
        vol_source = "setups_vol_ratio_fallback"
    else:
        open5_vs_baseline = None
        volume_surge = None
        vol_source = "unavailable"

    # --- VWAP (session from available bars through open, incl. PM) ---
    # Include PM so "above VWAP at open" is meaningful (RTH-only VWAP ≈ open).
    through_open = bars[: i0 + 1]
    study = []
    for b in through_open:
        o, h, l, c, v = _ohlcv(b)
        study.append(Bar({"t": int(b["t"]), "o": o, "h": h, "l": l, "c": c, "v": v}))
    vwaps = running_vwap(study) if study else []
    entry_close = _ohlcv(bars[i0])[3]
    vwap_at_entry = vwaps[-1] if vwaps else None
    above_vwap = (
        entry_close > vwap_at_entry if vwap_at_entry is not None else None
    )

    # --- RSI on closes through open (PM history feeds the window) ---
    closes = [_ohlcv(b)[3] for b in through_open]
    rsi = rsi_wilder(closes, RSI_PERIOD)

    # --- sweep_reclaim quality flag (pattern occurred y/n) ---
    signals = run_all_models(bars)
    sweep_passed = signals.get("sweep_reclaim") is not None

    return {
        "ok": True,
        "entry_index": i0,
        "entry_close": entry_close,
        "volume_surge": volume_surge,
        "open5_vs_baseline": (
            round(open5_vs_baseline, 4) if open5_vs_baseline is not None else None
        ),
        "vol_mult_threshold": vol_mult,
        "vol_source": vol_source,
        "above_vwap": above_vwap,
        "vwap_at_entry": round(vwap_at_entry, 4) if vwap_at_entry is not None else None,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "rsi_bucket": rsi_bucket(rsi),
        "gap_pct": gap_pct,
        "gap_bucket": gap_bucket(gap_pct),
        "regime": regime if regime not in (None, "None", "") else None,
        "sweep_reclaim_passed": sweep_passed,
    }


# ---------------------------------------------------------------------------
# Univariate analysis
# ---------------------------------------------------------------------------

def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "avg_return_pct": None,
            "win_rate_pct": None,
            "kill_rate_pct": None,
            "insufficient_n": True,
        }
    rets = [float(r["return_pct"]) for r in rows]
    wins = sum(1 for x in rets if x > 0)
    kills = sum(1 for r in rows if r.get("killed"))
    return {
        "n": n,
        "avg_return_pct": round(sum(rets) / n, 4),
        "win_rate_pct": round(100.0 * wins / n, 2),
        "kill_rate_pct": round(100.0 * kills / n, 2),
        "insufficient_n": n < MIN_N,
    }


def analyze_binary_filter(
    rows: list[dict[str, Any]],
    feature_key: str,
    *,
    pass_value: Any = True,
    filter_name: str,
    description: str,
) -> dict[str, Any]:
    """PASS when feature == pass_value; FAIL when feature is the opposite bool/value."""
    usable = [
        r for r in rows
        if r["features"].get(feature_key) not in (None, "unknown")
    ]
    passed = [r for r in usable if r["features"][feature_key] == pass_value]
    failed = [r for r in usable if r["features"][feature_key] != pass_value]

    def half(label: str) -> dict[str, Any]:
        subset = [r for r in usable if r["cluster"] == label]
        p = [r for r in subset if r["features"][feature_key] == pass_value]
        f = [r for r in subset if r["features"][feature_key] != pass_value]
        ps, fs = _group_stats(p), _group_stats(f)
        lift = None
        if ps["avg_return_pct"] is not None and fs["avg_return_pct"] is not None:
            lift = round(ps["avg_return_pct"] - fs["avg_return_pct"], 4)
        return {
            "n_pass": ps["n"],
            "n_fail": fs["n"],
            "pass": ps,
            "fail": fs,
            "lift_pct": lift,
            "both_sides_sufficient": (not ps["insufficient_n"]) and (not fs["insufficient_n"]),
        }

    overall_pass = _group_stats(passed)
    overall_fail = _group_stats(failed)
    lift = None
    if overall_pass["avg_return_pct"] is not None and overall_fail["avg_return_pct"] is not None:
        lift = round(overall_pass["avg_return_pct"] - overall_fail["avg_return_pct"], 4)

    cluster = half("cluster_2026-08-20")
    rest = half("rest")

    # Edge survives if lift sign agrees in both halves and both halves have lift.
    survives = False
    if (
        cluster["lift_pct"] is not None
        and rest["lift_pct"] is not None
        and cluster["both_sides_sufficient"]
        and rest["both_sides_sufficient"]
    ):
        survives = (cluster["lift_pct"] > 0 and rest["lift_pct"] > 0) or (
            cluster["lift_pct"] < 0 and rest["lift_pct"] < 0
        )
        # Only count as "survives positive edge" when overall lift > 0
        if lift is not None and lift > 0:
            survives = cluster["lift_pct"] > 0 and rest["lift_pct"] > 0
        elif lift is not None and lift < 0:
            survives = cluster["lift_pct"] < 0 and rest["lift_pct"] < 0
        else:
            survives = False

    insufficient = (
        overall_pass["insufficient_n"]
        or overall_fail["insufficient_n"]
        or not cluster["both_sides_sufficient"]
        or not rest["both_sides_sufficient"]
    )

    verdict = "insufficient / likely noise"
    if not insufficient and lift is not None:
        if lift > 0 and survives:
            verdict = "edge survives cluster split"
        elif lift > 0 and not survives:
            verdict = "edge ONLY in one half — likely overfitting"
        elif lift <= 0 and survives:
            verdict = "stable non-edge / negative filter"
        else:
            verdict = "unstable / no clear edge"

    return {
        "filter": filter_name,
        "description": description,
        "feature_key": feature_key,
        "pass_value": pass_value,
        "n_usable": len(usable),
        "n_pass": overall_pass["n"],
        "n_fail": overall_fail["n"],
        "pass": overall_pass,
        "fail": overall_fail,
        "lift_pct": lift,
        "robustness": {
            "cluster_2026-08-20": cluster,
            "rest": rest,
            "edge_survives_both_halves": survives,
        },
        "insufficient": insufficient,
        "verdict": verdict,
    }


def analyze_bucket_filter(
    rows: list[dict[str, Any]],
    feature_key: str,
    *,
    filter_name: str,
    description: str,
    bucket_order: list[str],
) -> dict[str, Any]:
    """Multi-bucket univariate report (still no combinations)."""
    usable = [
        r for r in rows
        if r["features"].get(feature_key) not in (None, "unknown")
    ]
    by_bucket: dict[str, Any] = {}
    for label in bucket_order:
        group = [r for r in usable if r["features"][feature_key] == label]
        rest = [r for r in usable if r["features"][feature_key] != label]
        gs = _group_stats(group)
        rs = _group_stats(rest)
        lift = None
        if gs["avg_return_pct"] is not None and rs["avg_return_pct"] is not None:
            lift = round(gs["avg_return_pct"] - rs["avg_return_pct"], 4)

        # robustness: this bucket vs rest in each half
        def half(cluster_label: str) -> dict[str, Any]:
            sub = [r for r in usable if r["cluster"] == cluster_label]
            g = [r for r in sub if r["features"][feature_key] == label]
            o = [r for r in sub if r["features"][feature_key] != label]
            gg, oo = _group_stats(g), _group_stats(o)
            lf = None
            if gg["avg_return_pct"] is not None and oo["avg_return_pct"] is not None:
                lf = round(gg["avg_return_pct"] - oo["avg_return_pct"], 4)
            return {
                "n_bucket": gg["n"],
                "n_rest": oo["n"],
                "bucket": gg,
                "rest": oo,
                "lift_pct": lf,
                "sufficient": (not gg["insufficient_n"]) and (not oo["insufficient_n"]),
            }

        cl = half("cluster_2026-08-20")
        rst = half("rest")
        survives = False
        if (
            lift is not None
            and lift > 0
            and cl["lift_pct"] is not None
            and rst["lift_pct"] is not None
            and cl["sufficient"]
            and rst["sufficient"]
        ):
            survives = cl["lift_pct"] > 0 and rst["lift_pct"] > 0

        insufficient = (
            gs["insufficient_n"]
            or rs["insufficient_n"]
            or not cl["sufficient"]
            or not rst["sufficient"]
        )
        if insufficient:
            verdict = "insufficient / likely noise"
        elif lift is not None and lift > 0 and survives:
            verdict = "edge survives cluster split"
        elif lift is not None and lift > 0:
            verdict = "edge ONLY in one half — likely overfitting"
        else:
            verdict = "unstable / no clear edge"

        by_bucket[label] = {
            "n": gs["n"],
            "stats": gs,
            "vs_rest_lift_pct": lift,
            "robustness": {
                "cluster_2026-08-20": cl,
                "rest": rst,
                "edge_survives_both_halves": survives,
            },
            "insufficient": insufficient,
            "verdict": verdict,
        }

    # Rank buckets by lift for the headline
    ranked = sorted(
        [
            {"bucket": k, "lift_pct": v["vs_rest_lift_pct"], **v}
            for k, v in by_bucket.items()
            if v["vs_rest_lift_pct"] is not None
        ],
        key=lambda x: x["lift_pct"],
        reverse=True,
    )
    return {
        "filter": filter_name,
        "description": description,
        "feature_key": feature_key,
        "type": "multi_bucket",
        "n_usable": len(usable),
        "buckets": by_bucket,
        "ranked_by_lift": [
            {
                "bucket": x["bucket"],
                "n": x["n"],
                "avg_return_pct": x["stats"]["avg_return_pct"],
                "lift_vs_rest_pct": x["lift_pct"],
                "verdict": x["verdict"],
            }
            for x in ranked
        ],
    }


# ---------------------------------------------------------------------------
# Report / print
# ---------------------------------------------------------------------------

def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    mid = len(ys) // 2
    if len(ys) % 2:
        return ys[mid]
    return 0.5 * (ys[mid - 1] + ys[mid])


def build_rows() -> list[dict[str, Any]]:
    setups = load_unique_setups()
    outcomes = load_matrix_immediate_a()
    rows: list[dict[str, Any]] = []

    for s in setups:
        key = (s["ticker"], s["flag_date"])
        outcome = outcomes.get(key)
        if not outcome:
            continue
        bars = load_bars(s["ticker"], s["flag_date"])
        if not bars:
            continue
        vol_mult = load_profile_vol_mult(s["ticker"], s["flag_date"])
        feats = compute_features(
            bars,
            gap_pct=s.get("gap_pct"),
            vol_ratio=s.get("vol_ratio"),
            regime=s.get("regime"),
            vol_mult=vol_mult,
        )
        if not feats.get("ok"):
            continue
        cluster = (
            "cluster_2026-08-20"
            if s["flag_date"] == CLUSTER_DATE
            else "rest"
        )
        rows.append({
            "ticker": s["ticker"],
            "flag_date": s["flag_date"],
            "cluster": cluster,
            "return_pct": outcome["return_pct"],
            "killed": outcome["killed"],
            "confidence": outcome.get("confidence"),
            "features": feats,
        })

    # Profile vol_mult (~1.75×) almost never fails in this catalyst-screened
    # universe. Add a balanced univariate cut: above-median open5 vs PM
    # baseline (or setups vol_ratio when that is the source).
    ratios = [
        float(r["features"]["open5_vs_baseline"])
        for r in rows
        if r["features"].get("open5_vs_baseline") is not None
    ]
    med = _median(ratios)
    for r in rows:
        f = r["features"]
        raw = f.get("open5_vs_baseline")
        f["volume_surge_profile_mult"] = f.get("volume_surge")
        if med is None or raw is None:
            f["volume_surge"] = None
        else:
            f["volume_surge"] = float(raw) >= med
        f["volume_surge_median_threshold"] = (
            round(med, 4) if med is not None else None
        )

    return rows


def run_study(rows: list[dict[str, Any]]) -> dict[str, Any]:
    binary_filters = [
        analyze_binary_filter(
            rows,
            "volume_surge",
            pass_value=True,
            filter_name="volume_surge",
            description=(
                "Opening 5-min vol vs morning-PM avg×5 (or setups vol_ratio) "
                "above sample median — profile vol_mult saturates this universe"
            ),
        ),
        analyze_binary_filter(
            rows,
            "volume_surge_profile_mult",
            pass_value=True,
            filter_name="volume_surge_profile_mult",
            description=(
                f"Same ratio >= profile vol_mult (default {DEFAULT_VOL_MULT}×) "
                "— expected to be imbalanced on catalyst setups"
            ),
        ),
        analyze_binary_filter(
            rows,
            "above_vwap",
            pass_value=True,
            filter_name="above_vwap",
            description="Immediate open close above session VWAP (incl. morning PM)",
        ),
        analyze_binary_filter(
            rows,
            "sweep_reclaim_passed",
            pass_value=True,
            filter_name="sweep_reclaim_passed",
            description="sweep_reclaim entry model would have fired (quality flag)",
        ),
        analyze_binary_filter(
            rows,
            "rsi_bucket",
            pass_value="<30",
            filter_name="rsi_oversold_<30",
            description="RSI(14) < 30 at open (oversold); needs >=15 1-min closes",
        ),
        analyze_binary_filter(
            rows,
            "rsi_bucket",
            pass_value=">70",
            filter_name="rsi_overbought_>70",
            description="RSI(14) > 70 at open (overbought)",
        ),
        analyze_binary_filter(
            rows,
            "rsi_bucket",
            pass_value="30-70",
            filter_name="rsi_mid_30_70",
            description="RSI(14) in 30–70 at open (neither extreme)",
        ),
        analyze_binary_filter(
            rows,
            "gap_bucket",
            pass_value="10-25%",
            filter_name="gap_10_25",
            description="Gap in 10–25% bucket (vs all other known gaps)",
        ),
        analyze_binary_filter(
            rows,
            "gap_bucket",
            pass_value=">25%",
            filter_name="gap_gt_25",
            description="Gap >25% (vs all other known gaps)",
        ),
    ]

    bucket_filters = [
        analyze_bucket_filter(
            rows,
            "gap_bucket",
            filter_name="gap_bucket",
            description="Gap size from setups.json (fraction): <10% / 10-25% / >25%",
            bucket_order=["<10%", "10-25%", ">25%"],
        ),
        analyze_bucket_filter(
            rows,
            "rsi_bucket",
            filter_name="rsi_bucket",
            description="RSI(14) at open: <30 / 30-70 / >70",
            bucket_order=["<30", "30-70", ">70"],
        ),
    ]

    # Regime: report availability only
    n_regime = sum(1 for r in rows if r["features"].get("regime") is not None)

    # Rank binary filters by lift (None last)
    ranked = sorted(
        binary_filters,
        key=lambda f: (
            f["lift_pct"] is None,
            -(f["lift_pct"] if f["lift_pct"] is not None else 0),
        ),
    )

    n_cluster = sum(1 for r in rows if r["cluster"] == "cluster_2026-08-20")
    n_rest = sum(1 for r in rows if r["cluster"] == "rest")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "fixed_exit": "Strategy A (Trailing)",
        "fixed_entry_for_outcome": "immediate (09:30 close)",
        "note": (
            "Univariate only — no filter combinations. "
            f"Groups with N < {MIN_N} flagged insufficient / likely noise. "
            "Robustness = edge sign must hold in 2026-08-20 cluster AND rest."
        ),
        "n_setups": len(rows),
        "n_cluster_2026_08_20": n_cluster,
        "n_rest": n_rest,
        "min_n": MIN_N,
        "regime_available_n": n_regime,
        "regime_note": (
            "regime not present on setups.json (all null) — skipped as a filter"
            if n_regime == 0
            else "regime present"
        ),
        "binary_filters_ranked_by_lift": ranked,
        "bucket_filters": bucket_filters,
        "per_setup_features": [
            {
                "ticker": r["ticker"],
                "flag_date": r["flag_date"],
                "cluster": r["cluster"],
                "return_pct": r["return_pct"],
                "killed": r["killed"],
                **{
                    k: r["features"].get(k)
                    for k in (
                        "volume_surge",
                        "open5_vs_baseline",
                        "above_vwap",
                        "rsi",
                        "rsi_bucket",
                        "gap_pct",
                        "gap_bucket",
                        "sweep_reclaim_passed",
                        "regime",
                    )
                },
            }
            for r in rows
        ],
    }


def _fmt(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}" if signed else f"{x:.2f}"


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 92)
    print("ENTRY FILTER EDGE STUDY — univariate (exit fixed = Strategy A)")
    print("=" * 92)
    print(f"  Setups: {report['n_setups']}  "
          f"(cluster {CLUSTER_DATE}: {report['n_cluster_2026_08_20']}  |  "
          f"rest: {report['n_rest']})")
    print(f"  Outcome: immediate entry × Strategy A return/kill")
    print(f"  Min N per group: {report['min_n']}  "
          f"(below → insufficient / likely noise)")
    print(f"  Regime: {report['regime_note']}")

    print()
    print("1) BINARY FILTERS — ranked by lift (pass avg% − fail avg%)")
    print("-" * 92)
    print(
        f"  {'filter':<24} {'Npass':>5} {'Nfail':>5}  "
        f"{'pass%':>8} {'fail%':>8} {'lift':>8}  "
        f"{'survives?':<10} verdict"
    )
    for f in report["binary_filters_ranked_by_lift"]:
        survives = "YES" if f["robustness"]["edge_survives_both_halves"] else "no"
        if f["insufficient"]:
            survives = "n/a"
        print(
            f"  {f['filter']:<24} {f['n_pass']:>5} {f['n_fail']:>5}  "
            f"{_fmt(f['pass']['avg_return_pct'], True):>8} "
            f"{_fmt(f['fail']['avg_return_pct'], True):>8} "
            f"{_fmt(f['lift_pct'], True):>8}  "
            f"{survives:<10} {f['verdict']}"
        )
        # kill rates
        print(
            f"    kill% pass={_fmt(f['pass']['kill_rate_pct'])}  "
            f"fail={_fmt(f['fail']['kill_rate_pct'])}  |  "
            f"cluster lift={_fmt(f['robustness']['cluster_2026-08-20']['lift_pct'], True)} "
            f"(n {f['robustness']['cluster_2026-08-20']['n_pass']}/"
            f"{f['robustness']['cluster_2026-08-20']['n_fail']})  "
            f"rest lift={_fmt(f['robustness']['rest']['lift_pct'], True)} "
            f"(n {f['robustness']['rest']['n_pass']}/"
            f"{f['robustness']['rest']['n_fail']})"
        )

    print()
    print("2) MULTI-BUCKET FILTERS (univariate; each bucket vs rest)")
    print("-" * 92)
    for bf in report["bucket_filters"]:
        print(f"\n  [{bf['filter']}]  {bf['description']}")
        print(
            f"  {'bucket':<10} {'N':>4}  {'avg%':>8} {'lift vs rest':>12}  verdict"
        )
        for row in bf["ranked_by_lift"]:
            print(
                f"  {row['bucket']:<10} {row['n']:>4}  "
                f"{_fmt(row['avg_return_pct'], True):>8} "
                f"{_fmt(row['lift_vs_rest_pct'], True):>12}  "
                f"{row['verdict']}"
            )

    print()
    print("3) TAKEAWAYS")
    print("-" * 92)
    survivors = [
        f for f in report["binary_filters_ranked_by_lift"]
        if f["verdict"] == "edge survives cluster split"
    ]
    one_half = [
        f for f in report["binary_filters_ranked_by_lift"]
        if "overfitting" in f["verdict"]
    ]
    noisy = [
        f for f in report["binary_filters_ranked_by_lift"]
        if f["insufficient"]
    ]
    if survivors:
        print("  Stable positive edges:")
        for f in survivors:
            print(
                f"    • {f['filter']}: lift {_fmt(f['lift_pct'], True)}%  "
                f"(pass N={f['n_pass']}, fail N={f['n_fail']})"
            )
    else:
        print("  No binary filter showed a stable positive edge in BOTH halves.")
    if one_half:
        print("  Looks like overfitting (edge in only one half):")
        for f in one_half:
            print(f"    • {f['filter']}: overall lift {_fmt(f['lift_pct'], True)}%")
    if noisy:
        print("  Insufficient N (treat as noise):")
        for f in noisy:
            print(
                f"    • {f['filter']}: pass N={f['n_pass']}, fail N={f['n_fail']}"
            )
    print("=" * 92)


def main() -> None:
    if not MATRIX_JSON.exists():
        raise SystemExit(
            f"Missing {MATRIX_JSON} — run matrix.py first "
            "(needs immediate × Strategy A outcomes)."
        )
    print("Building entry features + univariate filter study...")
    rows = build_rows()
    print(
        f"Usable setups: {len(rows)}  "
        f"(cluster={sum(1 for r in rows if r['cluster']=='cluster_2026-08-20')}, "
        f"rest={sum(1 for r in rows if r['cluster']=='rest')})"
    )
    report = run_study(rows)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"Wrote {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
