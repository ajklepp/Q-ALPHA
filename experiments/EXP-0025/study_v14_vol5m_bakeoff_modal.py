"""
EXP-0025b — LARGE-SAMPLE bakeoff: continuation v1.4 vs + soft 5m vol-alive.

Universe: ALL EXP-0021 all_hours_admit rows (3353) — larger than prior 1400 sample.
5m data: one Polygon pull per symbol (267) covering Jun–Sep 2026, then slice
pre-signal windows locally (no look-ahead).

Variants:
  v14              — live continuation_score (v1.4)
  v14_expand       — +EXPAND_PTS if 5m rv_ratio>1 and rv_slope>0
  v14_alive        — +ALIVE_PTS / -DEAD_PTS (cluster+expand vs dying)
  v14_rv_quart     — soft points from rv_ratio vs train median (causal WF medians)
  v14_combo        — expand + mild cluster

Ship gate (same spirit as blind-spot studies):
  challenger slot Exp R >= baseline - eps AND capture >= baseline - 1pp
  OR Exp R > baseline + 0.01 with capture not worse than -2pp
  OOS reported; vacuous (no-op) winners discarded.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0025/study_v14_vol5m_bakeoff_modal.py
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

APP_NAME = "q-alpha-exp025b-v14-vol5m-bakeoff"
_REPO = Path(r"C:\Users\ajkle\Documents\Q-ALPHA")
EXP_DIR = _REPO / "experiments" / "EXP-0025"
CORPUS = _REPO / "experiments" / "EXP-0021" / "corpus_htf_universe.csv"
SCORE_PY = _REPO / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"
OUT_MD = EXP_DIR / "BAKEOFF_V14_VOL5M.md"
OUT_JSON = EXP_DIR / "bakeoff_v14_vol5m_metrics.json"
CACHE_JSON = EXP_DIR / "bakeoff_v14_vol5m_cache.json"

app = modal.App(APP_NAME)
_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(["numpy", "pandas", "requests", "pytz", "tzdata"])
)
if CORPUS.exists():
    _image = _image.add_local_file(str(CORPUS), remote_path="/data/corpus.csv")
if SCORE_PY.exists():
    _image = _image.add_local_file(str(SCORE_PY), remote_path="/pkg/tsd_launch_score.py")
image = _image
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
ET = ZoneInfo("America/New_York")
SLEEP = 0.12
LOOKBACK_MIN = 180
BAR_START = "2026-06-01"
BAR_END = "2026-09-05"
SLOTS = 2
OOS_CUT = "2026-08-11"
EXPAND_PTS = 10.0
ALIVE_PTS = 12.0
DEAD_PTS = 12.0
CLUSTER_PTS = 6.0


def _get(url: str, params: dict, retries: int = 4) -> dict:
    import requests
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(0.8 * (i + 1))
                continue
            if r.status_code in (403, 404):
                return {"_status": r.status_code}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    return {"_error": str(last)[:160]}


def hurst_dfa(rets) -> float:
    import numpy as np
    x = np.asarray(rets, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 24:
        return float("nan")
    y = np.cumsum(x - x.mean())
    log_s, log_f = [], []
    s = 6
    while s <= n // 4:
        nseg = n // s
        if nseg < 2:
            break
        rms = []
        for i in range(nseg):
            seg = y[i * s:(i + 1) * s]
            t = np.arange(s, dtype=float)
            coef = np.polyfit(t, seg, 1)
            trend = coef[0] * t + coef[1]
            rms.append(float(np.sqrt(np.mean((seg - trend) ** 2))))
        f = float(np.mean(rms))
        if f > 0:
            log_s.append(math.log(s))
            log_f.append(math.log(f))
        s *= 2
    if len(log_s) < 2:
        return float("nan")
    import numpy as np
    X, Y = np.array(log_s), np.array(log_f)
    xm, ym = X.mean(), Y.mean()
    den = float(((X - xm) ** 2).sum())
    if den <= 0:
        return float("nan")
    return float(((X - xm) * (Y - ym)).sum() / den)


def feats_from_closes(closes: list[float]) -> dict[str, float]:
    import numpy as np
    c = np.asarray(closes, dtype=float)
    if len(c) < 20:
        return {"ok": 0.0}
    r = np.diff(np.log(np.clip(c, 1e-9, None)))
    r = r[np.isfinite(r)]
    if len(r) < 16:
        return {"ok": 0.0}
    short_n = min(6, max(3, len(r) // 2))
    rv_s = float(np.sqrt(np.mean(r[-short_n:] ** 2)))
    rv_l = float(np.sqrt(np.mean(r ** 2)))
    rv_ratio = rv_s / rv_l if rv_l > 1e-12 else float("nan")
    z = r ** 2
    zc = z - z.mean()
    if float((zc[:-1] ** 2).sum()) > 0:
        acf1 = float((zc[1:] * zc[:-1]).sum() / (zc[:-1] ** 2).sum())
    else:
        acf1 = float("nan")
    mid = len(r) // 2
    rv1 = float(np.sqrt(np.mean(r[:mid] ** 2))) if mid > 2 else float("nan")
    rv2 = float(np.sqrt(np.mean(r[mid:] ** 2))) if len(r) - mid > 2 else float("nan")
    rv_slope = (rv2 / rv1 - 1.0) if (rv1 == rv1 and rv1 > 1e-12) else float("nan")
    return {
        "ok": 1.0,
        "rv_ratio": rv_ratio,
        "acf_r2": acf1,
        "rv_slope": rv_slope,
        "hurst_5m": hurst_dfa(r),
        "rv_long": rv_l,
        "n_5m": float(len(c)),
    }


@app.function(image=image, secrets=[polygon_secret], timeout=300, max_containers=50)
def fetch_symbol_5m(symbol: str) -> dict[str, Any]:
    """All 5m bars for symbol over bakeoff window."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    sym = symbol.upper()
    out: dict[str, Any] = {"symbol": sym, "bars": [], "ok": 0}
    if not key:
        return out
    # paginate if needed
    bars = []
    ag = _get(
        f"{POLYGON}/v2/aggs/ticker/{sym}/range/5/minute/{BAR_START}/{BAR_END}",
        {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP)
    for b in ag.get("results") or []:
        try:
            bars.append({"t": int(b["t"]), "c": float(b["c"])})
        except Exception:
            continue
    # next_url pagination
    nxt = ag.get("next_url")
    while nxt and len(bars) < 200000:
        page = _get(nxt, {"apiKey": key})
        time.sleep(SLEEP)
        for b in page.get("results") or []:
            try:
                bars.append({"t": int(b["t"]), "c": float(b["c"])})
            except Exception:
                continue
        nxt = page.get("next_url")
        if not page.get("results"):
            break
    out["bars"] = bars
    out["ok"] = 1 if len(bars) >= 50 else 0
    return out


def ship_pass(chal: dict, base: dict) -> bool:
    return (
        chal["exp"] >= base["exp"] - 1e-9
        and chal["capture"] >= base["capture"] - 0.01
    ) or (
        chal["exp"] > base["exp"] + 0.01
        and chal["capture"] >= base["capture"] - 0.02
    )


def simulate_slots(df, *, score_col: str):
    work = df.copy()
    if work.empty:
        return work
    work = work.sort_values(
        ["signal_date", "hour", score_col], ascending=[True, True, False],
    )
    return work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS)


