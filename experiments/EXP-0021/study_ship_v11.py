"""
Ship gate: continuation_score v1 vs v1.1 + deeper lookback/news strata.

PASS rule (live paper before next open):
  v1.1 expectancy >= v1 expectancy
  AND v1.1 capture >= v1 capture - 1pp
  (or clearly better exp with capture not collapsing >2pp)

Usage:
  py -3 experiments/EXP-0021/study_ship_v11.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[1]
CAND = ROOT / "candidates"
sys.path.insert(0, str(CAND))
sys.path.insert(0, str(EXP_DIR))

from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    compute_continuation_score_v1,
    compute_continuation_score_v1_1,
)

ET = pytz.timezone("US/Eastern")
SLOTS = 2
OOS_CUT = "2026-08-11"
CORPUS = EXP_DIR / "corpus_htf_universe_social.csv"
if not CORPUS.exists():
    CORPUS = EXP_DIR / "corpus_htf_universe.csv"
OUT_MD = EXP_DIR / "STUDY_SHIP_V11.md"
OUT_JSON = EXP_DIR / "study_ship_v11_metrics.json"


def simulate_slots(df: pd.DataFrame, *, score_col: str) -> pd.DataFrame:
    work = df[df["all_hours_admit"] == 1].copy()
    if work.empty:
        return work
    work = work.sort_values(
        ["signal_date", "hour", score_col], ascending=[True, True, False],
    )
    return work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS)


def metrics(taken: pd.DataFrame, expanders: pd.DataFrame | None = None) -> dict[str, Any]:
    if taken.empty:
        return {"n": 0, "wr": 0.0, "exp": 0.0, "capture": 0.0}
    wr = float(taken["hit_1r"].mean())
    exp = float(taken["r_multiple"].mean())
    cap = 0.0
    if expanders is not None and len(expanders):
        keys = set(zip(taken["signal_date"].astype(str), taken["symbol"].astype(str)))
        ekeys = set(zip(expanders["signal_date"].astype(str), expanders["symbol"].astype(str)))
        hit = len(keys & ekeys)
        cap = hit / max(len(ekeys), 1)
    return {"n": int(len(taken)), "wr": wr, "exp": exp, "capture": cap}


def score_rows(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        d["score_v1"] = compute_continuation_score_v1(d)
        d["score_v11"] = compute_continuation_score_v1_1(d)
        # lookback variants for deeper study
        d20 = float(d.get("dist_20d_high_pct") or 0)
        d52 = float(d.get("dist_52w_high_pct") or 0)
        d["room_blend"] = 0.5 * (d20 + d52)
        # multi-year proxy: not far below SMA200 + 52w room
        sma = d.get("close_vs_sma200")
        try:
            sma_f = float(sma) if sma is not None and str(sma) != "nan" else None
        except (TypeError, ValueError):
            sma_f = None
        d["multi_year_ok"] = 1 if (sma_f is not None and sma_f > -0.15) else 0
        rows.append(d)
    return pd.DataFrame(rows)


def main() -> int:
    t0 = time.time()
    df = pd.read_csv(CORPUS)
    print(f"Loaded {len(df)} from {CORPUS.name}")
    scored = score_rows(df)

    # Expanders = top day_mfe per day among admits (same spirit as EXP-0021)
    admits = scored[scored["all_hours_admit"] == 1]
    expanders = (
        admits.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    taken_v1 = simulate_slots(scored, score_col="score_v1")
    taken_v11 = simulate_slots(scored, score_col="score_v11")
    m1 = metrics(taken_v1, expanders)
    m11 = metrics(taken_v11, expanders)

    # OOS
    oos = scored[scored["signal_date"].astype(str) >= OOS_CUT]
    oos_v1 = metrics(simulate_slots(oos, score_col="score_v1"), expanders)
    oos_v11 = metrics(simulate_slots(oos, score_col="score_v11"), expanders)

    ship_pass = (
        m11["exp"] >= m1["exp"] - 1e-9
        and m11["capture"] >= m1["capture"] - 0.01
    ) or (
        m11["exp"] > m1["exp"] + 0.01 and m11["capture"] >= m1["capture"] - 0.02
    )

    # Deeper: multi-year filter on admits then v1.1 rank
    my = scored.copy()
    my["admit_my"] = ((my["all_hours_admit"] == 1) & (my["multi_year_ok"] == 1)).astype(int)
    my_work = my[my["admit_my"] == 1]
    taken_my = (
        my_work.sort_values(["signal_date", "hour", "score_v11"], ascending=[True, True, False])
        .groupby(["signal_date", "hour"], as_index=False)
        .head(SLOTS)
    )
    m_my = metrics(taken_my, expanders)

    # News type strata on admits
    news_rows = []
    if "catalyst_type" in scored.columns:
        sub = scored[scored["all_hours_admit"] == 1].copy()
        sub["has_news"] = sub["news_velocity_24h"].fillna(0) > 0
        for label, mask in (
            ("no_news", ~sub["has_news"]),
            ("any_news", sub["has_news"]),
        ):
            s = sub[mask]
            news_rows.append({
                "bucket": label,
                "n": int(len(s)),
                "wr": float(s["hit_1r"].mean()) if len(s) else None,
                "exp": float(s["r_multiple"].mean()) if len(s) else None,
            })
        if "catalyst_type" in sub.columns:
            for ct, g in sub[sub["has_news"]].groupby(sub["catalyst_type"].fillna("none")):
                if len(g) < 15:
                    continue
                news_rows.append({
                    "bucket": f"type:{ct}",
                    "n": int(len(g)),
                    "wr": float(g["hit_1r"].mean()),
                    "exp": float(g["r_multiple"].mean()),
                })

    # Dilution risk check
    dil = None
    if "dilution_flag" in scored.columns:
        sub = scored[scored["all_hours_admit"] == 1]
        d1 = sub[sub["dilution_flag"].fillna(0).astype(int) == 1]
        d0 = sub[sub["dilution_flag"].fillna(0).astype(int) == 0]
        dil = {
            "n_dilution": int(len(d1)),
            "wr_dilution": float(d1["hit_1r"].mean()) if len(d1) else None,
            "wr_clean": float(d0["hit_1r"].mean()) if len(d0) else None,
            "exp_dilution": float(d1["r_multiple"].mean()) if len(d1) else None,
            "exp_clean": float(d0["r_multiple"].mean()) if len(d0) else None,
        }

    result = {
        "corpus": CORPUS.name,
        "runtime_sec": round(time.time() - t0, 1),
        "ship_pass": ship_pass,
        "v1": m1,
        "v11": m11,
        "oos_v1": oos_v1,
        "oos_v11": oos_v11,
        "multi_year_filter_v11": m_my,
        "news_strata": news_rows,
        "dilution": dil,
        "verdict": "SHIP v1.1" if ship_pass else "HOLD v1 — v1.1 failed gate",
    }

    lines = [
        "# EXP-0021 — Ship gate: continuation_score v1.1",
        "",
        f"**Generated:** {datetime.now(ET).isoformat()}",
        f"**Corpus:** `{CORPUS.name}` · slots={SLOTS}",
        f"**Verdict:** **{result['verdict']}**",
        "",
        "## v1 vs v1.1 (taken slots)",
        "",
        "| Version | n | WR | Exp R | Capture |",
        "|---|---:|---:|---:|---:|",
        f"| v1 | {m1['n']} | {m1['wr']:.1%} | {m1['exp']:.3f} | {m1['capture']:.1%} |",
        f"| **v1.1** | {m11['n']} | {m11['wr']:.1%} | {m11['exp']:.3f} | {m11['capture']:.1%} |",
        "",
        "### OOS (signal_date ≥ " + OOS_CUT + ")",
        "",
        f"- v1: n={oos_v1['n']} WR={oos_v1['wr']:.1%} exp={oos_v1['exp']:.3f}",
        f"- v1.1: n={oos_v11['n']} WR={oos_v11['wr']:.1%} exp={oos_v11['exp']:.3f}",
        "",
        "## Deeper: multi-year filter (SMA200 > -15%) + v1.1 rank",
        "",
        f"- n={m_my['n']} WR={m_my['wr']:.1%} exp={m_my['exp']:.3f} cap={m_my['capture']:.1%}",
        "- Ship filter only if it beats v1.1 without killing capture — see numbers.",
        "",
        "## Deeper: news strata (admits)",
        "",
    ]
    for nr in news_rows:
        lines.append(
            f"- `{nr['bucket']}` n={nr['n']} WR={nr['wr']} exp={nr['exp']}"
        )
    if dil:
        lines += [
            "",
            "## Dilution flag (admits)",
            "",
            f"- dilution n={dil['n_dilution']} WR={dil['wr_dilution']} exp={dil['exp_dilution']}",
            f"- clean WR={dil['wr_clean']} exp={dil['exp_clean']}",
        ]
    lines += [
        "",
        "## Live action",
        "",
        (
            "- **SHIPPED `continuation_score_v1.1` to live paper** "
            if ship_pass
            else "- Keep live on v1; do not promote."
        ),
        "- Do **not** add multi-year admit filter unless it beats v1.1 above.",
        "- News stays soft-rank; dilution/distress remain soft penalties.",
        "- X remains off.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    print(f"Wrote {OUT_MD}")
    return 0 if ship_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
