"""
EXP-0021 Blind-spot #1 — Short interest + float (Modal).

Polygon:
  GET /stocks/v1/short-interest  (biweekly; settlement_date)
  GET /stocks/v1/short-volume    (daily short_volume_ratio)
  GET /v3/reference/tickers/{t}  (shares outstanding proxy)

Causal:
  SI: settlement_date + SI_PUBLISH_LAG_DAYS <= signal_date
  Short volume: date < signal_date (prior session)
  Shares: ticker-details snapshot (coverage proxy; flagged)

Research only until user approves live. Bakeoff vs continuation_score_v1.1.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0021/study_blindspot_01_short_float_modal.py
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import modal

APP_NAME = "q-alpha-exp021-bs01-si"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
if EXP_DIR.name == "EXP-0021":
    ROOT = EXP_DIR.parents[1]
else:
    ROOT = Path("/data")

CORPUS_LOCAL = EXP_DIR / "corpus_htf_universe_social.csv"
SCORE_LOCAL = ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"
OUT_MD = EXP_DIR / "STUDY_BLINDSPOT_01_SHORT_FLOAT.md"
OUT_JSON = EXP_DIR / "study_blindspot_01_short_float_metrics.json"
CACHE_LOCAL = EXP_DIR / "blindspot_01_si_cache.json"

app = modal.App(APP_NAME)

_image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "pandas", "numpy", "requests", "pytz", "tzdata",
])
if EXP_DIR.name == "EXP-0021":
    _image = (
        _image
        .add_local_file(str(CORPUS_LOCAL), remote_path="/data/corpus.csv")
        .add_local_file(str(SCORE_LOCAL), remote_path="/pkg/tsd_launch_score.py")
    )
image = _image
polygon_secret = modal.Secret.from_name("polygon-api-key")

SLOTS = 2
OOS_CUT = "2026-08-11"
SI_PUBLISH_LAG_DAYS = 10  # FINRA biweekly publish lag proxy
POLYGON = "https://api.polygon.io"

# Soft thresholds (aligned with quality_history_gate where possible)
HIGH_SI_PCT = 20.0
MID_SI_PCT_LO = 10.0
HIGH_DTC = 5.0
LOW_FLOAT = 15_000_000
HIGH_SV_RATIO = 45.0  # prior-day short volume % of volume


def ship_pass(challenger: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        challenger["exp"] >= baseline["exp"] - 1e-9
        and challenger["capture"] >= baseline["capture"] - 0.01
    ) or (
        challenger["exp"] > baseline["exp"] + 0.01
        and challenger["capture"] >= baseline["capture"] - 0.02
    )


def simulate_slots(df, *, score_col: str, admit_col: str = "all_hours_admit"):
    work = df[df[admit_col] == 1].copy()
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


def _get_json(url: str, params: dict, retries: int = 3) -> dict:
    import requests

    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(0.5 * (i + 1))
                continue
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    return {"_error": str(last)[:120]}


@app.function(image=image, secrets=[polygon_secret], timeout=180, max_containers=40)
def fetch_symbol_si(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch SI history + recent short volume + shares outstanding for one ticker."""
    sym = str(payload["symbol"]).upper()
    key = os.environ.get("POLYGON_API_KEY") or ""
    out: dict[str, Any] = {"symbol": sym, "si": [], "sv": [], "shares": None, "ok": 0}
    if not key:
        out["reason"] = "no_key"
        return out

    # Short interest (biweekly)
    si_rows = []
    params = {
        "ticker": sym,
        "limit": 50,
        "sort": "settlement_date",
        "order": "desc",
        "settlement_date.gte": "2025-01-01",
        "settlement_date.lte": "2026-09-10",
        "apiKey": key,
    }
    data = _get_json(f"{POLYGON}/stocks/v1/short-interest", params)
    si_rows.extend(data.get("results") or [])
    time.sleep(0.12)

    # Short volume — last ~120 calendar days covering corpus
    sv_rows = []
    params_sv = {
        "ticker": sym,
        "limit": 120,
        "sort": "date",
        "order": "desc",
        "date.gte": "2026-05-01",
        "date.lte": "2026-09-05",
        "apiKey": key,
    }
    data_sv = _get_json(f"{POLYGON}/stocks/v1/short-volume", params_sv)
    for row in data_sv.get("results") or []:
        sv_rows.append(
            {
                "date": str(row.get("date") or "")[:10],
                "short_volume_ratio": float(row.get("short_volume_ratio") or 0),
                "short_volume": float(row.get("short_volume") or 0),
                "total_volume": float(row.get("total_volume") or 0),
            }
        )
    time.sleep(0.12)

    # Shares outstanding proxy (point-in-time snapshot — coverage only)
    td = _get_json(f"{POLYGON}/v3/reference/tickers/{sym}", {"apiKey": key})
    res = td.get("results") or {}
    shares = res.get("share_class_shares_outstanding") or res.get(
        "weighted_shares_outstanding"
    )
    try:
        shares_f = float(shares) if shares is not None else None
    except (TypeError, ValueError):
        shares_f = None

    out["si"] = [
        {
            "settlement_date": str(r.get("settlement_date") or "")[:10],
            "short_interest": float(r.get("short_interest") or 0),
            "avg_daily_volume": float(r.get("avg_daily_volume") or 0),
            "days_to_cover": float(r.get("days_to_cover") or 0),
        }
        for r in si_rows
    ]
    out["sv"] = sv_rows
    out["shares"] = shares_f
    out["ok"] = 1
    return out


