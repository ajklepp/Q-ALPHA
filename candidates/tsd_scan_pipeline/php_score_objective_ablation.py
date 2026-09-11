#!/usr/bin/env python3
"""
First-principles score ablation — which launch fields predict same-day extension?

Label (causal outcome, research-only):
  win_proxy = hit +2% MFE before -5% MAE on Polygon 1H bars after signal hour.

Does NOT change live ranking by itself — diagnoses why #1–2 underperform.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_taken_vs_missed_study import (  # noqa: E402
    RESULTS_DIR,
    _fetch_1h_bars,
    _parse_scan_et,
    _path_stats,
    _polygon_key,
)
from tsd_scan_pipeline.php_scan_funnel import list_scan_funnels_since  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    EARLY_SESSION_BONUS_HOURS,
    EARLY_SESSION_BONUS_PTS,
    LATE_SESSION_HOURS,
    LATE_SESSION_PENALTY_PTS,
    LAUNCH_TERM_WEIGHT,
)


def _v16_proxy(*, hour: int, launch_score: float) -> float:
    """
    Offline first-principles proxy using only fields on php_scan funnels.

    Full enrich needs bar_state/RS/tape; this isolates the two ablation winners
    (session clock + launch_score) that v1.6 reweights.
    """
    if hour in EARLY_SESSION_BONUS_HOURS:
        session = EARLY_SESSION_BONUS_PTS
    elif hour in LATE_SESSION_HOURS:
        session = -LATE_SESSION_PENALTY_PTS
    else:
        session = 0.0
    return round(session + LAUNCH_TERM_WEIGHT * float(launch_score or 0.0), 2)


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 8 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx <= 1e-12 or deny <= 1e-12:
        return None
    return round(num / (denx * deny), 4)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def build_ablation(*, days: int = 7, max_per_hour: int = 10) -> dict[str, Any]:
    key = _polygon_key()
    if not key:
        raise RuntimeError("POLYGON_API_KEY missing")

    paths = list_scan_funnels_since(days)
    bar_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        when = _parse_scan_et(doc, path)
        if when is None:
            continue
        bar_hour = int(doc.get("bar_hour") if doc.get("bar_hour") is not None else when.hour)
        day = when.date()
        launches = list(doc.get("launches") or [])
        launches = sorted(launches, key=lambda r: int(r.get("rank_order") or 999))[:max_per_hour]

        for L in launches:
            sym = str(L.get("symbol") or "").upper()
            try:
                entry = float(L.get("1h_close") or 0)
            except (TypeError, ValueError):
                entry = 0.0
            if not sym or entry <= 0:
                continue
            bars = _fetch_1h_bars(key, sym, day, bar_cache)
            path_stats = _path_stats(bars, signal_hour=bar_hour, entry=entry)
            if not path_stats.get("ok"):
                continue
            rows.append({
                "symbol": sym,
                "day": day.isoformat(),
                "hour": bar_hour,
                "taken": bool(L.get("taken")),
                "rank": float(L.get("rank") or 0),
                "rank_order": int(L.get("rank_order") or 0),
                "htf_score": float(L.get("htf_score") or 0),
                "launch_score": float(L.get("launch_score") or 0),
                "v16_proxy": _v16_proxy(
                    hour=bar_hour,
                    launch_score=float(L.get("launch_score") or 0),
                ),
                "price": entry,
                "phase": str(L.get("phase_3h") or ""),
                "mfe_pct": float(path_stats["mfe_pct"]),
                "mae_pct": float(path_stats["mae_pct"]),
                "close_ret_pct": float(path_stats["close_ret_pct"]),
                "win_proxy": int(bool(path_stats["win_proxy"])),
                "hit_2pct": int(bool(path_stats["hit_2pct"])),
            })

    # Correlations vs outcomes
    features = ("rank", "rank_order", "htf_score", "launch_score", "hour", "price", "v16_proxy")
    corrs: dict[str, dict[str, float | None]] = {}
    for f in features:
        xs = [float(r[f]) for r in rows]
        corrs[f] = {
            "vs_mfe_pct": _corr(xs, [r["mfe_pct"] for r in rows]),
            "vs_win_proxy": _corr(xs, [float(r["win_proxy"]) for r in rows]),
            "vs_close_ret_pct": _corr(xs, [r["close_ret_pct"] for r in rows]),
        }

    # Top-2 under stored rank vs top-2 under v1.6 proxy (per hour)
    top2_old: list[dict[str, Any]] = []
    top2_v16: list[dict[str, Any]] = []
    by_hour_for_pick: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_hour_for_pick[(r["day"], r["hour"])].append(r)
    for group in by_hour_for_pick.values():
        by_old = sorted(group, key=lambda x: int(x["rank_order"] or 999))
        by_v16 = sorted(group, key=lambda x: -float(x["v16_proxy"]))
        top2_old.extend(by_old[:2])
        top2_v16.extend(by_v16[:2])

    rest = [r for r in rows if r["rank_order"] >= 3]
    band = {
        "top2_stored_rank": {
            "n": len(top2_old),
            "win_proxy_rate": _mean([float(r["win_proxy"]) for r in top2_old]),
            "avg_mfe_pct": _mean([r["mfe_pct"] for r in top2_old]),
        },
        "top2_v16_proxy": {
            "n": len(top2_v16),
            "win_proxy_rate": _mean([float(r["win_proxy"]) for r in top2_v16]),
            "avg_mfe_pct": _mean([r["mfe_pct"] for r in top2_v16]),
        },
        "rank_3_to_max_stored": {
            "n": len(rest),
            "win_proxy_rate": _mean([float(r["win_proxy"]) for r in rest]),
            "avg_mfe_pct": _mean([r["mfe_pct"] for r in rest]),
        },
    }

    # Within each hour: does higher rank predict higher MFE among that hour's list?
    hour_rank_corr: list[float] = []
    by_hour: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_hour[(r["day"], r["hour"])].append(r)
    for group in by_hour.values():
        if len(group) < 4:
            continue
        c = _corr([r["rank"] for r in group], [r["mfe_pct"] for r in group])
        if c is not None:
            hour_rank_corr.append(c)

    # Diagnosis — first principles: does the score predict the live label?
    rank_mfe = (corrs.get("rank") or {}).get("vs_mfe_pct")
    hour_win = (corrs.get("hour") or {}).get("vs_win_proxy")
    launch_mfe = (corrs.get("launch_score") or {}).get("vs_mfe_pct")
    v16_mfe = (corrs.get("v16_proxy") or {}).get("vs_mfe_pct")
    diagnosis = []
    if rank_mfe is not None and abs(rank_mfe) < 0.12:
        diagnosis.append(
            f"continuation rank is near-noise vs same-day MFE (r={rank_mfe}). "
            "The ranker is not optimizing the live Peak Hour objective — "
            "fix the score, do not permanently skip to #3–5."
        )
    if hour_win is not None and abs(hour_win) > abs(rank_mfe or 0) * 2:
        diagnosis.append(
            f"Clock hour predicts win-proxy far better than score "
            f"(hour r={hour_win} vs rank r={rank_mfe}). "
            "Calendar peak-hour bonuses that ignore this are first-principles wrong."
        )
    if launch_mfe is not None and rank_mfe is not None and launch_mfe > rank_mfe:
        diagnosis.append(
            f"launch_score correlates more with MFE (r={launch_mfe}) than the composite "
            f"rank (r={rank_mfe}) — hist/room/HTF stack is diluting the useful signal."
        )
    if v16_mfe is not None and rank_mfe is not None and v16_mfe > rank_mfe:
        diagnosis.append(
            f"v1.6 proxy (session clock + launch) beats stored rank vs MFE "
            f"(r={v16_mfe} vs {rank_mfe})."
        )
    t2_old_mfe = band["top2_stored_rank"]["avg_mfe_pct"] or 0
    t2_v16_mfe = band["top2_v16_proxy"]["avg_mfe_pct"] or 0
    if t2_v16_mfe > t2_old_mfe:
        diagnosis.append(
            f"Per-hour top-2 under v1.6 proxy avg MFE {t2_v16_mfe:.2f}% > "
            f"stored-rank top-2 {t2_old_mfe:.2f}% — reweight improves slot picks without #3–5 skip."
        )
    within = {
        "n_hours": len(hour_rank_corr),
        "mean_corr": _mean(hour_rank_corr),
        "neg_corr_hours": sum(1 for c in hour_rank_corr if c < 0),
    }
    if within["n_hours"] and (within["mean_corr"] or 0) < 0.1:
        diagnosis.append(
            f"Within-hour rank vs MFE mean corr={within['mean_corr']} "
            f"({within['neg_corr_hours']}/{within['n_hours']} hours negative) — "
            "even inside one scan, higher score does not reliably pick the extender."
        )

    return {
        "n_rows": len(rows),
        "scans": len(paths),
        "feature_correlations": corrs,
        "top2_vs_rest": band,
        "within_hour_rank_vs_mfe": within,
        "diagnosis": diagnosis,
        "first_principles": {
            "wrong_objective": (
                "Legacy continuation_score maximized multi-factor 'setup quality' "
                "(ticker prior hist, 20d room, peak-hour calendar, HTF). "
                "Live Peak Hour needs P(same-day +2% before -5%) after the signal bar."
            ),
            "fix": (
                "v1.6 reweights toward session clock + launch_score / tape / RS_1h; "
                "downweights hist prior + late peak-hour calendar + deep room. "
                "Do not permanently skip to #3–5."
            ),
            "score_version": "v1.6",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-per-hour", type=int, default=10)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    print("Ablating score vs same-day path...")
    out = build_ablation(days=args.days, max_per_hour=args.max_per_hour)
    print(json.dumps(out, indent=2))
    if args.write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / "score_objective_ablation.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
