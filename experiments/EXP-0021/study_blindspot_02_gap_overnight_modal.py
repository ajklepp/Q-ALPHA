"""
EXP-0021 Blind-spot #2 — Gap + overnight quality (Modal).

Uses corpus fields (no extra API):
  gap_pct = (today_open - prior_close) / prior_close
  id_ret  = (signal_close - open) / open   # move since open at signal
  prior_close inferred; gap_fill if price already traded back through prior close

Literature map:
  - Mid gaps often continue; extreme gaps fade
  - Overnight-led moves differ from grind-from-open
  - Filled gaps weaken continuation for longs

Research only until user approves. Bakeoff vs continuation_score_v1.1.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0021/study_blindspot_02_gap_overnight_modal.py
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import modal

APP_NAME = "q-alpha-exp021-bs02-gap"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
ROOT = EXP_DIR.parents[1] if EXP_DIR.name == "EXP-0021" else Path("/data")

CORPUS_LOCAL = EXP_DIR / "corpus_htf_universe_social.csv"
SCORE_LOCAL = ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"
OUT_MD = EXP_DIR / "STUDY_BLINDSPOT_02_GAP_OVERNIGHT.md"
OUT_JSON = EXP_DIR / "study_blindspot_02_gap_overnight_metrics.json"

app = modal.App(APP_NAME)
_image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "pandas", "numpy", "pytz", "tzdata",
])
if EXP_DIR.name == "EXP-0021":
    _image = (
        _image
        .add_local_file(str(CORPUS_LOCAL), remote_path="/data/corpus.csv")
        .add_local_file(str(SCORE_LOCAL), remote_path="/pkg/tsd_launch_score.py")
    )
image = _image

SLOTS = 2
OOS_CUT = "2026-08-11"
# Gap bands (fraction, not percent)
GAP_MID_LO = 0.005   # 0.5%
GAP_MID_HI = 0.025   # 2.5%
GAP_BIG = 0.05       # 5%
GAP_NEG = -0.01      # -1%


def ship_pass(challenger: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        challenger["exp"] >= baseline["exp"] - 1e-9
        and challenger["capture"] >= baseline["capture"] - 0.01
    ) or (
        challenger["exp"] > baseline["exp"] + 0.01
        and challenger["capture"] >= baseline["capture"] - 0.02
    )


def simulate_slots(df, *, score_col: str):
    work = df[df["all_hours_admit"] == 1].copy()
    if work.empty:
        return work
    work = work.sort_values(
        ["signal_date", "hour", score_col], ascending=[True, True, False],
    )
    return work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS)


def metrics(taken, expanders=None) -> dict[str, Any]:
    if taken is None or getattr(taken, "empty", True):
        return {"n": 0, "wr": 0.0, "exp": 0.0, "capture": 0.0}
    wr = float(taken["hit_1r"].mean())
    exp = float(taken["r_multiple"].mean())
    cap = 0.0
    if expanders is not None and len(expanders):
        keys = set(zip(taken["signal_date"].astype(str), taken["symbol"].astype(str)))
        ekeys = set(
            zip(expanders["signal_date"].astype(str), expanders["symbol"].astype(str))
        )
        cap = len(keys & ekeys) / max(len(ekeys), 1)
    return {"n": int(len(taken)), "wr": wr, "exp": exp, "capture": cap}


def _f(x, default=None):
    try:
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def enrich_gap_features(row: dict[str, Any]) -> dict[str, Any]:
    """Derive gap / overnight / fill features from signal bar OHLC + gap_pct."""
    gap = _f(row.get("gap_pct"), 0.0) or 0.0
    o = _f(row.get("open"))
    h = _f(row.get("high"))
    low = _f(row.get("low"))
    c = _f(row.get("close"))
    vr = _f(row.get("vol_ratio_20"), 1.0) or 1.0

    id_ret = 0.0
    if o and o != 0 and c is not None:
        id_ret = (c - o) / o

    prior_close = None
    if o is not None and abs(1.0 + gap) > 1e-9:
        prior_close = o / (1.0 + gap)

    gap_filled = 0
    if prior_close is not None and low is not None and h is not None:
        if gap > 0 and low <= prior_close:
            gap_filled = 1
        elif gap < 0 and h >= prior_close:
            gap_filled = 1

    # Overnight share of total move from prior close to signal close
    total_from_prior = None
    overnight_share = None
    if prior_close and prior_close != 0 and c is not None:
        total_from_prior = (c - prior_close) / prior_close
        if abs(total_from_prior) > 1e-6:
            overnight_share = gap / total_from_prior
            # clamp wild ratios
            overnight_share = max(-2.0, min(2.0, overnight_share))

    overnight_led = 0
    if abs(gap) >= abs(id_ret) and abs(gap) >= 0.005:
        overnight_led = 1

    gap_abs = abs(gap)
    return {
        "gap_pct": gap,
        "gap_abs": gap_abs,
        "id_ret": id_ret,
        "prior_close": prior_close,
        "gap_filled": gap_filled,
        "total_from_prior": total_from_prior if total_from_prior is not None else 0.0,
        "overnight_share": overnight_share if overnight_share is not None else 0.0,
        "overnight_led": overnight_led,
        "gap_vol_ok": 1 if (gap_abs >= GAP_MID_LO and vr >= 1.2) else 0,
        "vol_ratio_20": vr,
    }


@app.function(image=image, timeout=60 * 45, memory=4096)
def run_study() -> dict[str, Any]:
    """Strata + bakeoff gap/overnight variants vs v1.1."""
    import pandas as pd
    import pytz
    import sys

    t0 = time.time()
    ET = pytz.timezone("America/New_York")
    sys.path.insert(0, "/pkg")
    from tsd_launch_score import compute_continuation_score_v1_1

    df = pd.read_csv("/data/corpus.csv")
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["signal_date"] = df["signal_date"].astype(str)

    scored_rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        d.update(enrich_gap_features(d))
        d["score_v11"] = float(compute_continuation_score_v1_1(d))
        scored_rows.append(d)
    scored = pd.DataFrame(scored_rows)

    admits = scored[scored["all_hours_admit"] == 1].copy()
    expanders = (
        admits.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    def strata_num(col: str, bins: list[tuple[str, float | None, float | None]]) -> list[dict]:
        rows = []
        sub = admits[admits[col].notna()].copy()
        for name, lo, hi in bins:
            m = pd.Series(True, index=sub.index)
            if lo is not None:
                m &= sub[col] >= lo
            if hi is not None:
                m &= sub[col] < hi
            g = sub[m]
            if len(g) < 25:
                continue
            rows.append({
                "bucket": name,
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()),
                "exp": float(g["r_multiple"].mean()),
                "med_mfe": float(g["mfe"].median()),
            })
        return rows

    def strata_bool(col: str) -> list[dict]:
        rows = []
        for val, g in admits.groupby(col):
            if len(g) < 25:
                continue
            rows.append({
                "bucket": str(val),
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()),
                "exp": float(g["r_multiple"].mean()),
                "med_mfe": float(g["mfe"].median()),
            })
        return rows

    strata = {
        "gap_pct": strata_num(
            "gap_pct",
            [
                ("gap_<=-2%", None, -0.02),
                ("gap_-2_-0.5%", -0.02, -0.005),
                ("gap_flat", -0.005, 0.005),
                ("gap_0.5_2.5%", 0.005, 0.025),
                ("gap_2.5_5%", 0.025, 0.05),
                ("gap_>=5%", 0.05, None),
            ],
        ),
        "id_ret": strata_num(
            "id_ret",
            [
                ("id_red_<=-1%", None, -0.01),
                ("id_flat", -0.01, 0.01),
                ("id_green_1_3%", 0.01, 0.03),
                ("id_green_>=3%", 0.03, None),
            ],
        ),
        "gap_filled": strata_bool("gap_filled"),
        "overnight_led": strata_bool("overnight_led"),
        "gap_vol_ok": strata_bool("gap_vol_ok"),
    }

    base = scored.copy()
    m_v11 = metrics(simulate_slots(base, score_col="score_v11"), expanders)
    oos = base[base["signal_date"] >= OOS_CUT]
    m_v11_oos = metrics(simulate_slots(oos, score_col="score_v11"), expanders)

    def with_adj(fn):
        tmp = base.copy()
        tmp["score_x"] = [float(r["score_v11"]) + float(fn(r)) for _, r in tmp.iterrows()]
        m = metrics(simulate_slots(tmp, score_col="score_x"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_x"),
            expanders,
        )
        return m, o

    def skip_mask(mask):
        tmp = base.copy()
        tmp.loc[mask.fillna(False), "all_hours_admit"] = 0
        m = metrics(simulate_slots(tmp, score_col="score_v11"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_v11"),
            expanders,
        )
        return m, o

    def adj_mid_gap(r) -> float:
        g = _f(r.get("gap_pct"), 0.0) or 0.0
        if GAP_MID_LO <= g < GAP_MID_HI:
            return 10.0
        if g >= GAP_BIG:
            return -15.0
        if g <= -0.02:
            return -10.0
        return 0.0

    def adj_overnight_led(r) -> float:
        if int(r.get("overnight_led") or 0) == 1 and (_f(r.get("gap_pct"), 0) or 0) > 0:
            return 8.0
        return 0.0

    def adj_filled_demote(r) -> float:
        if int(r.get("gap_filled") or 0) == 1 and (_f(r.get("gap_pct"), 0) or 0) > 0.005:
            return -15.0
        return 0.0

    def adj_gap_vol(r) -> float:
        if int(r.get("gap_vol_ok") or 0) == 1 and (_f(r.get("gap_pct"), 0) or 0) > 0:
            return 8.0
        return 0.0

    def adj_combo(r) -> float:
        return adj_mid_gap(r) + adj_filled_demote(r) + 0.5 * adj_gap_vol(r)

    variants: dict[str, Any] = {
        "v11": {**m_v11, "oos": m_v11_oos, "pass_vs_v11": True},
    }
    for name, fn in [
        ("boost_mid_gap", adj_mid_gap),
        ("boost_overnight_led", adj_overnight_led),
        ("demote_filled_gap", adj_filled_demote),
        ("boost_gap_with_vol", adj_gap_vol),
        ("combo_gap_quality", adj_combo),
    ]:
        m, o = with_adj(fn)
        variants[name] = {**m, "oos": o, "pass_vs_v11": ship_pass(m, m_v11)}

    for name, mask in [
        ("skip_gap_ge_5pct", (base["all_hours_admit"] == 1) & (base["gap_pct"] >= GAP_BIG)),
        ("skip_gap_le_neg1", (base["all_hours_admit"] == 1) & (base["gap_pct"] <= GAP_NEG)),
        ("skip_filled_up_gap", (base["all_hours_admit"] == 1) & (base["gap_filled"] == 1) & (base["gap_pct"] > 0.005)),
        ("skip_not_mid_gap", (base["all_hours_admit"] == 1) & ~(
            (base["gap_pct"] >= GAP_MID_LO) & (base["gap_pct"] < GAP_MID_HI)
        )),
        ("prefer_overnight_led_only", (base["all_hours_admit"] == 1) & (base["overnight_led"] != 1)),
    ]:
        m, o = skip_mask(mask)
        variants[name] = {**m, "oos": o, "pass_vs_v11": ship_pass(m, m_v11)}

    winners = [k for k, v in variants.items() if k != "v11" and v.get("pass_vs_v11")]
    # Drop vacuous winners (identical to baseline)
    real_winners = []
    for k in winners:
        v = variants[k]
        if (
            abs(float(v["exp"]) - float(m_v11["exp"])) < 1e-12
            and int(v["n"]) == int(m_v11["n"])
            and abs(float(v["capture"]) - float(m_v11["capture"])) < 1e-12
        ):
            continue
        real_winners.append(k)

    best = None
    if real_winners:
        best = max(
            real_winners,
            key=lambda k: float((variants[k].get("oos") or {}).get("exp", variants[k]["exp"])),
        )

    if best is None:
        rec, rec_text = "HOLD", (
            "No gap/overnight variant truly beat v1.1 on the ship gate. "
            "Keep live on v1.1; gap features stay research-only unless you override."
        )
    elif str(best).startswith("skip_") or str(best).startswith("prefer_"):
        rec, rec_text = "HARD", f"Best real passer: **{best}**. Hard filter only if you approve."
    else:
        rec, rec_text = "SOFT", f"Best real passer: **{best}**. Soft overlay only if you approve."

    return {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "admit_rows": int(len(admits)),
        "gap_filled_rate": float(admits["gap_filled"].mean()),
        "overnight_led_rate": float(admits["overnight_led"].mean()),
        "strata": strata,
        "baseline_v11": m_v11,
        "bakeoff": variants,
        "winners_raw": winners,
        "winners": real_winners,
        "vacuous_winners": [k for k in winners if k not in real_winners],
        "best_variant": best,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "gap_pct already in corpus; overnight_led = |gap|>=|id_ret| and |gap|>=0.5%.",
            "gap_filled = up-gap traded back to prior close (or down-gap to prior) by signal bar.",
            "Vacuous identical-to-v11 winners excluded from recommendation.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching Modal blind-spot #2: gap + overnight")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0021 — Blind-spot #2: Gap + overnight",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Coverage",
        "",
        f"- admits: {result.get('admit_rows')}",
        f"- gap_filled_rate: {float(result.get('gap_filled_rate') or 0):.1%}",
        f"- overnight_led_rate: {float(result.get('overnight_led_rate') or 0):.1%}",
        "",
        "## How this maps to Peak Hour",
        "",
        "- `gap_pct` is already computed but not a first-class v1.1 term.",
        "- Literature: mid gaps + volume continue; filled / extreme gaps fade;",
        "  overnight-led vs grind-from-open can differ.",
        "",
        "## Strata (admits)",
        "",
    ]
    for key, title in [
        ("gap_pct", "Gap %"),
        ("id_ret", "Intraday return since open"),
        ("gap_filled", "Gap already filled by signal"),
        ("overnight_led", "Overnight-led"),
        ("gap_vol_ok", "Gap + vol confirmation"),
    ]:
        lines += [f"### {title}", "", "| Bucket | n | WR | Exp R | med MFE |", "|---|---:|---:|---:|---:|"]
        for r in (result.get("strata") or {}).get(key) or []:
            lines.append(
                f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
            )
        lines.append("")

    lines += [
        "## Bakeoff vs v1.1",
        "",
        "| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name, b in (result.get("bakeoff") or {}).items():
        oos = b.get("oos") if isinstance(b.get("oos"), dict) else {}
        flag = "—" if name == "v11" else ("YES" if b.get("pass_vs_v11") else "no")
        if name in (result.get("vacuous_winners") or []):
            flag = "vacuous"
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('exp') or 0):.3f} | {float(b.get('capture') or 0):.1%} | "
            f"{float(oos.get('exp') or 0):.3f} | {flag} |"
        )
    lines += [
        "",
        "## Decision needed",
        "",
        "- Reply: **ADD soft / ADD hard / HOLD**",
        "- No live rewrite from this run.",
        "",
        "## Notes",
        "",
    ]
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    if result.get("vacuous_winners"):
        lines.append(f"- Vacuous winners ignored: `{result.get('vacuous_winners')}`")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "best_variant": result.get("best_variant"),
        "winners": result.get("winners"),
        "vacuous_winners": result.get("vacuous_winners"),
        "baseline_exp": (result.get("baseline_v11") or {}).get("exp"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2))