def metrics(taken, expanders=None) -> dict[str, Any]:
    if taken is None or getattr(taken, "empty", True):
        return {"n": 0, "wr": 0.0, "exp": 0.0, "capture": 0.0, "mfe": 0.0}
    wr = float(taken["hit_1r"].mean())
    exp = float(taken["r_multiple"].mean()) if "r_multiple" in taken.columns else float(taken["day_mfe"].mean())
    mfe = float(taken["day_mfe"].mean())
    cap = 0.0
    if expanders is not None and len(expanders):
        keys = set(zip(taken["signal_date"].astype(str), taken["symbol"].astype(str)))
        ekeys = set(zip(expanders["signal_date"].astype(str), expanders["symbol"].astype(str)))
        cap = len(keys & ekeys) / max(len(ekeys), 1)
    return {"n": int(len(taken)), "wr": wr, "exp": exp, "mfe": mfe, "capture": cap}


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 90)
def run_study() -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    import sys

    sys.path.insert(0, "/pkg")
    from tsd_launch_score import compute_continuation_score_v1_1, CONTINUATION_SCORE_VERSION

    t0 = time.time()
    df = pd.read_csv("/data/corpus.csv")
    df = df[df["all_hours_admit"] == 1].copy()
    df["signal_date"] = df["signal_date"].astype(str)
    df["symbol"] = df["symbol"].astype(str).str.upper()
    # ensure r_multiple
    if "r_multiple" not in df.columns:
        # proxy: day_mfe / kill-ish  — corpus often has hit_1r; use day_mfe as exp proxy if missing
        df["r_multiple"] = df.get("r_multiple", df["day_mfe"])

    symbols = sorted(df["symbol"].unique().tolist())
    print(f"Admits={len(df)} symbols={len(symbols)} — fetching 5m…")
    fetched = list(fetch_symbol_5m.map(symbols))
    by_sym = {r["symbol"]: r for r in fetched}
    ok_syms = sum(1 for r in fetched if r.get("ok"))

    # Attach feats per row
    rows = []
    miss = 0
    for rec in df.to_dict("records"):
        sym = rec["symbol"]
        bars = (by_sym.get(sym) or {}).get("bars") or []
        ts = str(rec.get("signal_ts") or "").replace(" ", "T")
        try:
            sig = datetime.fromisoformat(ts)
            if sig.tzinfo is None:
                sig = sig.replace(tzinfo=ET)
        except Exception:
            miss += 1
            feats = {"ok": 0.0}
            rows.append({**rec, **{k: float("nan") for k in ("rv_ratio", "acf_r2", "rv_slope", "hurst_5m")}, "vol5_ok": 0})
            continue
        end_ms = int(sig.astimezone(timezone.utc).timestamp() * 1000)
        start_ms = int((sig.astimezone(timezone.utc) - timedelta(minutes=LOOKBACK_MIN)).timestamp() * 1000)
        closes = [b["c"] for b in bars if start_ms <= b["t"] < end_ms]
        feats = feats_from_closes(closes)
        if feats.get("ok") != 1.0:
            miss += 1
            rows.append({**rec, "rv_ratio": float("nan"), "acf_r2": float("nan"), "rv_slope": float("nan"),
                         "hurst_5m": float("nan"), "vol5_ok": 0})
        else:
            rows.append({
                **rec,
                "rv_ratio": feats["rv_ratio"],
                "acf_r2": feats["acf_r2"],
                "rv_slope": feats["rv_slope"],
                "hurst_5m": feats["hurst_5m"],
                "vol5_ok": 1,
            })

    panel = pd.DataFrame(rows)
    # v1.4 score
    scores = []
    for rec in panel.to_dict("records"):
        try:
            scores.append(float(compute_continuation_score_v1_1(rec)))
        except Exception:
            scores.append(0.0)
    panel["score_v14"] = scores

    # Causal train median acf for cluster flag (use pre-OOS only for threshold)
    train = panel[panel["signal_date"] < OOS_CUT]
    med_acf = float(train["acf_r2"].median()) if train["acf_r2"].notna().any() else 0.0
    med_rv = float(train["rv_ratio"].median()) if train["rv_ratio"].notna().any() else 1.0

    ok = panel["vol5_ok"] == 1
    expand = ok & (panel["rv_ratio"] > 1.0) & (panel["rv_slope"] > 0)
    cluster = ok & (panel["acf_r2"] >= med_acf)
    alive = expand & cluster
    dead = ok & (
        ((panel["hurst_5m"] < 0.45) & panel["hurst_5m"].notna())
        | ((panel["rv_slope"] < 0) & (panel["rv_ratio"] < 1.0))
    )
    # Neutral when no 5m: no adjustment

    panel["score_expand"] = panel["score_v14"] + np.where(expand, EXPAND_PTS, 0.0)
    panel["score_alive"] = panel["score_v14"] + np.where(alive, ALIVE_PTS, 0.0) - np.where(dead, DEAD_PTS, 0.0)
    panel["score_cluster"] = panel["score_v14"] + np.where(cluster, CLUSTER_PTS, 0.0) - np.where(
        ok & ~cluster, CLUSTER_PTS * 0.5, 0.0
    )
    # rv quartile soft: points from rank of rv_ratio among ok rows on same day (cross-section)
    panel["score_rvq"] = panel["score_v14"].copy()
    for sd, g in panel.groupby("signal_date"):
        idx = g.index
        rr = g["rv_ratio"]
        if rr.notna().sum() < 5:
            continue
        # 0..1 rank
        ranks = rr.rank(pct=True)
        panel.loc[idx, "score_rvq"] = g["score_v14"] + 12.0 * (ranks.fillna(0.5) - 0.5)
    panel["score_combo"] = (
        panel["score_v14"]
        + np.where(expand, EXPAND_PTS, 0.0)
        + np.where(cluster, CLUSTER_PTS * 0.5, 0.0)
        - np.where(dead, DEAD_PTS * 0.75, 0.0)
    )

    # Expanders = top day_mfe per day among admits (for capture)
    expanders = (
        panel.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    variants = {
        "v14": "score_v14",
        "v14_expand": "score_expand",
        "v14_alive": "score_alive",
        "v14_cluster": "score_cluster",
        "v14_rv_quartile": "score_rvq",
        "v14_combo": "score_combo",
    }
    bakeoff = {}
    for name, col in variants.items():
        taken = simulate_slots(panel, score_col=col)
        m = metrics(taken, expanders)
        oos = taken[taken["signal_date"] >= OOS_CUT]
        m["oos"] = metrics(oos, expanders[expanders["signal_date"] >= OOS_CUT])
        if name == "v14":
            m["pass_vs_v14"] = True
            m["vacuous"] = False
        else:
            m["pass_vs_v14"] = ship_pass(m, bakeoff["v14"]) if "v14" in bakeoff else False
            # vacuous if scores identical on almost all rows
            same = float((panel[col] == panel["score_v14"]).mean())
            m["vacuous"] = same > 0.98
            if m["vacuous"]:
                m["pass_vs_v14"] = False
        bakeoff[name] = m

    # Fix pass flags now that v14 exists
    base = bakeoff["v14"]
    winners = []
    for name, m in bakeoff.items():
        if name == "v14":
            continue
        same = float((panel[variants[name]] == panel["score_v14"]).mean())
        m["vacuous"] = same > 0.98
        m["pass_vs_v14"] = (not m["vacuous"]) and ship_pass(m, base)
        # also require OOS exp not collapse
        oos_ok = float((m.get("oos") or {}).get("exp") or 0) >= float((base.get("oos") or {}).get("exp") or 0) - 0.02
        m["oos_ok"] = oos_ok
        if m["pass_vs_v14"] and oos_ok:
            winners.append(name)
        bakeoff[name] = m

    # Signature rates on full panel
    sig = {
        "n_admit": int(len(panel)),
        "n_vol5_ok": int(ok.sum()),
        "pct_vol5_ok": round(float(ok.mean()), 3),
        "expand_n": int(expand.sum()),
        "expand_wr": float(panel.loc[expand, "hit_1r"].mean()) if expand.any() else None,
        "alive_n": int(alive.sum()),
        "alive_wr": float(panel.loc[alive, "hit_1r"].mean()) if alive.any() else None,
        "dead_n": int(dead.sum()),
        "dead_wr": float(panel.loc[dead, "hit_1r"].mean()) if dead.any() else None,
        "base_wr": float(panel["hit_1r"].mean()),
        "med_acf_train": med_acf,
        "med_rv_train": med_rv,
    }

    if winners:
        rec = "ADD_SOFT"
        rec_text = (
            f"Large-sample bakeoff: {winners} beat v1.4 on slot Exp/capture (non-vacuous, OOS ok). "
            "Candidate soft terms for continuation_score — await explicit live wire approval."
        )
    elif any(bakeoff[k].get("pass_vs_v14") for k in bakeoff if k != "v14"):
        rec = "WEAK"
        rec_text = "Some in-sample pass but OOS weak or unstable. HOLD for live."
    else:
        # check strata lift anyway
        if sig["expand_n"] and sig["expand_wr"] and sig["expand_wr"] > sig["base_wr"] + 0.02:
            rec = "HOLD_DIAGNOSTIC"
            rec_text = (
                "Expanding-vol strata still richer than baseline, but soft score variants "
                "did not clear ship gate vs v1.4 on full-corpus slots. HOLD — do not wire."
            )
        else:
            rec = "HOLD"
            rec_text = "No additive soft edge vs v1.4 on full admit corpus. Keep v1.4 unchanged."

    # slim cache: per-row feats only
    cache_rows = panel[["symbol", "signal_ts", "vol5_ok", "rv_ratio", "acf_r2", "rv_slope"]].to_dict("records")

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "score_version": CONTINUATION_SCORE_VERSION,
        "design": {
            "admits": int(len(panel)),
            "symbols": len(symbols),
            "symbols_5m_ok": ok_syms,
            "lookback_min": LOOKBACK_MIN,
            "slots": SLOTS,
            "oos_cut": OOS_CUT,
            "expand_pts": EXPAND_PTS,
            "alive_pts": ALIVE_PTS,
            "dead_pts": DEAD_PTS,
        },
        "signatures": sig,
        "bakeoff": bakeoff,
        "winners": winners,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Full all_hours_admit corpus (3353) — larger than EXP-0025 sample (1400).",
            "5m fetched per symbol once; pre-signal slice only.",
            "Missing 5m ⇒ neutral (no boost/demote) so v1.4 still ranks them.",
            "Research only — no live tsd_launch_score edit in this run.",
        ],
        "cache_rows": cache_rows[:5000],  # cap
        "cache_n": len(cache_rows),
    }


