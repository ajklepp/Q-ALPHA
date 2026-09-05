#!/usr/bin/env python3
"""
EXP-0021 — Deep-edge studies (1→6) on existing HTF corpus.

1) Score-term ablation
2) Lookback / room horizon bakeoff (20d vs 52w vs SMA200 proxy)
3) Vol / scan bands on taken slots
4) News/catalyst (report coverage; pilot if present)
5) TF-mix proxies (1H admit vs +daily trend filters)
6) Fitted logistic ranker + feature importance

Usage:
  .\\venv\\Scripts\\python.exe experiments\\EXP-0021\\study_deep_edge.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_DIR))

from lib.features import (  # noqa: E402
    BAR_STATE_PTS,
    PEAK_HOURS,
    continuation_score_v1,
)

ET = pytz.timezone("America/New_York")
SLOTS = 2
CORPUS_PATH = EXP_DIR / "corpus_htf_universe.csv"
PILOT_PATH = EXP_DIR / "corpus_pilot.csv"
OUT_MD = EXP_DIR / "STUDY_DEEP_EDGE.md"
OUT_JSON = EXP_DIR / "study_deep_edge_metrics.json"
OOS_CUT = "2026-08-11"


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _metrics(taken: pd.DataFrame) -> dict[str, float]:
    if taken is None or taken.empty:
        return {
            "n": 0.0, "wr": 0.0, "exp": 0.0, "med_mfe": 0.0,
            "oos_wr": 0.0, "oos_exp": 0.0,
        }
    oos = taken[taken["signal_date"] >= OOS_CUT]
    return {
        "n": float(len(taken)),
        "wr": float(taken["hit_1r"].mean()),
        "exp": float(taken["r_multiple"].mean()),
        "med_mfe": float(taken["mfe"].median()),
        "oos_wr": float(oos["hit_1r"].mean()) if len(oos) else 0.0,
        "oos_exp": float(oos["r_multiple"].mean()) if len(oos) else 0.0,
    }


def expander_capture(corpus: pd.DataFrame, taken: pd.DataFrame) -> dict[str, float]:
    day_sym = corpus.groupby(["signal_date", "symbol"], as_index=False)["day_mfe"].max()
    if day_sym.empty:
        return {"capture": 0.0, "n_expanders": 0.0, "n_caught": 0.0}
    thr = float(day_sym["day_mfe"].quantile(0.90))
    expanders = day_sym[day_sym["day_mfe"] >= thr]
    if expanders.empty:
        expanders = day_sym.nlargest(max(1, len(day_sym) // 10), "day_mfe")
    keys = set(zip(taken["signal_date"], taken["symbol"])) if not taken.empty else set()
    caught = sum(1 for _, r in expanders.iterrows() if (r["signal_date"], r["symbol"]) in keys)
    n = len(expanders)
    return {"capture": caught / n if n else 0.0, "n_expanders": float(n), "n_caught": float(caught)}


def simulate_slots(df: pd.DataFrame, *, score_col: str, admit_col: str = "all_hours_admit") -> pd.DataFrame:
    work = df[df[admit_col] == 1].copy()
    if work.empty:
        return work
    work = work.sort_values(["signal_date", "hour", score_col], ascending=[True, True, False])
    return work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS)


def score_v1_custom(
    feat: dict[str, Any],
    *,
    zero: set[str] | None = None,
    room_field: str = "dist_20d_high_pct",
    scan_penalty: bool = True,
    vol_dead_penalty: bool = True,
    soft_extension: bool = True,
    flip_vol_reward_high: bool = False,
) -> float:
    """continuation_score_v1 with ablation / lookback / vol knobs."""
    zero = zero or set()
    peak = 25.0 if int(feat.get("hour") or -1) in PEAK_HOURS or feat.get("peak_hour") else 0.0
    if "peak" in zero:
        peak = 0.0

    bs = str(feat.get("bar_state") or "orange")
    bar_pts = float(BAR_STATE_PTS.get(bs, 0.0))
    if "bar" in zero:
        bar_pts = 0.0

    room = float(feat.get(room_field) or 0.0)
    # For SMA200 distance, positive = above SMA → treat as "room/structure ok"
    if room_field == "close_vs_sma200":
        # Map: more above SMA200 → modest bonus; below → penalty
        if room < 0:
            room_term = -15.0 * _clip01((-room) / 0.10)
        else:
            room_term = 20.0 * _clip01(room / 0.25)
    else:
        if room < 0:
            room_term = -15.0 * _clip01((-room) / 0.05)
        else:
            room_term = 20.0 * _clip01(room / 0.15)
    if "room" in zero:
        room_term = 0.0

    bounce = float(feat.get("dist_20d_low_bounce") or 0.0)
    bounce_term = 15.0 * _clip01(bounce)
    if "bounce" in zero:
        bounce_term = 0.0

    vr = float(feat.get("vol_ratio_20") or 1.0)
    vol_term = 15.0 * _clip01(math.log1p(max(vr, 0.0)) / math.log1p(5.0))
    if vol_dead_penalty and vr < 0.5:
        vol_term -= 8.0
    if flip_vol_reward_high and vr >= 2.0:
        vol_term += 12.0  # ride the wave bonus
    if "vol" in zero:
        vol_term = 0.0

    prior_hit = float(feat.get("ticker_prior_hit1r_rate") or 0.0)
    prior_mfe = float(feat.get("ticker_prior_mfe_p50") or 0.0)
    hist_term = 10.0 * _clip01(prior_hit) + 12.0 * _clip01(prior_mfe / 0.05)
    if "prior" in zero:
        hist_term = 0.0

    news_v = float(feat.get("news_velocity_24h") or feat.get("news_headline_count_48h") or 0.0)
    news_term = 10.0 * _clip01(news_v / 5.0)
    if "news" in zero:
        news_term = 0.0

    launch = float(feat.get("launch_score") or 0.0)
    launch_term = 0.25 * launch
    if "launch" in zero:
        launch_term = 0.0

    scan = float(feat.get("scan_score") or 55.0)
    if 25.0 <= scan <= 45.0:
        scan_term = 10.0
    elif scan <= 55.0:
        scan_term = 4.0
    else:
        scan_term = -10.0 if scan_penalty else 0.0
    if "scan" in zero:
        scan_term = 0.0

    score = (
        peak + bar_pts + room_term + bounce_term + vol_term + hist_term
        + news_term + launch_term + scan_term
    )
    if soft_extension and scan > 55.0 and "extension" not in zero:
        score -= 20.0
    if "extension" in zero and soft_extension is False:
        pass
    if feat.get("guidance_cut"):
        score -= 25.0
    return round(score, 2)


def apply_score(df: pd.DataFrame, col: str, fn: Callable[[dict], float]) -> pd.DataFrame:
    out = df.copy()
    out[col] = [fn(r._asdict() if hasattr(r, "_asdict") else r.to_dict()) for _, r in out.iterrows()]
    return out


def study1_ablation(df: pd.DataFrame) -> dict[str, Any]:
    """Leave-one-term-out ablations vs baseline v1."""
    print("\n=== STUDY 1: Score-term ablation ===")
    variants = {
        "baseline_v1": set(),
        "no_peak": {"peak"},
        "no_bar": {"bar"},
        "no_room": {"room"},
        "no_bounce": {"bounce"},
        "no_vol": {"vol"},
        "no_prior": {"prior"},
        "no_launch": {"launch"},
        "no_scan_term": {"scan"},
        "no_soft_extension": {"extension"},  # handled specially
    }
    rows = []
    for name, zero in variants.items():
        col = f"score_{name}"

        def _fn(row, z=zero, n=name):
            soft = "extension" not in z
            # no_soft_extension → zero extension penalty
            if n == "no_soft_extension":
                return score_v1_custom(row, zero=set(), soft_extension=False)
            return score_v1_custom(row, zero=z, soft_extension=soft)

        # faster: vectorized-ish apply
        scores = []
        for _, r in df.iterrows():
            scores.append(_fn(r.to_dict()))
        work = df.copy()
        work[col] = scores
        taken = simulate_slots(work, score_col=col)
        m = _metrics(taken)
        cap = expander_capture(df, taken)
        row = {"variant": name, **m, **cap}
        rows.append(row)
        print(
            f"  {name:20s} n={m['n']:.0f} wr={m['wr']:.1%} exp={m['exp']:.3f} "
            f"cap={cap['capture']:.1%} oos_exp={m['oos_exp']:.3f}"
        )
    base = next(r for r in rows if r["variant"] == "baseline_v1")
    for r in rows:
        r["delta_exp"] = r["exp"] - base["exp"]
        r["delta_capture"] = r["capture"] - base["capture"]
    # Rank impact: largest |delta_exp| when removed (except baseline)
    impact = sorted(
        [r for r in rows if r["variant"] != "baseline_v1"],
        key=lambda x: abs(x["delta_exp"]),
        reverse=True,
    )
    return {"rows": rows, "impact_by_abs_delta_exp": [r["variant"] for r in impact]}


def study2_lookback(df: pd.DataFrame) -> dict[str, Any]:
    print("\n=== STUDY 2: Lookback / room horizon ===")
    configs = [
        ("room_20d", "dist_20d_high_pct"),
        ("room_52w", "dist_52w_high_pct"),
        ("above_sma200", "close_vs_sma200"),
        ("room_20d_plus_52w_avg", None),  # special
    ]
    rows = []
    for name, field in configs:
        col = f"score_{name}"
        scores = []
        for _, r in df.iterrows():
            d = r.to_dict()
            if name == "room_20d_plus_52w_avg":
                # Average of 20d and 52w room terms via temp field
                a = score_v1_custom(d, room_field="dist_20d_high_pct")
                b = score_v1_custom(d, room_field="dist_52w_high_pct")
                scores.append(round(0.5 * (a + b), 2))
            else:
                scores.append(score_v1_custom(d, room_field=field or "dist_20d_high_pct"))
        work = df.copy()
        work[col] = scores
        taken = simulate_slots(work, score_col=col)
        m = _metrics(taken)
        cap = expander_capture(df, taken)
        # Correlation of room field with mfe among admits
        if field and field in df.columns:
            sub = df[df["all_hours_admit"] == 1]
            corr = float(sub[field].corr(sub["mfe"])) if len(sub) > 10 else float("nan")
        else:
            corr = float("nan")
        rows.append({"variant": name, "mfe_corr": corr, **m, **cap})
        print(
            f"  {name:24s} n={m['n']:.0f} wr={m['wr']:.1%} exp={m['exp']:.3f} "
            f"cap={cap['capture']:.1%} corr_mfe={corr:.3f}"
        )
    best = max(rows, key=lambda r: r["exp"])
    return {"rows": rows, "best_by_exp": best["variant"]}


def study3_vol_scan(df: pd.DataFrame) -> dict[str, Any]:
    print("\n=== STUDY 3: Vol / scan on taken slots ===")
    # Baseline taken slots
    taken = simulate_slots(df, score_col="continuation_score_v1")
    admits = df[df["all_hours_admit"] == 1]

    def band_table(frame: pd.DataFrame, col: str, bins: list[tuple[str, Any]]) -> list[dict]:
        out = []
        for label, mask in bins:
            g = frame[mask]
            out.append({
                "band": label,
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()) if len(g) else 0.0,
                "exp": float(g["r_multiple"].mean()) if len(g) else 0.0,
                "med_mfe": float(g["mfe"].median()) if len(g) else 0.0,
                "med_mae": float(g["mae"].median()) if len(g) else 0.0,
            })
        return out

    scan = taken["scan_score"].astype(float)
    vol = taken["vol_ratio_20"].astype(float)
    taken_scan = band_table(taken, "scan", [
        ("scan_<45", scan < 45),
        ("scan_45_55", (scan >= 45) & (scan <= 55)),
        ("scan_55_75", (scan > 55) & (scan < 75)),
        ("scan_ge_75", scan >= 75),
    ])
    taken_vol = band_table(taken, "vol", [
        ("vol_<0.5", vol < 0.5),
        ("vol_0.5_1", (vol >= 0.5) & (vol < 1.0)),
        ("vol_1_2", (vol >= 1.0) & (vol < 2.0)),
        ("vol_ge_2", vol >= 2.0),
    ])
    print("  Taken by scan:")
    for r in taken_scan:
        print(f"    {r['band']:12s} n={r['n']:4d} wr={r['wr']:.1%} exp={r['exp']:.3f} mfe={r['med_mfe']:.2%}")
    print("  Taken by vol:")
    for r in taken_vol:
        print(f"    {r['band']:12s} n={r['n']:4d} wr={r['wr']:.1%} exp={r['exp']:.3f} mfe={r['med_mfe']:.2%}")

    # Score policy variants
    policies = []
    for name, kwargs in [
        ("baseline", {}),
        ("no_scan_penalty", {"scan_penalty": False, "soft_extension": False}),
        ("reward_high_vol", {"flip_vol_reward_high": True}),
        ("no_vol_dead_penalty", {"vol_dead_penalty": False}),
        ("reward_high_vol_no_ext_pen", {
            "flip_vol_reward_high": True, "scan_penalty": False, "soft_extension": False,
        }),
    ]:
        col = f"pol_{name}"
        work = df.copy()
        work[col] = [score_v1_custom(r.to_dict(), **kwargs) for _, r in df.iterrows()]
        t = simulate_slots(work, score_col=col)
        m = _metrics(t)
        cap = expander_capture(df, t)
        policies.append({"policy": name, **m, **cap})
        print(f"  policy {name:28s} exp={m['exp']:.3f} cap={cap['capture']:.1%} wr={m['wr']:.1%}")

    return {
        "taken_by_scan": taken_scan,
        "taken_by_vol": taken_vol,
        "admit_corr_scan_mfe": float(admits["scan_score"].corr(admits["mfe"])),
        "admit_corr_vol_mfe": float(admits["vol_ratio_20"].corr(admits["mfe"])),
        "policies": policies,
        "best_policy_by_exp": max(policies, key=lambda r: r["exp"])["policy"],
    }


def study4_news(df: pd.DataFrame) -> dict[str, Any]:
    print("\n=== STUDY 4: News / catalyst ===")
    has_news = "news_velocity_24h" in df.columns or "news_headline_count_48h" in df.columns
    social_miss = float(df["social_missing"].mean()) if "social_missing" in df.columns else 1.0
    news_col = None
    for c in ("news_velocity_24h", "news_headline_count_48h"):
        if c in df.columns:
            news_col = c
            break

    result: dict[str, Any] = {
        "htf_social_missing_rate": social_miss,
        "htf_has_news_column": bool(news_col),
        "verdict": "",
        "pilot": None,
        "htf": None,
    }

    if news_col and df[news_col].fillna(0).gt(0).any():
        sub = df[df["all_hours_admit"] == 1].copy()
        has = sub[news_col].fillna(0) > 0
        result["htf"] = {
            "n_with_news": int(has.sum()),
            "n_without": int((~has).sum()),
            "wr_with": float(sub.loc[has, "hit_1r"].mean()) if has.any() else None,
            "wr_without": float(sub.loc[~has, "hit_1r"].mean()) if (~has).any() else None,
            "exp_with": float(sub.loc[has, "r_multiple"].mean()) if has.any() else None,
            "exp_without": float(sub.loc[~has, "r_multiple"].mean()) if (~has).any() else None,
        }
        print(f"  HTF news col={news_col}: with={has.sum()} without={(~has).sum()}")
    else:
        print("  HTF corpus: news/social effectively empty (social_missing≈1).")

    if PILOT_PATH.exists():
        pilot = pd.read_csv(PILOT_PATH)
        ncol = None
        for c in ("news_velocity_24h", "news_headline_count_48h"):
            if c in pilot.columns:
                ncol = c
                break
        if ncol:
            p = pilot.copy()
            if "all_hours_admit" in p.columns:
                p = p[p["all_hours_admit"] == 1]
            has = p[ncol].fillna(0) > 0
            result["pilot"] = {
                "n": int(len(p)),
                "n_with_news": int(has.sum()),
                "wr_with": float(p.loc[has, "hit_1r"].mean()) if has.any() else None,
                "wr_without": float(p.loc[~has, "hit_1r"].mean()) if (~has).any() else None,
                "catalyst_types": (
                    p["catalyst_type"].value_counts().to_dict()
                    if "catalyst_type" in p.columns else {}
                ),
            }
            print(
                f"  Pilot: n={len(p)} news>0={has.sum()} "
                f"wr_with={result['pilot']['wr_with']} wr_without={result['pilot']['wr_without']}"
            )

    if social_miss > 0.95 and (result["htf"] is None or (result["htf"] or {}).get("n_with_news", 0) == 0):
        result["verdict"] = (
            "INCONCLUSIVE — full HTF bakeoff ran with news/social off. "
            "Need powered rebuild before catalyst claims. Pilot alone underpowered."
        )
    else:
        result["verdict"] = "Partial data — see tables; do not ship catalyst gates yet."
    print(f"  Verdict: {result['verdict']}")
    return result


def study5_tf_mix(df: pd.DataFrame) -> dict[str, Any]:
    print("\n=== STUDY 5: TF-mix proxies ===")
    work = df.copy()
    # A: baseline all_hours_admit
    work["admit_A"] = work["all_hours_admit"]
    # B: 1H + above SMA50
    work["admit_B"] = (
        (work["all_hours_admit"] == 1) & (work["close_vs_sma50"].astype(float) > 0)
    ).astype(int)
    # C: 1H + HH/HL 20d structure
    work["admit_C"] = (
        (work["all_hours_admit"] == 1) & (work["hh_hl_20"].astype(float) > 0)
    ).astype(int)
    # D: 1H + above SMA200 (longer daily context)
    work["admit_D"] = (
        (work["all_hours_admit"] == 1) & (work["close_vs_sma200"].astype(float) > 0)
    ).astype(int)
    # E: 1H + not deep under SMA200 (avoid multi-year broken)
    work["admit_E"] = (
        (work["all_hours_admit"] == 1) & (work["close_vs_sma200"].astype(float) > -0.15)
    ).astype(int)

    rows = []
    for name, acol in [
        ("A_1H_only", "admit_A"),
        ("B_1H_above_sma50", "admit_B"),
        ("C_1H_hh_hl_20", "admit_C"),
        ("D_1H_above_sma200", "admit_D"),
        ("E_1H_not_far_below_sma200", "admit_E"),
    ]:
        taken = simulate_slots(work, score_col="continuation_score_v1", admit_col=acol)
        m = _metrics(taken)
        cap = expander_capture(df, taken)
        n_admit = int((work[acol] == 1).sum())
        rows.append({"variant": name, "n_admit": n_admit, **m, **cap})
        print(
            f"  {name:28s} admit={n_admit:5d} taken={m['n']:.0f} wr={m['wr']:.1%} "
            f"exp={m['exp']:.3f} cap={cap['capture']:.1%}"
        )
    best = max(rows, key=lambda r: (r["exp"], r["capture"]))
    return {"rows": rows, "best_by_exp_then_cap": best["variant"]}


def study6_fitted(df: pd.DataFrame) -> dict[str, Any]:
    print("\n=== STUDY 6: Fitted logistic + importance ===")
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
    except ImportError:
        print("  sklearn missing — skip")
        return {"skipped": True, "reason": "sklearn not installed"}

    feats = [
        "hour", "peak_hour", "scan_score", "launch_score", "vol_ratio_20",
        "dist_20d_high_pct", "dist_52w_high_pct", "dist_20d_low_bounce",
        "ticker_prior_hit1r_rate", "ticker_prior_mfe_p50",
        "close_vs_sma50", "close_vs_sma200", "bar_range_pct", "dollar_vol_1h",
        "htf_score", "gap_pct", "hh_hl_20",
    ]
    work = df[df["all_hours_admit"] == 1].copy()
    # bar state one-hot soft
    for bs in ("yellow", "red", "green", "orange"):
        work[f"bs_{bs}"] = (work["bar_state"] == bs).astype(float)
        feats.append(f"bs_{bs}")

    available = [c for c in feats if c in work.columns]
    X = work[available].fillna(0.0).astype(float)
    y = work["hit_1r"].astype(int)
    train = work["signal_date"] < OOS_CUT
    test = ~train
    if train.sum() < 100 or test.sum() < 50:
        return {"skipped": True, "reason": "insufficient temporal split"}

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=500, class_weight="balanced")),
    ])
    pipe.fit(X.loc[train], y.loc[train])
    proba = pipe.predict_proba(X)[:, 1]
    work = work.copy()
    work["fit_score"] = proba

    # Slot sim on full df: merge fit_score
    full = df.copy()
    full["fit_score"] = 0.0
    full.loc[work.index, "fit_score"] = work["fit_score"]
    # non-admits stay 0
    taken_fit = simulate_slots(full, score_col="fit_score")
    taken_v1 = simulate_slots(df, score_col="continuation_score_v1")
    m_fit = _metrics(taken_fit)
    m_v1 = _metrics(taken_v1)
    cap_fit = expander_capture(df, taken_fit)
    cap_v1 = expander_capture(df, taken_v1)

    coef = pipe.named_steps["clf"].coef_[0]
    imp = sorted(
        [{"feature": f, "coef": float(c)} for f, c in zip(available, coef)],
        key=lambda x: abs(x["coef"]),
        reverse=True,
    )
    print("  Top coefficients (abs):")
    for row in imp[:12]:
        print(f"    {row['feature']:28s} {row['coef']:+.3f}")
    print(
        f"  Fitted slots: exp={m_fit['exp']:.3f} cap={cap_fit['capture']:.1%} | "
        f"v1: exp={m_v1['exp']:.3f} cap={cap_v1['capture']:.1%}"
    )
    return {
        "features": available,
        "importance": imp,
        "fitted_slots": {**m_fit, **cap_fit},
        "v1_slots": {**m_v1, **cap_v1},
        "beats_v1_exp": m_fit["exp"] > m_v1["exp"],
        "beats_v1_capture": cap_fit["capture"] > cap_v1["capture"],
    }


def write_report(all_results: dict[str, Any], runtime_s: float) -> None:
    s1 = all_results["study1"]
    s2 = all_results["study2"]
    s3 = all_results["study3"]
    s4 = all_results["study4"]
    s5 = all_results["study5"]
    s6 = all_results["study6"]

    lines = [
        "# EXP-0021 — Deep-edge studies (1→6)",
        "",
        f"**Generated:** {datetime.now(ET).isoformat()}",
        f"**Runtime:** {runtime_s:.1f}s",
        f"**Corpus:** `{CORPUS_PATH.name}` · OOS cut `{OOS_CUT}` · slots={SLOTS}",
        "",
        "## Executive findings",
        "",
    ]

    # Study 1 summary
    base = next(r for r in s1["rows"] if r["variant"] == "baseline_v1")
    worst = min(
        [r for r in s1["rows"] if r["variant"] != "baseline_v1"],
        key=lambda r: r["delta_exp"],
    )
    lines += [
        "### 1) Score-term ablation",
        f"- Baseline v1: n={base['n']:.0f} WR={base['wr']:.1%} exp={base['exp']:.3f} "
        f"capture={base['capture']:.1%}",
        f"- Largest **hurt** when removed (by Δexp): **`{worst['variant']}`** "
        f"(Δexp={worst['delta_exp']:+.3f}, Δcap={worst['delta_capture']:+.1%})",
        f"- Impact order (|Δexp|): {', '.join(s1['impact_by_abs_delta_exp'][:5])}",
        "",
        "| Variant | n | WR | Exp R | Capture | Δexp | Δcap |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in s1["rows"]:
        lines.append(
            f"| {r['variant']} | {r['n']:.0f} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['capture']:.1%} | {r['delta_exp']:+.3f} | {r['delta_capture']:+.1%} |"
        )

    lines += [
        "",
        "### 2) Lookback / room horizon",
        f"- Best by expectancy: **`{s2['best_by_exp']}`**",
        "",
        "| Variant | n | WR | Exp R | Capture | corr(room,mfe) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in s2["rows"]:
        corr = r["mfe_corr"]
        corr_s = f"{corr:.3f}" if corr == corr else "—"
        lines.append(
            f"| {r['variant']} | {r['n']:.0f} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['capture']:.1%} | {corr_s} |"
        )

    lines += [
        "",
        "### 3) Vol / scan on taken slots",
        f"- Admit corr(scan,mfe)={s3['admit_corr_scan_mfe']:.3f} · "
        f"corr(vol,mfe)={s3['admit_corr_vol_mfe']:.3f}",
        f"- Best policy by exp: **`{s3['best_policy_by_exp']}`**",
        "",
        "**Taken by scan**",
        "",
        "| Band | n | WR | Exp | med MFE | med MAE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in s3["taken_by_scan"]:
        lines.append(
            f"| {r['band']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['med_mfe']:.2%} | {r['med_mae']:.2%} |"
        )
    lines += [
        "",
        "**Taken by vol**",
        "",
        "| Band | n | WR | Exp | med MFE | med MAE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in s3["taken_by_vol"]:
        lines.append(
            f"| {r['band']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['med_mfe']:.2%} | {r['med_mae']:.2%} |"
        )
    lines += [
        "",
        "**Policies**",
        "",
        "| Policy | n | WR | Exp | Capture |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in s3["policies"]:
        lines.append(
            f"| {r['policy']} | {r['n']:.0f} | {r['wr']:.1%} | {r['exp']:.3f} | {r['capture']:.1%} |"
        )

    lines += [
        "",
        "### 4) News / catalyst",
        f"- {s4['verdict']}",
        f"- HTF social_missing rate: {s4['htf_social_missing_rate']:.0%}",
    ]
    if s4.get("pilot"):
        p = s4["pilot"]
        lines.append(
            f"- Pilot: n={p['n']} news>0={p['n_with_news']} "
            f"WR with={p['wr_with']} without={p['wr_without']}"
        )

    lines += [
        "",
        "### 5) TF-mix proxies",
        f"- Best by exp then capture: **`{s5['best_by_exp_then_cap']}`**",
        "",
        "| Variant | n_admit | taken | WR | Exp | Capture |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in s5["rows"]:
        lines.append(
            f"| {r['variant']} | {r['n_admit']} | {r['n']:.0f} | {r['wr']:.1%} | "
            f"{r['exp']:.3f} | {r['capture']:.1%} |"
        )

    lines += ["", "### 6) Fitted logistic + importance", ""]
    if s6.get("skipped"):
        lines.append(f"- Skipped: {s6.get('reason')}")
    else:
        lines.append(
            f"- Fitted slots exp={s6['fitted_slots']['exp']:.3f} "
            f"cap={s6['fitted_slots']['capture']:.1%} · "
            f"v1 exp={s6['v1_slots']['exp']:.3f} cap={s6['v1_slots']['capture']:.1%}"
        )
        lines.append(
            f"- Beats v1 on exp: **{s6['beats_v1_exp']}** · "
            f"on capture: **{s6['beats_v1_capture']}**"
        )
        lines += ["", "| Feature | coef |", "|---|---:|"]
        for row in s6["importance"][:15]:
            lines.append(f"| {row['feature']} | {row['coef']:+.3f} |")

    lines += [
        "",
        "## What this means for the deep-look design",
        "",
        "1. Equal 1H admit stays; rank terms should be kept only if ablation shows lift.",
        "2. Longer lookback (52w / SMA200) is a candidate upgrade over pure 20d room — see study 2.",
        "3. Soft-punishing hot scan/vol is **not** clearly justified — see study 3 policies.",
        "4. Catalyst claims stay blocked until a powered news corpus exists.",
        "5. Daily structure filters (SMA200 / HH-HL) change the list shape — trade capture vs quality.",
        "6. Fitted importance surfaces metrics beyond the heuristic weights.",
        "",
        "## FAIL / PASS labels",
        "",
    ]
    # Label each study
    lines.append(
        f"- Study 1 ablation: **PASS** (completed on corpus)"
    )
    lines.append(
        f"- Study 2 lookback: **PASS** (completed; best=`{s2['best_by_exp']}`)"
    )
    lines.append(
        f"- Study 3 vol/scan: **PASS** (completed; best policy=`{s3['best_policy_by_exp']}`)"
    )
    lines.append(
        f"- Study 4 news: **FAIL / INCONCLUSIVE** (data missing on HTF corpus)"
        if "INCONCLUSIVE" in s4["verdict"] else
        f"- Study 4 news: **PASS** (partial)"
    )
    lines.append(
        f"- Study 5 TF mix: **PASS** (completed; best=`{s5['best_by_exp_then_cap']}`)"
    )
    if s6.get("skipped"):
        lines.append("- Study 6 fitted: **FAIL** (skipped)")
    else:
        lines.append(
            "- Study 6 fitted: **PASS**"
            if (s6.get("beats_v1_exp") or s6.get("beats_v1_capture"))
            else "- Study 6 fitted: **FAIL** vs heuristic on both exp and capture"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


def main() -> int:
    t0 = time.time()
    print("=" * 64)
    print("EXP-0021 deep-edge studies 1→6")
    print("=" * 64)
    if not CORPUS_PATH.exists():
        print(f"Missing corpus: {CORPUS_PATH}")
        return 1
    df = pd.read_csv(CORPUS_PATH)
    print(f"Loaded {len(df)} rows · admits={(df['all_hours_admit']==1).sum()}")

    all_results = {
        "study1": study1_ablation(df),
        "study2": study2_lookback(df),
        "study3": study3_vol_scan(df),
        "study4": study4_news(df),
        "study5": study5_tf_mix(df),
        "study6": study6_fitted(df),
    }
    write_report(all_results, time.time() - t0)
    print(f"Done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
