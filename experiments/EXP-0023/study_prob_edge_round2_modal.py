"""
EXP-0023 Round-2 — Cross-sectional + SPY-regime edge hunt (Modal / Polygon).

Time-series timing (R1) failed. This round tests where equity probability
edge usually appears: cross-sectional ranks and market-regime overlays.

Long-only. Costs on flips / reconstitution. No live Peak Hour changes.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0023/study_prob_edge_round2_modal.py
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modal

APP_NAME = "q-alpha-exp023-prob-r2"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results_round2.md"
OUT_JSON = EXP_DIR / "study_prob_edge_round2_metrics.json"

app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "numpy", "pandas", "requests", "pytz", "tzdata",
])
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
COST = 0.0015
START = "2019-01-01"
END = "2026-09-05"
TRAIN = 504
TEST = 63
SLEEP = 0.12
TOP_Q = 0.2  # long top 20%

UNIVERSE = [
    "SPY",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "NFLX",
    "AMD", "ORCL", "CRM", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMAT", "MU",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    "XOM", "CVX", "COP", "SLB", "OXY",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "AMGN", "TMO",
    "WMT", "COST", "HD", "TGT", "NKE", "SBUX", "MCD", "PG", "KO", "PEP",
    "CAT", "GE", "BA", "HON", "DE", "UPS", "FDX",
    "DIS", "CMCSA", "IBM", "NOW", "PANW", "CRWD",
    "UBER", "ABNB", "SHOP", "COIN", "PLTR", "SNOW", "DDOG",
    "SMCI", "ARM", "APP", "MSTR", "SOFI", "HOOD",
]


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


@app.function(image=image, secrets=[polygon_secret], timeout=180, max_containers=48)
def fetch_daily(symbol: str) -> dict[str, Any]:
    key = os.environ.get("POLYGON_API_KEY") or ""
    sym = symbol.upper()
    out: dict[str, Any] = {"symbol": sym, "bars": [], "ok": 0}
    if not key:
        return out
    ag = _get(
        f"{POLYGON}/v2/aggs/ticker/{sym}/range/1/day/{START}/{END}",
        {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP)
    bars = []
    for b in ag.get("results") or []:
        try:
            d = datetime.fromtimestamp(int(b["t"]) / 1000, tz=timezone.utc).date().isoformat()
            bars.append({"date": d, "c": float(b["c"])})
        except Exception:
            continue
    out["bars"] = bars
    out["ok"] = 1 if len(bars) >= 400 else 0
    return out


def _np():
    import numpy as np
    return np


def sharpe(x) -> float:
    np = _np()
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 5:
        return 0.0
    s = float(a.std(ddof=1))
    return float(a.mean() / s * math.sqrt(252)) if s > 0 else 0.0


def max_dd(x) -> float:
    np = _np()
    eq = np.cumprod(1.0 + np.asarray(x, dtype=float))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min()) if len(eq) else 0.0


def fit_hmm2(obs, n_iter=12):
    np = _np()
    x = np.asarray(obs, dtype=float)
    finite = x[np.isfinite(x)]
    if len(finite) < 80:
        return None
    med = float(np.median(finite))
    mu = np.array([
        finite[finite <= med].mean() if (finite <= med).any() else finite.mean(),
        finite[finite > med].mean() if (finite > med).any() else finite.mean(),
    ], dtype=float)
    var = np.array([
        max(float(finite[finite <= med].var()) if (finite <= med).sum() > 2 else float(finite.var()), 1e-8),
        max(float(finite[finite > med].var()) if (finite > med).sum() > 2 else float(finite.var()), 1e-8),
    ])
    A = np.array([[0.92, 0.08], [0.08, 0.92]])
    pi = np.array([0.5, 0.5])
    xx = np.where(np.isfinite(x), x, 0.0)
    mask = np.isfinite(x)
    T = len(xx)
    gamma = np.zeros((T, 2))
    for _ in range(n_iter):
        logB = np.zeros((T, 2))
        for k in range(2):
            logB[:, k] = -0.5 * (math.log(2 * math.pi * var[k]) + (xx - mu[k]) ** 2 / var[k])
        log_alpha = np.zeros((T, 2))
        log_alpha[0] = np.log(pi + 1e-12) + logB[0]
        for t in range(1, T):
            for j in range(2):
                log_alpha[t, j] = logB[t, j] + np.logaddexp(
                    log_alpha[t - 1, 0] + math.log(A[0, j] + 1e-12),
                    log_alpha[t - 1, 1] + math.log(A[1, j] + 1e-12),
                )
        log_beta = np.zeros((T, 2))
        for t in range(T - 2, -1, -1):
            for i in range(2):
                log_beta[t, i] = np.logaddexp(
                    math.log(A[i, 0] + 1e-12) + logB[t + 1, 0] + log_beta[t + 1, 0],
                    math.log(A[i, 1] + 1e-12) + logB[t + 1, 1] + log_beta[t + 1, 1],
                )
        for t in range(T):
            lg = log_alpha[t] + log_beta[t]
            lg -= np.logaddexp(lg[0], lg[1])
            gamma[t] = np.exp(lg)
        num = np.zeros((2, 2))
        for t in range(T - 1):
            log_xi = np.zeros((2, 2))
            for i in range(2):
                for j in range(2):
                    log_xi[i, j] = log_alpha[t, i] + math.log(A[i, j] + 1e-12) + logB[t + 1, j] + log_beta[t + 1, j]
            m = np.max(log_xi)
            xi = np.exp(log_xi - m)
            xi /= xi.sum() + 1e-12
            num += xi
        A = num / (num.sum(axis=1, keepdims=True) + 1e-12)
        pi = gamma[0] / (gamma[0].sum() + 1e-12)
        for k in range(2):
            w = gamma[:, k] * mask
            sw = w.sum()
            if sw < 5:
                continue
            mu[k] = float((w * xx).sum() / sw)
            var[k] = max(float((w * (xx - mu[k]) ** 2).sum() / sw), 1e-8)
    return {"mu": mu, "var": var, "A": A, "pi": pi}


def filter_hmm(obs, model):
    np = _np()
    if model is None:
        return None
    mu, var, A, pi = model["mu"], model["var"], model["A"], model["pi"]
    xx = np.asarray(obs, dtype=float)
    T = len(xx)
    filt = np.full((T, 2), np.nan)
    prev = pi.copy()
    for t in range(T):
        if not np.isfinite(xx[t]):
            filt[t] = prev
            continue
        emit = np.array([
            math.exp(-0.5 * (math.log(2 * math.pi * var[k]) + (xx[t] - mu[k]) ** 2 / var[k]))
            for k in range(2)
        ])
        pred = A.T @ prev
        un = pred * emit
        s = un.sum()
        prev = un / s if s > 0 else pred
        filt[t] = prev
    return filt


def panel_from_fetch(fetched: list[dict]) -> tuple[list[str], list[str], Any]:
    """Return dates, symbols (ex-SPY), close matrix (T x N), spy series."""
    np = _np()
    by = {r["symbol"]: r for r in fetched if r.get("ok")}
    spy = by["SPY"]["bars"]
    dates = [b["date"] for b in spy]
    spy_c = np.array([b["c"] for b in spy], dtype=float)
    syms = sorted(s for s in by if s != "SPY")
    # keep symbols with enough overlap
    mats = []
    keep = []
    for s in syms:
        m = {b["date"]: b["c"] for b in by[s]["bars"]}
        series = [m.get(d, math.nan) for d in dates]
        if sum(1 for x in series if x == x) >= TRAIN + TEST + 50:
            mats.append(series)
            keep.append(s)
    C = np.array(mats, dtype=float).T  # T x N
    return dates, keep, C, spy_c


def next_day_rets(C):
    np = _np()
    R = np.full_like(C, np.nan)
    R[1:] = C[1:] / C[:-1] - 1.0
    return R


def feature_matrices(C, spy_c):
    """Causal features at t for ranking into t+1."""
    np = _np()
    T, N = C.shape
    R = next_day_rets(C)
    spy_r = np.full(T, np.nan)
    spy_r[1:] = spy_c[1:] / spy_c[:-1] - 1.0

    mom20 = np.full((T, N), np.nan)
    mom60 = np.full((T, N), np.nan)
    mom120 = np.full((T, N), np.nan)
    vol20 = np.full((T, N), np.nan)
    z20 = np.full((T, N), np.nan)
    rs20 = np.full((T, N), np.nan)
    resid5 = np.full((T, N), np.nan)  # 5d residual vs SPY (MR signal = -resid)

    for t in range(T):
        if t >= 20:
            mom20[t] = C[t] / C[t - 20] - 1.0
            # vol of daily rets
            sl = R[t - 19:t + 1]
            vol20[t] = np.nanstd(sl, axis=0, ddof=1)
            m = np.nanmean(C[t - 19:t + 1], axis=0)
            s = np.nanstd(C[t - 19:t + 1], axis=0, ddof=1)
            z20[t] = (C[t] - m) / np.where(s > 0, s, np.nan)
            spy_m = spy_c[t] / spy_c[t - 20] - 1.0
            rs20[t] = mom20[t] - spy_m
        if t >= 60:
            mom60[t] = C[t] / C[t - 60] - 1.0
        if t >= 120:
            mom120[t] = C[t] / C[t - 120] - 1.0
        if t >= 5:
            # residual approx: stock 5d - beta*spy 5d with beta=1
            stock5 = C[t] / C[t - 5] - 1.0
            spy5 = spy_c[t] / spy_c[t - 5] - 1.0
            resid5[t] = stock5 - spy5

    return {
        "mom20": mom20,
        "mom60": mom60,
        "mom120": mom120,
        "vol20": vol20,
        "z20": z20,
        "rs20": rs20,
        "neg_resid5": -resid5,  # long most negative residual (MR)
        "low_vol": -vol20,      # long lowest vol
        "R": R,
        "spy_r": spy_r,
    }


def top_quantile_mask(score_row, q=TOP_Q):
    np = _np()
    x = np.asarray(score_row, dtype=float)
    valid = np.isfinite(x)
    pos = np.zeros_like(x, dtype=float)
    if valid.sum() < 10:
        return pos
    thr = np.nanquantile(x[valid], 1.0 - q)
    pos[valid & (x >= thr)] = 1.0
    return pos


def portfolio_rets(pos, day_rets, prev_pos=None):
    """Equal-weight among longs; cost on name turnover fraction."""
    np = _np()
    pos = np.asarray(pos, dtype=float)
    day_rets = np.asarray(day_rets, dtype=float)
    active = pos > 0
    if not active.any():
        return 0.0, pos
    w = pos / active.sum()
    gross = float(np.nansum(w * day_rets))
    if prev_pos is None:
        turnover = float(active.mean())  # initial deploy
    else:
        # L1 turnover / 2
        prev = np.asarray(prev_pos, dtype=float)
        prev_a = prev > 0
        if prev_a.any():
            pw = prev / prev_a.sum()
        else:
            pw = np.zeros_like(prev)
        turnover = 0.5 * float(np.nansum(np.abs(w - pw)))
    net = gross - COST * turnover
    return net, pos


def eval_cross_section(feats, spy_bull_filt) -> dict[str, Any]:
    np = _np()
    R = feats["R"]
    T, N = R.shape
    strategies = {
        "ew_universe": None,  # baseline: equal weight all
        "cs_mom20": "mom20",
        "cs_mom60": "mom60",
        "cs_mom120": "mom120",
        "cs_rs20": "rs20",
        "cs_mr_z": None,  # long lowest z (most oversold) = bottom quantile of z
        "cs_mr_resid": "neg_resid5",
        "cs_low_vol": "low_vol",
        "cs_mom20_lowvol": None,  # composite
        "ew_spy_bull": None,  # EW only when SPY HMM bull
        "cs_mom20_spy_bull": None,
    }

    # Walk-forward windows for reporting stability
    window_stats = {k: [] for k in strategies}
    pooled = {k: [] for k in strategies}

    start = TRAIN
    while start + TEST <= T - 1:
        te0, te1 = start, start + TEST
        prev = {k: None for k in strategies}
        for t in range(te0, te1):
            # earn R[t+1] using scores at t
            if t + 1 >= T:
                break
            day = R[t + 1]
            # baselines / strategies masks at t
            ew = np.ones(N)
            ew[~np.isfinite(day)] = 0

            masks = {}
            masks["ew_universe"] = ew
            masks["cs_mom20"] = top_quantile_mask(feats["mom20"][t])
            masks["cs_mom60"] = top_quantile_mask(feats["mom60"][t])
            masks["cs_mom120"] = top_quantile_mask(feats["mom120"][t])
            masks["cs_rs20"] = top_quantile_mask(feats["rs20"][t])
            # MR: top of -z = bottom z
            masks["cs_mr_z"] = top_quantile_mask(-feats["z20"][t])
            masks["cs_mr_resid"] = top_quantile_mask(feats["neg_resid5"][t])
            masks["cs_low_vol"] = top_quantile_mask(feats["low_vol"][t])
            # composite: mom20 rank among low-vol half
            vol = feats["vol20"][t]
            med = np.nanmedian(vol)
            mom = feats["mom20"][t].copy()
            mom[~(np.isfinite(vol) & (vol <= med))] = np.nan
            masks["cs_mom20_lowvol"] = top_quantile_mask(mom)

            bull = True
            if spy_bull_filt is not None and np.isfinite(spy_bull_filt[t]):
                bull = bool(spy_bull_filt[t] >= 0.55)
            masks["ew_spy_bull"] = ew if bull else np.zeros(N)
            masks["cs_mom20_spy_bull"] = masks["cs_mom20"] if bull else np.zeros(N)

            for name, m in masks.items():
                net, prev[name] = portfolio_rets(m, day, prev[name])
                pooled[name].append(net)

        # window summary vs ew
        base = pooled["ew_universe"][- (te1 - te0):]
        for name in strategies:
            seg = pooled[name][-(te1 - te0):]
            window_stats[name].append({
                "sharpe": sharpe(seg),
                "ret": float(np.nansum(seg)),
                "beat": sharpe(seg) > sharpe(base) and float(np.nansum(seg)) > float(np.nansum(base)),
            })
        start += TEST

    out = {}
    base_pool = pooled["ew_universe"]
    for name, series in pooled.items():
        beats = [w["beat"] for w in window_stats[name]]
        out[name] = {
            "sharpe": round(sharpe(series), 3),
            "sharpe_ew": round(sharpe(base_pool), 3),
            "ret": round(float(np.nansum(series)), 4),
            "ret_ew": round(float(np.nansum(base_pool)), 4),
            "mdd": round(max_dd(series), 4),
            "mdd_ew": round(max_dd(base_pool), 4),
            "edge_sharpe": round(sharpe(series) - sharpe(base_pool), 3),
            "pass_frac": round(float(np.mean(beats)) if beats else 0.0, 3),
            "n_windows": len(beats),
            "calmar": round(
                (float(np.nansum(series)) / max(abs(max_dd(series)), 1e-6)), 3
            ) if series else 0.0,
        }
        # gates vs equal-weight universe
        out[name]["gate_pass"] = bool(
            out[name]["sharpe"] > out[name]["sharpe_ew"] + 0.10
            and out[name]["ret"] > out[name]["ret_ew"]
            and out[name]["pass_frac"] >= 0.35
        )
        out[name]["ship_pass"] = bool(
            out[name]["sharpe"] > out[name]["sharpe_ew"] + 0.20
            and out[name]["ret"] > out[name]["ret_ew"]
            and out[name]["pass_frac"] >= 0.45
            and out[name]["mdd"] > out[name]["mdd_ew"] - 0.05  # not much worse DD
        )
    return out


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 30)
def run_study() -> dict[str, Any]:
    np = _np()
    t0 = time.time()
    print(f"Fetching {len(UNIVERSE)}…")
    fetched = list(fetch_daily.map(UNIVERSE))
    dates, syms, C, spy_c = panel_from_fetch(fetched)
    print(f"Panel T={len(dates)} N={len(syms)}")

    feats = feature_matrices(C, spy_c)
    spy_r = feats["spy_r"]
    # SPY HMM on train-like full sample filtered causally with rolling refit sparse
    # Fit on first TRAIN, filter forward; refit each TEST block
    spy_bull = np.full(len(spy_r), np.nan)
    start = TRAIN
    last_model = fit_hmm2(spy_r[:TRAIN])
    while start < len(spy_r):
        model = fit_hmm2(spy_r[max(0, start - TRAIN):start]) or last_model
        if model is not None:
            last_model = model
        filt = filter_hmm(spy_r, last_model)
        bull = int(np.argmax(last_model["mu"])) if last_model is not None else 0
        end = min(start + TEST, len(spy_r))
        if filt is not None:
            spy_bull[start:end] = filt[start:end, bull]
        start += TEST

    bakeoff = eval_cross_section(feats, spy_bull)
    # drop baseline from winners
    winners_gate = [k for k, v in bakeoff.items() if k != "ew_universe" and v.get("gate_pass")]
    winners_ship = [k for k, v in bakeoff.items() if k != "ew_universe" and v.get("ship_pass")]
    ranked = sorted(
        [(k, v) for k, v in bakeoff.items() if k != "ew_universe"],
        key=lambda kv: (kv[1].get("ship_pass"), kv[1].get("gate_pass"), kv[1].get("edge_sharpe", -999)),
        reverse=True,
    )
    best = ranked[0] if ranked else (None, {})

    if winners_ship:
        rec, rec_text = "PROMISING", (
            f"Cross-sectional ship winners: {winners_ship}. "
            "Edge vs equal-weight universe after turnover costs. Research-only."
        )
    elif winners_gate:
        rec, rec_text = "WEAK", f"Soft gate: {winners_gate}."
    else:
        rec, rec_text = "FAIL", (
            f"Round-2 also FAIL. Closest={best[0]} edge_sharpe={((best[1] or {}).get('edge_sharpe'))}."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "design": {
            "panel_T": len(dates), "panel_N": len(syms),
            "top_q": TOP_Q, "cost": COST, "train": TRAIN, "test": TEST,
            "baseline": "equal_weight_universe",
        },
        "bakeoff": bakeoff,
        "winners_gate": winners_gate,
        "winners_ship": winners_ship,
        "best": {"name": best[0], **(best[1] or {})} if best[0] else None,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Round-1 time-series timing FAIL; Round-2 = cross-section + SPY regime.",
            "Long-only top quintile (or EW). No shorts.",
            "Standalone research.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching EXP-0023 Round-2 cross-sectional edge hunt")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# EXP-0023 Round-2 — Cross-sectional + SPY regime",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        f"**Ship:** `{result.get('winners_ship')}`",
        f"**Gate:** `{result.get('winners_gate')}`",
        "",
        "## Bakeoff vs equal-weight universe",
        "",
        "| Strategy | Sharpe | EW Sharpe | Edge | Ret | EW Ret | MDD | Pass% | Gate | Ship |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    items = sorted(
        (result.get("bakeoff") or {}).items(),
        key=lambda kv: kv[1].get("edge_sharpe", -999),
        reverse=True,
    )
    for name, b in items:
        lines.append(
            f"| {name} | {float(b.get('sharpe') or 0):.2f} | {float(b.get('sharpe_ew') or 0):.2f} | "
            f"{float(b.get('edge_sharpe') or 0):+.2f} | {float(b.get('ret') or 0):.3f} | "
            f"{float(b.get('ret_ew') or 0):.3f} | {float(b.get('mdd') or 0):.1%} | "
            f"{float(b.get('pass_frac') or 0):.0%} | "
            f"{'YES' if b.get('gate_pass') else 'no'} | {'YES' if b.get('ship_pass') else 'no'} |"
        )
    lines += ["", "## Notes", ""]
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "winners_ship": result.get("winners_ship"),
        "winners_gate": result.get("winners_gate"),
        "best": result.get("best"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