@app.local_entrypoint()
def main():
    print("Launching LARGE-SAMPLE v1.4 vs 5m vol-alive bakeoff")
    result = run_study.remote()
    cache = {
        "n": result.get("cache_n"),
        "rows": result.get("cache_rows") or [],
        "meta": result.get("design"),
    }
    CACHE_JSON.write_text(json.dumps(cache), encoding="utf-8")
    metrics = {k: v for k, v in result.items() if k not in ("cache_rows",)}
    OUT_JSON.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0025b — Large-sample bakeoff: v1.4 vs +5m vol-alive",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Live score baseline:** `{result.get('score_version')}`",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Design",
        f"`{json.dumps(result.get('design'))}`",
        "",
        "## Signatures (full corpus)",
        f"`{json.dumps(result.get('signatures'))}`",
        "",
        "## Bakeoff (top-2 slots / date-hour)",
        "",
        "| Variant | n | WR | Exp | MFE | Capture | OOS Exp | OOS WR | Pass vs v1.4 | Vacuous |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name, b in (result.get("bakeoff") or {}).items():
        oos = b.get("oos") if isinstance(b.get("oos"), dict) else {}
        flag = "—" if name == "v14" else ("YES" if b.get("pass_vs_v14") else "no")
        if b.get("vacuous"):
            flag = "vacuous"
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('exp') or 0):.4f} | {float(b.get('mfe') or 0):.4f} | "
            f"{float(b.get('capture') or 0):.1%} | {float(oos.get('exp') or 0):.4f} | "
            f"{float(oos.get('wr') or 0):.1%} | {flag} | {b.get('vacuous')} |"
        )
    lines += [
        "",
        f"**Winners:** `{result.get('winners')}`",
        "",
        "## Decision",
        "",
        "- Reply **ADD soft** / **HOLD** for live `continuation_score` wire.",
        "- No live rewrite from this file alone.",
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
        "winners": result.get("winners"),
        "signatures": result.get("signatures"),
        "bakeoff_exp": {k: (v.get("exp"), (v.get("oos") or {}).get("exp")) for k, v in (result.get("bakeoff") or {}).items()},
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
