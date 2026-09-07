"""
EXP-0025 — Sub-1H vol clustering + fractal as continuation vs failure.

Hypothesis (user):
  Volatility clustering is the *predictive* piece of fat-tail / power-law world.
  Fractal structure *below* 1H may show continuation vs failure signatures.

Design:
  - Peak Hour-style admits from EXP-0021 corpus (all_hours_admit)
  - Polygon 5-minute bars in the lookback *before* signal_ts only (no look-ahead)
  - Features: RV ratio (short/long), ACF of r^2, DFA Hurst on 5m, RV slope
  - Labels: hit_1r, day_mfe
  - Strata + soft score bakeoff vs flat baseline on admits

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0025/study_sub1h_vol_fractal_modal.py
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import modal

APP_NAME = "q-alpha-exp025-sub1h-vol-fractal"
# Paths: Modal imports this file under /root — keep module-level paths safe.
_REPO = Path(r"C:\Users\ajkle\Documents\Q-ALPHA")
EXP_DIR = _REPO / "experiments" / "EXP-0025"
CORPUS = _REPO / "experiments" / "EXP-0021" / "corpus_htf_universe.csv"
OUT_MD = EXP_DIR / "results.md"
OUT_JSON = EXP_DIR / "study_sub1h_vol_fractal_metrics.json"

app = modal.App(APP_NAME)
_image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "numpy", "pandas", "requests", "pytz", "tzdata",
])
if CORPUS.exists():
    _image = _image.add_local_file(str(CORPUS), remote_path="/data/corpus.csv")
image = _image
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
ET = ZoneInfo("America/New_York")
SAMPLE_N = 1400
LOOKBACK_MIN = 180  # 3h of 5m context before signal
SLEEP = 0.12
OOS_CUT = "2026-08-11"


def _get(url: str, params: dict, retries: int = 4) -> dict:
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=45)
            if r.status_code == 429:
                time.sleep(0.7 * (i + 1))
                continue
            if r.status_code in (403, 404):
                return {"_status": r.status_code}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    return {"_error": str(last)[:160]}


def hurst_dfa(rets, min_s: int = 6) -> float:
    import numpy as np
    x = np.asarray(rets, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 24:
        return float("nan")
    y = np.cumsum(x - x.mean())
    sizes, log_s, log_f = [], [], []
    s = min_s
    while s <= n // 4:
        sizes.append(s)
        s *= 2
    if len(sizes) < 2:
        return float("nan")
    for sz in sizes:
        nseg = n // sz
        if nseg < 2:
            continue
        rms = []
        for i in range(nseg):
            seg = y[i * sz:(i + 1) * sz]
            t = np.arange(sz, dtype=float)
            coef = np.polyfit(t, seg, 1)
            trend = coef[0] * t + coef[1]
            rms.append(float(np.sqrt(np.mean((seg - trend) ** 2))))
        f = float(np.mean(rms))
        if f > 0:
            log_s.append(math.log(sz))
            log_f.append(math.log(f))
    if len(log_s) < 2:
        return float("nan")
    X, Y = np.array(log_s), np.array(log_f)
    xm, ym = X.mean(), Y.mean()
    den = float(((X - xm) ** 2).sum())
    if den <= 0:
        return float("nan")
    return float(((X - xm) * (Y - ym)).sum() / den)


def feats_from_5m(closes: list[float]) -> dict[str, float]:
    import numpy as np
    c = np.asarray(closes, dtype=float)
    if len(c) < 20:
        return {"ok": 0.0}
    r = np.diff(np.log(c))
    r = r[np.isfinite(r)]
    if len(r) < 16:
        return {"ok": 0.0}
    # realized vol short (last 6 bars ~30m) vs long (full window)
    short_n = min(6, len(r) // 2)
    rv_s = float(np.sqrt(np.mean(r[-short_n:] ** 2)))
    rv_l = float(np.sqrt(np.mean(r ** 2)))
    rv_ratio = rv_s / rv_l if rv_l > 1e-12 else float("nan")
    # vol clustering: lag-1 ACF of squared returns
    z = r ** 2
    z = z - z.mean()
    if float((z[:-1] ** 2).sum()) > 0:
        acf1 = float((z[1:] * z[:-1]).sum() / (z[:-1] ** 2).sum())
    else:
        acf1 = float("nan")
    # RV slope: second half vs first half
    mid = len(r) // 2
    rv1 = float(np.sqrt(np.mean(r[:mid] ** 2))) if mid > 2 else float("nan")
    rv2 = float(np.sqrt(np.mean(r[mid:] ** 2))) if len(r) - mid > 2 else float("nan")
    rv_slope = (rv2 / rv1 - 1.0) if (rv1 == rv1 and rv1 > 1e-12) else float("nan")
    H = hurst_dfa(r)
    return {
        "ok": 1.0,
        "n_5m": float(len(c)),
        "rv_ratio": rv_ratio,
        "acf_r2": acf1,
        "rv_slope": rv_slope,
        "hurst_5m": H,
        "rv_long": rv_l,
    }


@app.function(image=image, secrets=[polygon_secret], timeout=120, max_containers=60)
def fetch_pre_signal_5m(payload: dict) -> dict[str, Any]:
    """5m bars strictly before signal_ts."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    sym = str(payload["symbol"]).upper()
    out = {**payload, "feats": {"ok": 0.0}, "ok": 0}
    if not key:
        return out
    # parse signal
    ts = str(payload["signal_ts"]).replace(" ", "T")
    try:
        # e.g. 2026-06-08T09:00:00-04:00
        sig = datetime.fromisoformat(ts)
        if sig.tzinfo is None:
            sig = sig.replace(tzinfo=ET)
    except Exception:
        return out
    end = sig.astimezone(timezone.utc)
    start = end - timedelta(minutes=LOOKBACK_MIN)
    # Polygon path dates
    d0 = start.strftime("%Y-%m-%d")
    d1 = end.strftime("%Y-%m-%d")
    ag = _get(
        f"{POLYGON}/v2/aggs/ticker/{sym}/range/5/minute/{d0}/{d1}",
        {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP)
    closes = []
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    for b in ag.get("results") or []:
        try:
            t = int(b["t"])
            if start_ms <= t < end_ms:  # strictly before signal
                closes.append(float(b["c"]))
        except Exception:
            continue
    feats = feats_from_5m(closes)
    out["feats"] = feats
    out["ok"] = 1 if feats.get("ok") == 1.0 else 0
    out["n_bars"] = len(closes)
    return out


def strata(df, col: str, q: int = 4):
    import numpy as np
    import pandas as pd
    work = df[np.isfinite(df[col])].copy()
    if work.empty:
        return []
    try:
        work["bucket"] = pd.qcut(work[col], q=q, duplicates="drop")
    except Exception:
        return []
    rows = []
    for b, g in work.groupby("bucket", observed=True):
        rows.append({
            "bucket": str(b),
            "n": int(len(g)),
            "wr": float(g["hit_1r"].mean()),
            "mfe": float(g["day_mfe"].mean()),
            "med_mfe": float(g["day_mfe"].median()),
        })
    return rows


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 40)
def run_study() -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    t0 = time.time()
    df = pd.read_csv("/data/corpus.csv")
    df = df[df["all_hours_admit"] == 1].copy()
    df["signal_date"] = df["signal_date"].astype(str)
    # prefer buy/early rows
    df = df[(df["buy_signal"] == True) | (df["early_bull"] == True)].copy()
    # sample for API budget
    if len(df) > SAMPLE_N:
        df = df.sample(SAMPLE_N, random_state=42)
    payloads = df[["symbol", "signal_ts", "signal_date", "hour", "hit_1r", "day_mfe", "scan_score"]].to_dict("records")
    print(f"Fetching 5m pre-signal for {len(payloads)} admits…")
    fetched = list(fetch_pre_signal_5m.map(payloads))
    rows = []
    for r in fetched:
        f = r.get("feats") or {}
        if not r.get("ok"):
            continue
        rows.append({
            "symbol": r["symbol"],
            "signal_ts": r["signal_ts"],
            "signal_date": r["signal_date"],
            "hour": r["hour"],
            "hit_1r": int(r["hit_1r"]),
            "day_mfe": float(r["day_mfe"]),
            "scan_score": float(r.get("scan_score") or 0),
            **{k: float(f[k]) for k in ("rv_ratio", "acf_r2", "rv_slope", "hurst_5m", "rv_long", "n_5m") if k in f},
        })
    panel = pd.DataFrame(rows)
    coverage = {"requested": len(payloads), "ok": int(len(panel)), "pct": round(len(panel) / max(len(payloads), 1), 3)}

    # Binary signatures (continuation-friendly hypotheses)
    # 1) vol clustering alive: high acf_r2
    # 2) vol expanding into signal: rv_ratio>1 and rv_slope>0
    # 3) fractal persist: hurst_5m > 0.55
    # 4) fractal chop/fail: hurst_5m < 0.45
    if not panel.empty:
        med_acf = float(panel["acf_r2"].median())
        med_rv = float(panel["rv_ratio"].median())
        panel["sig_cluster"] = (panel["acf_r2"] >= med_acf).astype(int)
        panel["sig_expand"] = ((panel["rv_ratio"] > 1.0) & (panel["rv_slope"] > 0)).astype(int)
        panel["sig_persist"] = (panel["hurst_5m"] > 0.55).astype(int)
        panel["sig_chop"] = (panel["hurst_5m"] < 0.45).astype(int)
        panel["sig_alive"] = ((panel["sig_cluster"] == 1) & (panel["sig_expand"] == 1)).astype(int)
        panel["sig_dead"] = ((panel["sig_chop"] == 1) | ((panel["rv_slope"] < 0) & (panel["rv_ratio"] < 1))).astype(int)

    def rate(mask):
        g = panel[mask] if len(panel) else panel
        if g is None or len(g) == 0:
            return {"n": 0, "wr": 0.0, "mfe": 0.0}
        return {"n": int(len(g)), "wr": float(g["hit_1r"].mean()), "mfe": float(g["day_mfe"].mean())}

    base = rate(np.ones(len(panel), dtype=bool)) if len(panel) else {"n": 0, "wr": 0.0, "mfe": 0.0}
    signatures = {
        "baseline_admits": base,
        "vol_cluster_high": rate(panel["sig_cluster"] == 1) if len(panel) else base,
        "vol_cluster_low": rate(panel["sig_cluster"] == 0) if len(panel) else base,
        "vol_expanding": rate(panel["sig_expand"] == 1) if len(panel) else base,
        "hurst_persist": rate(panel["sig_persist"] == 1) if len(panel) else base,
        "hurst_chop": rate(panel["sig_chop"] == 1) if len(panel) else base,
        "alive_cluster_expand": rate(panel["sig_alive"] == 1) if len(panel) else base,
        "dead_chop_or_dying_vol": rate(panel["sig_dead"] == 1) if len(panel) else base,
    }

    # Soft score: boost alive, demote dead — compare taken top-2 per date-hour using scan_score ± adjustments
    # Approximate: rank by scan_score + 8*alive - 8*dead within each signal_date,hour; take top 2; metrics
    def slot_metrics(score_col: str):
        if panel.empty:
            return {"n": 0, "wr": 0.0, "mfe": 0.0, "oos_wr": 0.0}
        work = panel.copy()
        work = work.sort_values(["signal_date", "hour", score_col], ascending=[True, True, False])
        taken = work.groupby(["signal_date", "hour"], as_index=False).head(2)
        oos = taken[taken["signal_date"] >= OOS_CUT]
        return {
            "n": int(len(taken)),
            "wr": float(taken["hit_1r"].mean()),
            "mfe": float(taken["day_mfe"].mean()),
            "oos_n": int(len(oos)),
            "oos_wr": float(oos["hit_1r"].mean()) if len(oos) else 0.0,
            "oos_mfe": float(oos["day_mfe"].mean()) if len(oos) else 0.0,
        }

    if not panel.empty:
        panel["score_base"] = panel["scan_score"].fillna(0)
        panel["score_alive"] = panel["score_base"] + 10.0 * panel["sig_alive"] - 10.0 * panel["sig_dead"]
        panel["score_hurst"] = panel["score_base"] + 8.0 * panel["sig_persist"] - 8.0 * panel["sig_chop"]
        panel["score_cluster"] = panel["score_base"] + 8.0 * panel["sig_cluster"] - 6.0 * (1 - panel["sig_cluster"])
        panel["score_combo"] = (
            panel["score_base"]
            + 8.0 * panel["sig_alive"]
            + 5.0 * panel["sig_persist"]
            - 10.0 * panel["sig_dead"]
        )
    bakeoff = {
        "base_scan": slot_metrics("score_base") if len(panel) else {},
        "boost_alive_vol": slot_metrics("score_alive") if len(panel) else {},
        "boost_hurst": slot_metrics("score_hurst") if len(panel) else {},
        "boost_cluster": slot_metrics("score_cluster") if len(panel) else {},
        "boost_combo": slot_metrics("score_combo") if len(panel) else {},
    }

    feat_strata = {
        "acf_r2": strata(panel, "acf_r2") if len(panel) else [],
        "rv_ratio": strata(panel, "rv_ratio") if len(panel) else [],
        "rv_slope": strata(panel, "rv_slope") if len(panel) else [],
        "hurst_5m": strata(panel, "hurst_5m") if len(panel) else [],
    }

    def ship(chal, base_m):
        if not chal or not base_m or chal.get("n", 0) < 30:
            return False
        # Require material lift — ties with baseline are vacuous
        return (
            chal["wr"] > base_m["wr"] + 0.005
            and chal["mfe"] >= base_m["mfe"] - 1e-6
        ) or (
            chal["wr"] >= base_m["wr"] - 1e-9
            and chal["mfe"] > base_m["mfe"] + 0.002
        )

    base_m = bakeoff.get("base_scan") or {}
    winners_raw = [k for k, v in bakeoff.items() if k != "base_scan" and ship(v, base_m)]
    # Drop vacuous: score identical in practice to base (hurst often no-ops if H all-NaN)
    winners = []
    for k in winners_raw:
        b, base = bakeoff[k], base_m
        if abs(float(b.get("wr") or 0) - float(base.get("wr") or 0)) < 1e-9 and abs(
            float(b.get("mfe") or 0) - float(base.get("mfe") or 0)
        ) < 1e-9:
            continue
        winners.append(k)

    alive = signatures.get("alive_cluster_expand") or {}
    dead = signatures.get("dead_chop_or_dying_vol") or {}
    expand = signatures.get("vol_expanding") or {}
    lift = {
        "wr_gap_alive_minus_dead": round(float(alive.get("wr", 0) - dead.get("wr", 0)), 4),
        "mfe_gap_alive_minus_dead": round(float(alive.get("mfe", 0) - dead.get("mfe", 0)), 4),
        "alive_n": alive.get("n", 0),
        "dead_n": dead.get("n", 0),
        "expand_n": expand.get("n", 0),
        "expand_wr": expand.get("wr", 0),
    }

    # Evidence hierarchy
    strata_rv = (feat_strata.get("rv_ratio") or [])
    rv_mono = False
    if len(strata_rv) >= 3:
        wrs = [float(r["wr"]) for r in strata_rv]
        rv_mono = wrs[-1] >= wrs[0] + 0.03

    if winners and (lift["alive_n"] >= 30 or rv_mono):
        rec = "PROMISING"
        rec_text = (
            f"Sub-1H vol-alive signatures help continuation rank: winners={winners}. "
            f"Alive WR {alive.get('wr', 0):.1%} (n={alive.get('n')}) vs dead {dead.get('wr', 0):.1%}. "
            "Best practical tell: expanding 5m vol into the 1H signal. Research-only soft weight."
        )
    elif rv_mono or (lift["wr_gap_alive_minus_dead"] >= 0.05 and lift["alive_n"] >= 25):
        rec = "WEAK"
        rec_text = (
            "Vol clustering / expanding tape shows continuation lift in strata, "
            "but soft-score ship is thin or sample of 'alive' is small. Hold for live; diagnostic keep."
        )
    else:
        rec = "FAIL"
        rec_text = (
            "Sub-1H vol clustering / Hurst did not show a reliable continuation vs failure edge."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "coverage": coverage,
        "baseline": base,
        "signatures": signatures,
        "lift": lift,
        "strata": feat_strata,
        "bakeoff": bakeoff,
        "winners": winners,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "5m bars strictly before signal_ts (no look-ahead into the signal hour).",
            "Vol clustering ≈ ACF(r^2); expanding tape ≈ rv_ratio>1 & rv_slope>0.",
            "Fractal persist H>0.55 / chop H<0.45 on pre-signal 5m returns.",
            "Continuation label = hit_1r / day_mfe from EXP-0021 corpus.",
            "Long-only research. No Peak Hour live edits.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching EXP-0025 sub-1H vol clustering + fractal continuation study")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# EXP-0025 — Sub-1H vol clustering + fractal (continuation vs failure)",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Coverage",
        f"`{json.dumps(result.get('coverage'))}`",
        "",
        "## Simple idea",
        "",
        "- **Volatility clustering** = big wiggles tend to follow big wiggles (tape is 'alive').",
        "- That helps ask *continuation vs failure* after a 1H signal — not naked up/down prediction.",
        "- **Below 1H fractals** = is the 5m path sticky (persist) or choppy (fail risk)?",
        "",
        "## Signatures vs hit-1R",
        "",
        "| Signature | n | Hit-1R | Mean MFE |",
        "|---|---:|---:|---:|",
    ]
    for k, v in (result.get("signatures") or {}).items():
        lines.append(
            f"| {k} | {v.get('n', 0)} | {float(v.get('wr') or 0):.1%} | {float(v.get('mfe') or 0):.3f} |"
        )
    lines += [
        "",
        f"**Lift (alive − dead):** `{json.dumps(result.get('lift'))}`",
        "",
        "## Soft-score bakeoff (top-2 slots / date-hour)",
        "",
        "| Variant | n | WR | MFE | OOS WR | Ship? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    base = (result.get("bakeoff") or {}).get("base_scan") or {}
    for name, b in (result.get("bakeoff") or {}).items():
        flag = "—" if name == "base_scan" else ("YES" if name in (result.get("winners") or []) else "no")
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('mfe') or 0):.3f} | {float(b.get('oos_wr') or 0):.1%} | {flag} |"
        )
    lines += ["", "## Feature strata", ""]
    for feat, rows in (result.get("strata") or {}).items():
        lines += [f"### {feat}", "", "| Bucket | n | WR | MFE |", "|---|---:|---:|---:|"]
        for r in rows or []:
            lines.append(
                f"| {r.get('bucket')} | {r.get('n')} | {float(r.get('wr') or 0):.1%} | {float(r.get('mfe') or 0):.3f} |"
            )
        lines.append("")
    lines += ["## Notes", ""]
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "winners": result.get("winners"),
        "lift": result.get("lift"),
        "coverage": result.get("coverage"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
