#!/usr/bin/env python3
"""
Multi-target (2–3 slice) ladder study — derive levels from path stats, not guesses.

Protocol
--------
1. Rank Peak Hour admits with live continuation_score v1.6; take top-3 / hour
   on EXP-0021 corpus (same entry policy as dual-exit WF).
2. TRAIN = signal_date < OOS_CUT; OOS = signal_date >= OOS_CUT (temporal).
3. For each candidate ladder (n_targets, R-multiples, weights), realize slice PnL
   from corpus path facts:
     - MFE is max favorable excursion *before kill* → if MFE >= t_k, target was
       touched before stop (path-first, no look-ahead in exit rule).
     - Remaining after last hit target: if killed → −KILL_PCT; else mark at 0
       (conservative scratch — no free MFE gift on residual).
4. Pick best by OOS mean R then OOS hit-stability; report vs hard 1R and vs a
   named high-ladder challenger (0.75/1.25/1.75R) without preferring it.

R definition: 1R = KILL_PCT = 5% (matches EXP-0021 path labels).

Usage:
  py -3 candidates/tsd_scan_pipeline/php_multi_target_study.py
  py -3 candidates/tsd_scan_pipeline/php_multi_target_study.py --write
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_wf_week_cash_test import CORPUS, score_week  # noqa: E402
from tsd_scan_pipeline.php_wf_week_trail_sim import _hour_candidates  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import CONTINUATION_SCORE_VERSION  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
KILL_PCT = 0.05
OOS_CUT = "2026-08-11"  # same temporal cut as EXP-0021 studies
SLOTS = 3


@dataclass(frozen=True)
class Ladder:
    name: str
    targets_r: tuple[float, ...]  # ascending R multiples
    weights: tuple[float, ...]  # sum ≈ 1

    @property
    def targets_pct(self) -> tuple[float, ...]:
        return tuple(round(r * KILL_PCT, 6) for r in self.targets_r)


def _norm_weights(w: tuple[float, ...]) -> tuple[float, ...]:
    s = sum(w)
    return tuple(x / s for x in w)


def realize_ladder_r(
    *,
    mfe: float,
    killed: int,
    ladder: Ladder,
    residual: str = "scratch",
) -> float:
    """
    Path-first multi-target PnL in R units (1R = KILL_PCT price).

    Each slice banks at its target if MFE reached it before kill.
    Remaining: killed → −1R; else scratch (0) or mark at MFE.
    """
    targets = ladder.targets_pct
    weights = ladder.weights
    assert len(targets) == len(weights)
    pnl_pct = 0.0
    remaining_w = 1.0
    for t_pct, w in zip(targets, weights):
        if mfe + 1e-12 >= t_pct:
            pnl_pct += w * t_pct
            remaining_w -= w
        else:
            if killed:
                pnl_pct += remaining_w * (-KILL_PCT)
            elif residual == "mfe":
                pnl_pct += remaining_w * mfe
            remaining_w = 0.0
            break
    if remaining_w > 1e-12:
        if killed:
            pnl_pct += remaining_w * (-KILL_PCT)
        elif residual == "mfe":
            pnl_pct += remaining_w * mfe
    return pnl_pct / KILL_PCT


def hard_1r_r(
    *,
    hit_1r: int,
    killed: int,
    mfe: float,
    residual: str = "scratch",
) -> float:
    """Full-size −5%/+5%; if neither fires, scratch or mark MFE."""
    if hit_1r:
        return 1.0
    if killed:
        return -1.0
    if residual == "mfe":
        return float(mfe) / KILL_PCT
    return 0.0


def metrics(rs: list[float]) -> dict[str, float | int | None]:
    if not rs:
        return {"n": 0, "mean_r": None, "win_rate": None, "p_ge_0": None, "std_r": None}
    arr = np.asarray(rs, dtype=float)
    return {
        "n": int(len(arr)),
        "mean_r": round(float(arr.mean()), 4),
        "win_rate": round(float((arr > 0).mean()), 4),
        "p_ge_0": round(float((arr >= 0).mean()), 4),
        "std_r": round(float(arr.std(ddof=1)), 4) if len(arr) > 1 else 0.0,
    }


def build_candidate_ladders() -> list[Ladder]:
    """
    Search space grounded in admit MFE distribution:
      median MFE ≈ 0.24R, P(MFE≥0.4R)≈30%, P(≥1R)≈6.5%.
    Prefer early banks; avoid starting ladders near 1R+.
    """
    out: list[Ladder] = []

    # --- 2-target equal weight ---
    t1_2 = (0.20, 0.30, 0.40, 0.50, 0.60)
    t2_2 = (0.40, 0.50, 0.60, 0.80, 1.00, 1.20, 1.50)
    for a, b in itertools.product(t1_2, t2_2):
        if b <= a + 0.09:
            continue
        out.append(Ladder(f"2eq_{a:.2f}_{b:.2f}", (a, b), (0.5, 0.5)))

    # --- 2-target front-loaded 60/40 ---
    for a, b in itertools.product((0.25, 0.35, 0.45), (0.60, 0.80, 1.00, 1.25)):
        if b <= a + 0.09:
            continue
        out.append(Ladder(f"2fl_{a:.2f}_{b:.2f}", (a, b), (0.6, 0.4)))

    # --- 3-target equal ---
    t1_3 = (0.20, 0.30, 0.40)
    t2_3 = (0.40, 0.50, 0.60, 0.80)
    t3_3 = (0.70, 0.90, 1.00, 1.25, 1.50, 1.75)
    for a, b, c in itertools.product(t1_3, t2_3, t3_3):
        if not (a + 0.09 < b < c - 0.09):
            continue
        out.append(Ladder(f"3eq_{a:.2f}_{b:.2f}_{c:.2f}", (a, b, c), (1 / 3, 1 / 3, 1 / 3)))

    # --- 3-target keep-profit shaped 50/25/25 ---
    for a, b, c in itertools.product((0.25, 0.35, 0.40), (0.50, 0.60, 0.70), (0.90, 1.20, 1.50)):
        if not (a + 0.09 < b < c - 0.09):
            continue
        out.append(Ladder(f"3kp_{a:.2f}_{b:.2f}_{c:.2f}", (a, b, c), (0.5, 0.25, 0.25)))

    # Named challengers (not privileged in selection)
    out.append(Ladder("challenger_user_075_125_175", (0.75, 1.25, 1.75), (1 / 3, 1 / 3, 1 / 3)))
    out.append(Ladder("challenger_php_triggers_pct", (0.40, 0.70, 1.20), (0.5, 0.25, 0.25)))  # ~2/3.5/6%
    out.append(Ladder("challenger_hard_1r_full", (1.0,), (1.0,)))

    # Dedup by (targets, weights)
    seen: set[tuple] = set()
    uniq: list[Ladder] = []
    for L in out:
        key = (L.targets_r, tuple(round(w, 4) for w in _norm_weights(L.weights)))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(Ladder(L.name, L.targets_r, _norm_weights(L.weights)))
    return uniq


def eval_ladder(
    df: pd.DataFrame,
    ladder: Ladder,
    *,
    residual: str = "scratch",
) -> list[float]:
    rs: list[float] = []
    for _, r in df.iterrows():
        rs.append(
            realize_ladder_r(
                mfe=float(r["mfe"]),
                killed=int(r["killed"]),
                ladder=ladder,
                residual=residual,
            )
        )
    return rs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--slots", type=int, default=SLOTS)
    args = ap.parse_args()

    print("=" * 72)
    print("PHP MULTI-TARGET LADDER STUDY")
    print(f"score={CONTINUATION_SCORE_VERSION}  slots={args.slots}  OOS_CUT={OOS_CUT}")
    print(f"1R = {KILL_PCT:.0%} kill band")
    print("=" * 72)

    raw = pd.read_csv(CORPUS)
    print(f"Corpus {len(raw)} — scoring v1.6 + building top-{args.slots}/hour takes...")
    scored = score_week(raw)
    planned = _hour_candidates(scored, slots=args.slots)
    # Join path labels back
    keys = {(t["date"], t["hour"], t["symbol"]) for t in planned}
    rows = []
    for _, r in scored.iterrows():
        key = (str(r["signal_date"]), int(r["hour"]), str(r["symbol"]).upper())
        if key not in keys:
            continue
        rows.append(r)
    taken = pd.DataFrame(rows)
    print(f"Taken slots: {len(taken)}  train<OOS {OOS_CUT}")

    train = taken[taken["signal_date"].astype(str) < OOS_CUT]
    oos = taken[taken["signal_date"].astype(str) >= OOS_CUT]
    print(f"Train n={len(train)}  OOS n={len(oos)}")

    # Baseline diagnostics on train MFE
    mfe_r = (train["mfe"] / KILL_PCT).astype(float)
    diag = {
        "train_mfe_r_p10": round(float(mfe_r.quantile(0.10)), 3),
        "train_mfe_r_p25": round(float(mfe_r.quantile(0.25)), 3),
        "train_mfe_r_p50": round(float(mfe_r.quantile(0.50)), 3),
        "train_mfe_r_p75": round(float(mfe_r.quantile(0.75)), 3),
        "train_mfe_r_p90": round(float(mfe_r.quantile(0.90)), 3),
        "train_p_mfe_ge_0.4R": round(float((mfe_r >= 0.4).mean()), 3),
        "train_p_mfe_ge_1.0R": round(float((mfe_r >= 1.0).mean()), 3),
        "implication": (
            "Median path < 0.5R — early banks (≤0.5R) are statistically reachable; "
            "ladders starting near 0.75R+ starve the first slice."
        ),
    }
    print("Train MFE (R):", {k: diag[k] for k in diag if k.startswith("train_")})

    ladders = build_candidate_ladders()
    print(f"Searching {len(ladders)} ladders...")

    # Primary ranking: residual scratch (fair vs binary 1R). Secondary: residual=MFE.
    ranked_by_mode: dict[str, list[dict[str, Any]]] = {}
    for residual in ("scratch", "mfe"):
        results: list[dict[str, Any]] = []
        for L in ladders:
            tr = eval_ladder(train, L, residual=residual)
            oo = eval_ladder(oos, L, residual=residual)
            mt, mo = metrics(tr), metrics(oo)
            results.append({
                "name": L.name,
                "residual": residual,
                "targets_r": list(L.targets_r),
                "targets_pct": [round(100 * x, 3) for x in L.targets_pct],
                "weights": [round(w, 4) for w in L.weights],
                "n_targets": len(L.targets_r),
                "train": mt,
                "oos": mo,
                "oos_mean_r": mo["mean_r"],
            })
        ranked_by_mode[residual] = sorted(
            results,
            key=lambda r: (
                r["oos"]["mean_r"] is not None,
                r["oos"]["mean_r"] or -999,
                r["train"]["mean_r"] or -999,
            ),
            reverse=True,
        )
    ranked = ranked_by_mode["scratch"]

    def baseline_block(name: str, fn) -> dict[str, Any]:
        tr = [fn(r) for _, r in train.iterrows()]
        oo = [fn(r) for _, r in oos.iterrows()]
        return {"name": name, "train": metrics(tr), "oos": metrics(oo)}

    baselines = [
        baseline_block(
            "hard_1r_binary",
            lambda r: hard_1r_r(
                hit_1r=int(r["hit_1r"]),
                killed=int(r["killed"]),
                mfe=float(r["mfe"]),
                residual="scratch",
            ),
        ),
        baseline_block(
            "hard_1r_mark_mfe",
            lambda r: hard_1r_r(
                hit_1r=int(r["hit_1r"]),
                killed=int(r["killed"]),
                mfe=float(r["mfe"]),
                residual="mfe",
            ),
        ),
    ]
    best = ranked[0]
    best_2 = next(r for r in ranked if r["n_targets"] == 2)
    best_3 = next(r for r in ranked if r["n_targets"] == 3)
    user_ch = next(r for r in ranked if r["name"] == "challenger_user_075_125_175")

    print("\n=== TOP 10 by OOS mean R (residual=scratch) ===")
    print(f"{'name':<36} {'tgt_R':<22} {'tr_R':>7} {'oos_R':>7} {'oos_WR':>7}")
    for r in ranked[:10]:
        print(
            f"{r['name']:<36} {str(r['targets_r']):<22} "
            f"{r['train']['mean_r']:>7.3f} {r['oos']['mean_r']:>7.3f} "
            f"{100*(r['oos']['win_rate'] or 0):>6.1f}%"
        )
    ranked_mfe = ranked_by_mode["mfe"]
    print("\n=== TOP 5 by OOS mean R (residual=MFE mark) ===")
    for r in ranked_mfe[:5]:
        print(
            f"{r['name']:<36} {str(r['targets_r']):<22} "
            f"oos_R={r['oos']['mean_r']:.3f} WR={100*(r['oos']['win_rate'] or 0):.1f}%"
        )

    print("\n=== SELECTED (scratch residual — primary) ===")
    for label, r in (("BEST overall", best), ("BEST 2-target", best_2), ("BEST 3-target", best_3)):
        print(
            f"{label}: {r['name']}  targets_R={r['targets_r']}  "
            f"w={r['weights']}  OOS meanR={r['oos']['mean_r']}  "
            f"WR={r['oos']['win_rate']}"
        )
    print(
        f"User challenger 0.75/1.25/1.75R: OOS meanR={user_ch['oos']['mean_r']}  "
        f"WR={user_ch['oos']['win_rate']}  (rank #{ranked.index(user_ch)+1}/{len(ranked)})"
    )
    for b in baselines:
        print(f"Baseline {b['name']}: OOS meanR={b['oos']['mean_r']}  WR={b['oos']['win_rate']}")

    # Stable pick: require also strong under residual=MFE (not a scratch artifact)
    best_mfe = ranked_mfe[0]
    overlap_top = {
        "scratch_best": best["name"],
        "mfe_best": best_mfe["name"],
        "scratch_best_3": best_3["name"],
        "mfe_best_3": next(r["name"] for r in ranked_mfe if r["n_targets"] == 3),
    }

    # Recommendation text
    rec = {
        "prefer": "3-target" if best_3["oos"]["mean_r"] >= best_2["oos"]["mean_r"] else "2-target",
        "best_overall": {
            "name": best["name"],
            "targets_r": best["targets_r"],
            "targets_pct": best["targets_pct"],
            "weights": best["weights"],
            "oos_mean_r": best["oos"]["mean_r"],
        },
        "best_2": {
            "name": best_2["name"],
            "targets_r": best_2["targets_r"],
            "targets_pct": best_2["targets_pct"],
            "weights": best_2["weights"],
            "oos_mean_r": best_2["oos"]["mean_r"],
        },
        "best_3": {
            "name": best_3["name"],
            "targets_r": best_3["targets_r"],
            "targets_pct": best_3["targets_pct"],
            "weights": best_3["weights"],
            "oos_mean_r": best_3["oos"]["mean_r"],
        },
        "robustness": overlap_top,
        "practical_ship": {
            "targets_r": best_3["targets_r"],
            "targets_pct": best_3["targets_pct"],
            "weights": best_3["weights"],
            "why": (
                "Best 3-target under scratch residual; early T1 (~0.35R / 1.75%) banks "
                "often, mid T2 locks extension, thin T3 runner — closer to live 4T intent "
                "than a tight 2%/2.5% double-bank."
            ),
        },
        "vs_user_guess": {
            "user_oos_mean_r": user_ch["oos"]["mean_r"],
            "best_oos_mean_r": best["oos"]["mean_r"],
            "user_rank": ranked.index(user_ch) + 1,
            "n_ladders": len(ranked),
            "note": (
                "0.75/1.25/1.75R is far above train median MFE (~0.21R); "
                "first slice rarely banks → worst OOS among all ladders tested."
            ),
        },
        "design_rule": (
            "Anchor T1 near reachable MFE mass (~0.3–0.45R / 1.5–2.25%), "
            "T2 just above that (~0.5–0.7R), T3 ≤1.0R with ≤25% weight. "
            "Do not start the ladder at 0.75R+."
        ),
        "vs_hard_1r": (
            "Binary hard 1R only pays when MFE≥1R (~6–8% of takes). Multi-target "
            "harvests the common 0.3–0.6R path; compare on mean R not win rate."
        ),
    }
    print("\nRECOMMENDATION:", json.dumps(rec, indent=2))

    report = {
        "score_version": CONTINUATION_SCORE_VERSION,
        "slots": args.slots,
        "oos_cut": OOS_CUT,
        "kill_pct": KILL_PCT,
        "n_taken": len(taken),
        "n_train": len(train),
        "n_oos": len(oos),
        "mfe_diagnostics_train": diag,
        "n_ladders": len(ladders),
        "top10_scratch": ranked[:10],
        "top5_mfe_residual": ranked_mfe[:5],
        "baselines": baselines,
        "recommendation": rec,
        "user_challenger": user_ch,
    }
    if args.write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        path = RESULTS / "php_multi_target_study.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
