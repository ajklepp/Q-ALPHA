"""
EXP-0023 Round-3 — Stress-test near-winners (Modal).

R2 near-misses:
  - ew_spy_bull: +Sharpe, better MDD, lower total return (risk edge)
  - cs_mom60: +Sharpe/+Ret, worse MDD, unstable windows (growth edge)

This round separates RISK vs GROWTH gates, tests combos, monthly rebalance.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0023/study_prob_edge_round3_modal.py
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

APP_NAME = "q-alpha-exp023-prob-r3"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results_round3.md"
OUT_JSON = EXP_DIR / "study_prob_edge_round3_metrics.json"

app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "numpy", "requests", "pytz", "tzdata",
])
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
COST = 0.0015
START = "2019-01-01"
END = "2026-09-05"
TRAIN = 504
TEST = 63
TOP_Q = 0.2
SLEEP = 0.12

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


def build_panel(fetched):
    np = _np()
    by = {r["symbol"]: r for r in fetched if r.get("ok")}
    spy = by["SPY"]["bars"]
    dates = [b["date"] for b in spy]
    spy_c = np.array([float(b["c"]) for b in spy])
    mats, keep = [], []
    for s in sorted(x for x in by if x != "SPY"):
        m = {b["date"]: b["c"] for b in by[s]["bars"]}
        series = [m.get(d, math.nan) for d in dates]
        if sum(1 for v in series if v == v) >= TRAIN + TEST + 50:
            mats.append(series)
            keep.append(s)
    return dates, keep, np.array(mats, dtype=float).T, spy_c


def top_q(score, q=TOP_Q):
    np = _np()
    x = np.asarray(score, dtype=float)
    pos = np.zeros_like(x)
    valid = np.isfinite(x)
    if valid.sum() < 10:
        return pos
    thr = np.nanquantile(x[valid], 1.0 - q)
    pos[valid & (x >= thr)] = 1.0
    return pos


def port_day(pos, day_rets, prev):
    np = _np()
    pos = np.asarray(pos, dtype=float)
    day_rets = np.asarray(day_rets, dtype=float)
    active = pos > 0
    if not active.any():
        return 0.0, pos
    w = pos / active.sum()
    gross = float(np.nansum(w * day_rets))
    if prev is None:
        turnover = float(active.mean())
    else:
        prev = np.asarray(prev, dtype=float)
        pa = prev > 0
        pw = prev / pa.sum() if pa.any() else np.zeros_like(prev)
        turnover = 0.5 * float(np.nansum(np.abs(w - pw)))
    return gross - COST * turnover, pos


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 30)
def run_study() -> dict[str, Any]:
    np = _np()
    t0 = time.time()
    fetched = list(fetch_daily.map(UNIVERSE))
    dates, syms, C, spy_c = build_panel(fetched)
    T, N = C.shape
    R = np.full_like(C, np.nan)
    R[1:] = C[1:] / C[:-1] - 1.0
    spy_r = np.full(T, np.nan)
    spy_r[1:] = spy_c[1:] / spy_c[:-1] - 1.0

    mom60 = np.full((T, N), np.nan)
    mom20 = np.full((T, N), np.nan)
    for t in range(T):
        if t >= 60:
            mom60[t] = C[t] / C[t - 60] - 1.0
        if t >= 20:
            mom20[t] = C[t] / C[t - 20] - 1.0

    # causal SPY bull filter with block refits
    spy_bull = np.full(T, np.nan)
    start = TRAIN
    model = fit_hmm2(spy_r[:TRAIN])
    while start < T:
        model = fit_hmm2(spy_r[max(0, start - TRAIN):start]) or model
        filt = filter_hmm(spy_r, model)
        bull = int(np.argmax(model["mu"])) if model is not None else 0
        end = min(start + TEST, T)
        if filt is not None:
            spy_bull[start:end] = filt[start:end, bull]
        start += TEST

    # also: simple SPY trend regime (close > SMA200) as robustness check
    spy_sma200 = np.full(T, np.nan)
    for t in range(199, T):
        spy_sma200[t] = np.nanmean(spy_c[t - 199:t + 1])
    spy_trend = (spy_c > spy_sma200).astype(float)

    names = [
        "ew",
        "ew_hmm_bull",
        "ew_sma200",
        "cs_mom60_d",          # daily rebalance top20
        "cs_mom60_m21",       # rebalance every 21d
        "cs_mom60_hmm",       # mom60 only in HMM bull
        "cs_mom60_sma200",
        "cs_mom20_m21",
        "cs_mom60_m21_hmm",
    ]
    pooled = {k: [] for k in names}
    windows = {k: [] for k in names}
    invested = {k: [] for k in names}  # fraction invested

    start = TRAIN
    while start + TEST <= T - 1:
        prev = {k: None for k in names}
        last_reb = {k: -10**9 for k in names}
        seg0 = {k: len(pooled[k]) for k in names}
        for t in range(start, start + TEST):
            if t + 1 >= T:
                break
            day = R[t + 1]
            ew = np.ones(N)
            ew[~np.isfinite(day)] = 0
            bull = bool(spy_bull[t] >= 0.55) if np.isfinite(spy_bull[t]) else True
            trend_on = bool(spy_trend[t] == 1) if np.isfinite(spy_sma200[t]) else True

            def maybe_reb(name, desired, every):
                if t - last_reb[name] >= every or prev[name] is None:
                    last_reb[name] = t
                    return desired
                # hold previous membership but drop names with nan day
                hold = np.array(prev[name], dtype=float)
                hold[~np.isfinite(day)] = 0
                return hold

            masks = {
                "ew": ew,
                "ew_hmm_bull": ew if bull else np.zeros(N),
                "ew_sma200": ew if trend_on else np.zeros(N),
                "cs_mom60_d": top_q(mom60[t]),
                "cs_mom60_m21": maybe_reb("cs_mom60_m21", top_q(mom60[t]), 21),
                "cs_mom60_hmm": top_q(mom60[t]) if bull else np.zeros(N),
                "cs_mom60_sma200": top_q(mom60[t]) if trend_on else np.zeros(N),
                "cs_mom20_m21": maybe_reb("cs_mom20_m21", top_q(mom20[t]), 21),
                "cs_mom60_m21_hmm": (
                    maybe_reb("cs_mom60_m21_hmm", top_q(mom60[t]), 21) if bull else np.zeros(N)
                ),
            }
            for name, m in masks.items():
                net, prev[name] = port_day(m, day, prev[name])
                pooled[name].append(net)
                invested[name].append(float((m > 0).any()))

        base = pooled["ew"][seg0["ew"]:]
        for name in names:
            seg = pooled[name][seg0[name]:]
            windows[name].append({
                "sharpe": sharpe(seg),
                "ret": float(np.nansum(seg)),
                "beat_s": sharpe(seg) > sharpe(base),
                "beat_r": float(np.nansum(seg)) > float(np.nansum(base)),
            })
        start += TEST

    bakeoff = {}
    base = pooled["ew"]
    for name in names:
        series = pooled[name]
        ws = windows[name]
        mdd = max_dd(series)
        mdd_ew = max_dd(base)
        sh = sharpe(series)
        sh_ew = sharpe(base)
        ret = float(np.nansum(series))
        ret_ew = float(np.nansum(base))
        pf = float(np.mean([1 if (w["beat_s"] and w["beat_r"]) else 0 for w in ws])) if ws else 0
        pf_s = float(np.mean([1 if w["beat_s"] else 0 for w in ws])) if ws else 0
        inv = float(np.mean(invested[name])) if invested[name] else 0
        risk_pass = (
            sh > sh_ew + 0.15
            and mdd > mdd_ew + 0.05  # less negative
            and ret >= 0.80 * ret_ew
            and pf_s >= 0.35
        )
        growth_pass = (
            sh > sh_ew + 0.15
            and ret > ret_ew
            and pf >= 0.30
            and mdd > mdd_ew - 0.08
        )
        ship = risk_pass or growth_pass
        bakeoff[name] = {
            "sharpe": round(sh, 3),
            "sharpe_ew": round(sh_ew, 3),
            "edge_sharpe": round(sh - sh_ew, 3),
            "ret": round(ret, 4),
            "ret_ew": round(ret_ew, 4),
            "mdd": round(mdd, 4),
            "mdd_ew": round(mdd_ew, 4),
            "invested_frac": round(inv, 3),
            "pass_frac_both": round(pf, 3),
            "pass_frac_sharpe": round(pf_s, 3),
            "risk_pass": bool(risk_pass),
            "growth_pass": bool(growth_pass),
            "ship_pass": bool(ship and name != "ew"),
        }

    winners = [k for k, v in bakeoff.items() if v.get("ship_pass")]
    ranked = sorted(
        [(k, v) for k, v in bakeoff.items() if k != "ew"],
        key=lambda kv: (
            kv[1].get("ship_pass"),
            kv[1].get("risk_pass") or kv[1].get("growth_pass"),
            kv[1].get("edge_sharpe", -999),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else (None, {})

    if winners:
        rec = "PROMISING"
        rec_text = (
            f"Ship candidates under RISK/GROWTH gates: {winners}. "
            "Research-only — candidate side sleeve or PHP risk overlay, not auto-wired."
        )
    else:
        rec = "FAIL"
        rec_text = (
            f"Round-3 FAIL on ship gates. Closest={best[0]} "
            f"edge={((best[1] or {}).get('edge_sharpe'))} "
            f"risk={((best[1] or {}).get('risk_pass'))} growth={((best[1] or {}).get('growth_pass'))}."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "design": {
            "T": T, "N": N, "top_q": TOP_Q, "cost": COST,
            "gates": {
                "risk": "sharpe>+0.15, MDD better by >=5pp, ret>=80% EW, sharpe-win windows>=35%",
                "growth": "sharpe>+0.15, ret>EW, both-win windows>=30%, MDD not >8pp worse",
            },
        },
        "bakeoff": bakeoff,
        "winners": winners,
        "best": {"name": best[0], **(best[1] or {})} if best[0] else None,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "R1 time-series FAIL; R2 found near-misses; R3 stress-tests them.",
            "Long-only. Standalone research.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching EXP-0023 Round-3 near-winner stress test")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# EXP-0023 Round-3 — Near-winner stress test",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        f"**Winners:** `{result.get('winners')}`",
        "",
        f"Gates: `{(result.get('design') or {}).get('gates')}`",
        "",
        "| Strategy | Sharpe | Edge | Ret | MDD | Inv% | Risk | Growth | Ship |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    items = sorted(
        (result.get("bakeoff") or {}).items(),
        key=lambda kv: kv[1].get("edge_sharpe", -999),
        reverse=True,
    )
    for name, b in items:
        lines.append(
            f"| {name} | {float(b.get('sharpe') or 0):.2f} | {float(b.get('edge_sharpe') or 0):+.2f} | "
            f"{float(b.get('ret') or 0):.3f} | {float(b.get('mdd') or 0):.1%} | "
            f"{float(b.get('invested_frac') or 0):.0%} | "
            f"{'YES' if b.get('risk_pass') else 'no'} | "
            f"{'YES' if b.get('growth_pass') else 'no'} | "
            f"{'YES' if b.get('ship_pass') else 'no'} |"
        )
    lines += ["", "## Notes", ""]
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "winners": result.get("winners"),
        "best": result.get("best"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