def pick_si(si_rows: list[dict], signal_date: str) -> dict[str, Any] | None:
    """Latest SI report causally available by signal_date (settlement + publish lag)."""
    if not si_rows:
        return None
    sig = datetime.strptime(signal_date[:10], "%Y-%m-%d").date()
    cutoff = sig - timedelta(days=SI_PUBLISH_LAG_DAYS)
    usable = []
    for r in si_rows:
        try:
            sd = datetime.strptime(str(r["settlement_date"])[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if sd <= cutoff:
            usable.append((sd, r))
    if not usable:
        return None
    usable.sort(key=lambda x: x[0])
    return usable[-1][1]


def pick_sv(sv_rows: list[dict], signal_date: str) -> dict[str, Any] | None:
    """Prior session short-volume ratio (date < signal_date)."""
    if not sv_rows:
        return None
    sig = signal_date[:10]
    usable = [r for r in sv_rows if str(r.get("date") or "") < sig]
    if not usable:
        return None
    usable.sort(key=lambda r: r["date"])
    return usable[-1]


def si_delta(si_rows: list[dict], chosen: dict[str, Any] | None) -> float | None:
    """Pct change in short_interest vs prior report (causal chain)."""
    if not chosen or not si_rows:
        return None
    ordered = sorted(si_rows, key=lambda r: r["settlement_date"])
    dates = [r["settlement_date"] for r in ordered]
    try:
        idx = dates.index(chosen["settlement_date"])
    except ValueError:
        return None
    if idx <= 0:
        return None
    prev = float(ordered[idx - 1]["short_interest"] or 0)
    cur = float(chosen["short_interest"] or 0)
    if prev <= 0:
        return None
    return (cur - prev) / prev


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 90, memory=4096)
def run_study() -> dict[str, Any]:
    """Attach SI/float features to corpus; strata + bakeoff vs v1.1."""
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
    admits = df[df["all_hours_admit"] == 1]
    symbols = sorted(admits["symbol"].unique().tolist())
    print(f"Fetching SI/SV for {len(symbols)} symbols…")

    payloads = [{"symbol": s} for s in symbols]
    fetched = list(fetch_symbol_si.map(payloads, order_outputs=True, return_exceptions=True))
    cache: dict[str, Any] = {}
    err = 0
    for item in fetched:
        if isinstance(item, Exception):
            err += 1
            continue
        cache[str(item["symbol"]).upper()] = item
    print(f"Fetched ok={len(cache)} errors={err}")

    # Score baseline
    scored = df.copy()
    scored["score_v11"] = [
        float(compute_continuation_score_v1_1(r.to_dict())) for _, r in scored.iterrows()
    ]

    # Attach features
    feat_rows = []
    for _, r in scored.iterrows():
        sym = str(r["symbol"]).upper()
        sd = str(r["signal_date"])[:10]
        pack = cache.get(sym) or {}
        si = pick_si(pack.get("si") or [], sd)
        sv = pick_sv(pack.get("sv") or [], sd)
        shares = pack.get("shares")
        si_shares = float(si["short_interest"]) if si else None
        dtc = float(si["days_to_cover"]) if si else None
        si_pct = None
        if si_shares is not None and shares and float(shares) > 0:
            si_pct = 100.0 * si_shares / float(shares)
        d_si = si_delta(pack.get("si") or [], si) if si else None
        svr = float(sv["short_volume_ratio"]) if sv else None
        feat_rows.append(
            {
                "si_ok": 1 if si else 0,
                "si_pct": si_pct,
                "days_to_cover": dtc,
                "si_delta": d_si,
                "float_shares": float(shares) if shares else None,
                "sv_ratio_prior": svr,
                "sv_ok": 1 if sv else 0,
            }
        )
    feat_df = pd.DataFrame(feat_rows)
    for c in feat_df.columns:
        scored[c] = feat_df[c].values

    admits_s = scored[scored["all_hours_admit"] == 1].copy()
    expanders = (
        admits_s.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    def strata_num(col: str, bins: list[tuple[str, float | None, float | None]]) -> list[dict]:
        rows = []
        sub = admits_s[admits_s[col].notna()].copy()
        for name, lo, hi in bins:
            m = pd.Series(True, index=sub.index)
            if lo is not None:
                m &= sub[col] >= lo
            if hi is not None:
                m &= sub[col] < hi
            g = sub[m]
            if len(g) < 20:
                continue
            rows.append(
                {
                    "bucket": name,
                    "n": int(len(g)),
                    "wr": float(g["hit_1r"].mean()),
                    "exp": float(g["r_multiple"].mean()),
                    "med_mfe": float(g["mfe"].median()),
                }
            )
        return rows

    strata = {
        "si_pct": strata_num(
            "si_pct",
            [
                ("si_<5", None, 5.0),
                ("si_5_10", 5.0, 10.0),
                ("si_10_20", 10.0, 20.0),
                ("si_20_35", 20.0, 35.0),
                ("si_>=35", 35.0, None),
            ],
        ),
        "days_to_cover": strata_num(
            "days_to_cover",
            [
                ("dtc_<1.5", None, 1.5),
                ("dtc_1.5_3", 1.5, 3.0),
                ("dtc_3_5", 3.0, 5.0),
                ("dtc_>=5", 5.0, None),
            ],
        ),
        "si_delta": strata_num(
            "si_delta",
            [
                ("si_falling", None, -0.05),
                ("si_flat", -0.05, 0.05),
                ("si_rising", 0.05, None),
            ],
        ),
        "float_shares": strata_num(
            "float_shares",
            [
                ("float_<15M", None, 15_000_000),
                ("float_15_50M", 15_000_000, 50_000_000),
                ("float_50_200M", 50_000_000, 200_000_000),
                ("float_>=200M", 200_000_000, None),
            ],
        ),
        "sv_ratio_prior": strata_num(
            "sv_ratio_prior",
            [
                ("svr_<30", None, 30.0),
                ("svr_30_45", 30.0, 45.0),
                ("svr_>=45", 45.0, None),
            ],
        ),
    }

    # Variants
    base = scored.copy()
    m_v11 = metrics(simulate_slots(base, score_col="score_v11"), expanders)
    oos = base[base["signal_date"] >= OOS_CUT]
    m_v11_oos = metrics(simulate_slots(oos, score_col="score_v11"), expanders)

    def with_adj(fn) -> tuple[dict, dict]:
        tmp = base.copy()
        tmp["score_x"] = [
            float(r["score_v11"]) + float(fn(r)) for _, r in tmp.iterrows()
        ]
        m = metrics(simulate_slots(tmp, score_col="score_x"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_x"),
            expanders,
        )
        return m, o

    def skip_mask(mask) -> tuple[dict, dict]:
        tmp = base.copy()
        tmp.loc[mask, "all_hours_admit"] = 0
        m = metrics(simulate_slots(tmp, score_col="score_v11"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_v11"),
            expanders,
        )
        return m, o

    # Soft: boost high SI% squeeze candidates
    def adj_squeeze(r) -> float:
        sp = r.get("si_pct")
        if sp is None or (isinstance(sp, float) and math.isnan(sp)):
            return 0.0
        if float(sp) >= HIGH_SI_PCT:
            return 12.0
        if float(sp) >= MID_SI_PCT_LO:
            return 5.0
        return 0.0

    # Soft: boost rising SI
    def adj_rising_si(r) -> float:
        d = r.get("si_delta")
        if d is None or (isinstance(d, float) and math.isnan(d)):
            return 0.0
        if float(d) >= 0.10:
            return 10.0
        if float(d) >= 0.05:
            return 5.0
        if float(d) <= -0.10:
            return -8.0
        return 0.0

    # Soft: demote high prior short-volume ratio (supply pressure)
    def adj_svr(r) -> float:
        v = r.get("sv_ratio_prior")
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0
        if float(v) >= HIGH_SV_RATIO:
            return -12.0
        if float(v) >= 35.0:
            return -5.0
        return 0.0

    # Soft: demote very low float (size-risk; literature size down)
    def adj_low_float(r) -> float:
        f = r.get("float_shares")
        if f is None or (isinstance(f, float) and math.isnan(f)):
            return 0.0
        if float(f) < LOW_FLOAT:
            return -10.0
        return 0.0

    # Combined literature-ish: squeeze boost + demote high SVR
    def adj_combo(r) -> float:
        return adj_squeeze(r) + adj_svr(r) + 0.5 * adj_rising_si(r)

    variants: dict[str, Any] = {"v11": {**m_v11, "oos": m_v11_oos, "pass_vs_v11": True}}

    for name, fn in [
        ("boost_high_si", adj_squeeze),
        ("boost_rising_si", adj_rising_si),
        ("demote_high_svr", adj_svr),
        ("demote_low_float", adj_low_float),
        ("combo_si_svr", adj_combo),
    ]:
        m, o = with_adj(fn)
        variants[name] = {**m, "oos": o, "pass_vs_v11": ship_pass(m, m_v11)}

    # Hard skips
    for name, mask in [
        ("skip_si_ge_35", (base["all_hours_admit"] == 1) & (base["si_pct"] >= 35)),
        ("skip_dtc_ge_5", (base["all_hours_admit"] == 1) & (base["days_to_cover"] >= HIGH_DTC)),
        ("skip_svr_ge_45", (base["all_hours_admit"] == 1) & (base["sv_ratio_prior"] >= HIGH_SV_RATIO)),
        ("skip_float_lt_15m", (base["all_hours_admit"] == 1) & (base["float_shares"] < LOW_FLOAT)),
        (
            "prefer_si_10_20_only",
            (base["all_hours_admit"] == 1)
            & ~(
                base["si_pct"].notna()
                & (base["si_pct"] >= 10)
                & (base["si_pct"] < 20)
            ),
        ),
    ]:
        m, o = skip_mask(mask.fillna(False))
        variants[name] = {**m, "oos": o, "pass_vs_v11": ship_pass(m, m_v11)}

    winners = [k for k, v in variants.items() if k != "v11" and v.get("pass_vs_v11")]
    best = None
    if winners:
        best = max(
            winners,
            key=lambda k: float(
                (variants[k].get("oos") or {}).get("exp")
                if isinstance(variants[k].get("oos"), dict)
                else variants[k]["exp"]
            ),
        )

    if best is None:
        rec, rec_text = "HOLD", (
            "No SI/float variant beat v1.1 on the ship gate. Keep live on v1.1; "
            "SI/float stay research-only unless you override."
        )
    elif str(best).startswith("skip_") or str(best).startswith("prefer_"):
        rec, rec_text = "HARD", f"Best passer: **{best}**. Hard filter only if you approve."
    else:
        rec, rec_text = "SOFT", f"Best passer: **{best}**. Soft score overlay only if you approve."

    coverage = {
        "symbols": len(symbols),
        "admit_rows": int(len(admits_s)),
        "si_coverage": float(admits_s["si_ok"].mean()),
        "sv_coverage": float(admits_s["sv_ok"].mean()),
        "si_pct_nonnull": float(admits_s["si_pct"].notna().mean()),
        "fetch_errors": err,
    }

    return {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "coverage": coverage,
        "strata": strata,
        "baseline_v11": m_v11,
        "bakeoff": variants,
        "winners": winners,
        "best_variant": best,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Shares outstanding from ticker-details snapshot (not perfect PIT float).",
            f"SI causal lag: settlement_date + {SI_PUBLISH_LAG_DAYS}d <= signal_date.",
            "Short volume uses prior calendar date < signal_date.",
        ],
        "cache": cache,
    }


@app.local_entrypoint()
def main():
    print("Launching Modal blind-spot #1: short interest + float")
    result = run_study.remote()
    if result.get("error"):
        print("ERROR", result)
        return

    CACHE_LOCAL.write_text(
        json.dumps({"cache": result.get("cache") or {}, "meta": {
            "generated": result.get("generated"),
            "coverage": result.get("coverage"),
        }}, indent=2, default=str),
        encoding="utf-8",
    )
    metrics_out = {k: v for k, v in result.items() if k != "cache"}
    OUT_JSON.write_text(json.dumps(metrics_out, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0021 — Blind-spot #1: Short interest + float",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Coverage",
        "",
        f"`{json.dumps(result.get('coverage'))}`",
        "",
        "## How this maps to Peak Hour",
        "",
        "- Live ranker today ignores SI/float (tags exist in quality gate but corpus bakeoff was empty).",
        "- Literature: high SI can fuel squeeze continuation; rising borrow/short volume often pressures longs;",
        "  ultra-low float raises variance (size down, don't auto-boost).",
        "- Causal SI uses biweekly FINRA settle + 10d publish lag; SV uses prior day ratio.",
        "",
        "## Strata (admits)",
        "",
    ]
    for key, title in [
        ("si_pct", "Short interest % of shares"),
        ("days_to_cover", "Days to cover"),
        ("si_delta", "SI change vs prior report"),
        ("float_shares", "Shares outstanding (proxy float)"),
        ("sv_ratio_prior", "Prior-day short volume ratio"),
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
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('exp') or 0):.3f} | {float(b.get('capture') or 0):.1%} | "
            f"{float(oos.get('exp') or 0):.3f} | {flag} |"
        )
    lines += [
        "",
        "## Decision needed",
        "",
        "- **ADD soft / ADD hard / HOLD research** — reply after reading this.",
        "- No live rewrite from this run.",
        "",
        "## Notes",
        "",
    ]
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "best_variant": result.get("best_variant"),
        "winners": result.get("winners"),
        "baseline_exp": (result.get("baseline_v11") or {}).get("exp"),
        "coverage": result.get("coverage"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2))
