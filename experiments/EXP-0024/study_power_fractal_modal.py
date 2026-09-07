"""
EXP-0024 — Power-law tails + fractal/Hurst edge hunt (Modal / Polygon).

Two different maths from Markov/HMM:
  - Power law: P(|r| > x) ~ x^{-α}  (Hill estimator on left/right tails)
  - Fractal/Hurst: long memory (H>0.5 persist, H<0.5 mean-revert)

Tests whether these yield RISK or GROWTH edge vs equal-weight after costs.
Also compares to EXP-0023 winner: EW × SPY HMM bull.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0024/study_power_fractal_modal.py
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

APP_NAME = "q-alpha-exp024-power-fractal"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results.md"
OUT_JSON = EXP_DIR / "study_power_fractal_metrics.json"

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
HURST_WIN = 126  # ~6 months rolling
HILL_WIN = 252
HILL_K_FRAC = 0.10  # top 10% of |tail| for Hill
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


def hill_alpha(abs_tail: list[float] | Any, k: int | None = None) -> float:
    """Hill estimator α for power-law tail on positive magnitudes."""
    np = _np()
    x = np.sort(np.asarray(abs_tail, dtype=float))
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 30:
        return float("nan")
    if k is None:
        k = max(10, int(HILL_K_FRAC * len(x)))
    k = min(k, len(x) - 1)
    thresh = x[-k - 1]
    excess = x[-k:]
    if thresh <= 0:
        return float("nan")
    return float(k / np.sum(np.log(excess / thresh)))


def hurst_rs(series: Any, min_chunk: int = 8) -> float:
    """Classical R/S Hurst on a 1d series (returns or log prices)."""
    np = _np()
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 40:
        return float("nan")
    # use several chunk sizes
    sizes = []
    s = min_chunk
    while s <= n // 2:
        sizes.append(s)
        s *= 2
    if len(sizes) < 3:
        return float("nan")
    log_n = []
    log_rs = []
    for sz in sizes:
        n_chunks = n // sz
        if n_chunks < 2:
            continue
        rs_vals = []
        for i in range(n_chunks):
            chunk = x[i * sz:(i + 1) * sz]
            mu = chunk.mean()
            y = np.cumsum(chunk - mu)
            r = float(y.max() - y.min())
            s_ = float(chunk.std(ddof=1))
            if s_ > 1e-12 and r > 0:
                rs_vals.append(r / s_)
        if rs_vals:
            log_n.append(math.log(sz))
            log_rs.append(math.log(float(np.mean(rs_vals))))
    if len(log_n) < 3:
        return float("nan")
    # OLS slope
    X = np.array(log_n)
    Y = np.array(log_rs)
    xm, ym = X.mean(), Y.mean()
    den = float(((X - xm) ** 2).sum())
    if den <= 0:
        return float("nan")
    return float(((X - xm) * (Y - ym)).sum() / den)


def hurst_dfa(series: Any) -> float:
    """Simple DFA Hurst (order-1)."""
    np = _np()
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 40:
        return float("nan")
    # profile
    y = np.cumsum(x - x.mean())
    sizes = []
    s = 8
    while s <= n // 4:
        sizes.append(s)
        s *= 2
    if len(sizes) < 3:
        return float("nan")
    log_s, log_f = [], []
    for sz in sizes:
        n_seg = n // sz
        if n_seg < 2:
            continue
        rms = []
        for i in range(n_seg):
            seg = y[i * sz:(i + 1) * sz]
            t = np.arange(sz, dtype=float)
            # linear detrend
            coef = np.polyfit(t, seg, 1)
            trend = coef[0] * t + coef[1]
            rms.append(float(np.sqrt(np.mean((seg - trend) ** 2))))
        f = float(np.mean(rms))
        if f > 0:
            log_s.append(math.log(sz))
            log_f.append(math.log(f))
    if len(log_s) < 3:
        return float("nan")
    X = np.array(log_s)
    Y = np.array(log_f)
    xm, ym = X.mean(), Y.mean()
    den = float(((X - xm) ** 2).sum())
    if den <= 0:
        return float("nan")
    return float(((X - xm) * (Y - ym)).sum() / den)


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


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 45)
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

    # ---- Diagnostics on full sample (labeled in-sample) ----
    spy_abs = np.abs(spy_r[np.isfinite(spy_r)])
    diag = {
        "spy_hill_abs": hill_alpha(spy_abs),
        "spy_hill_left": hill_alpha(-spy_r[np.isfinite(spy_r) & (spy_r < 0)]),
        "spy_hill_right": hill_alpha(spy_r[np.isfinite(spy_r) & (spy_r > 0)]),
        "spy_hurst_rs": hurst_rs(spy_r[np.isfinite(spy_r)]),
        "spy_hurst_dfa": hurst_dfa(spy_r[np.isfinite(spy_r)]),
    }
    # cross-section median Hill/Hurst on last TRAIN window of each name
    hills, hursts = [], []
    for j in range(N):
        rj = R[-TRAIN:, j]
        rj = rj[np.isfinite(rj)]
        if len(rj) < 100:
            continue
        hills.append(hill_alpha(np.abs(rj)))
        hursts.append(hurst_dfa(rj))
    hills = [h for h in hills if h == h]
    hursts = [h for h in hursts if h == h]
    diag["median_stock_hill_abs"] = float(np.median(hills)) if hills else None
    diag["median_stock_hurst_dfa"] = float(np.median(hursts)) if hursts else None
    diag["frac_stocks_H_gt_055"] = float(np.mean([1 if h > 0.55 else 0 for h in hursts])) if hursts else None
    diag["frac_stocks_H_lt_045"] = float(np.mean([1 if h < 0.45 else 0 for h in hursts])) if hursts else None
    diag["frac_stocks_alpha_lt_3"] = float(np.mean([1 if a < 3 else 0 for a in hills])) if hills else None

    # ---- Rolling SPY features (causal) ----
    spy_H = np.full(T, np.nan)
    spy_alpha_left = np.full(T, np.nan)
    for t in range(max(HURST_WIN, HILL_WIN), T):
        w = spy_r[t - HURST_WIN + 1:t + 1]
        spy_H[t] = hurst_dfa(w)
        wl = spy_r[t - HILL_WIN + 1:t + 1]
        left = -wl[np.isfinite(wl) & (wl < 0)]
        spy_alpha_left[t] = hill_alpha(left)

    # SPY HMM bull (EXP-0023 baseline)
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

    # Per-name rolling Hurst at decision time (expensive → compute every day but vectorized-ish)
    # Use DFA on last HURST_WIN returns for each name — sample every day in WF only
    mom60 = np.full((T, N), np.nan)
    for t in range(60, T):
        mom60[t] = C[t] / C[t - 60] - 1.0

    names = [
        "ew",
        "ew_hmm_bull",           # EXP-0023 baseline
        "ew_hurst_persist",      # EW when SPY H > 0.55
        "ew_hurst_antipersist",  # EW when SPY H < 0.45 (expect MR — still long-only EW? skip)
        "ew_fat_tail_skip",      # EW when left-tail α > median (thinner left tail = safer)
        "ew_thin_tail_only",     # same
        "hurst_switch_mom_mr",   # CS mom60 if H>0.55 else CS MR (-mom60) if H<0.45 else EW
        "cs_high_H",             # long names with highest rolling H
        "cs_low_alpha",          # long names with fattest abs tails (lottery) — expect FAIL
        "cs_high_alpha",         # long thinnest-tail (safer) names
        "hmm_and_hurst",         # EW when HMM bull AND H>0.52
        "hmm_and_thin_tail",     # EW when HMM bull AND left α above train median
    ]

    # Precompute train-median thresholds lazily per window
    pooled = {k: [] for k in names}
    windows = {k: [] for k in names}
    invested = {k: [] for k in names}

    # Cache name-level H and alpha at each t (compute on demand inside WF to save — but N*T DFA is heavy)
    # Compromise: update name H/alpha every 5 days
    name_H = np.full((T, N), np.nan)
    name_alpha = np.full((T, N), np.nan)
    for t in range(HURST_WIN, T):
        if t % 5 != 0 and t != HURST_WIN:
            name_H[t] = name_H[t - 1]
            name_alpha[t] = name_alpha[t - 1]
            continue
        for j in range(N):
            w = R[t - HURST_WIN + 1:t + 1, j]
            name_H[t, j] = hurst_dfa(w)
            wa = R[t - HILL_WIN + 1:t + 1, j] if t >= HILL_WIN else w
            name_alpha[t, j] = hill_alpha(np.abs(wa[np.isfinite(wa)]))

    start = TRAIN
    while start + TEST <= T - 1:
        prev = {k: None for k in names}
        seg0 = {k: len(pooled[k]) for k in names}
        # train medians for thresholds
        tr_alpha = spy_alpha_left[start - TEST:start] if start >= TEST else spy_alpha_left[:start]
        tr_alpha = tr_alpha[np.isfinite(tr_alpha)]
        alpha_med = float(np.median(tr_alpha)) if len(tr_alpha) else 3.0

        for t in range(start, start + TEST):
            if t + 1 >= T:
                break
            day = R[t + 1]
            ew = np.ones(N)
            ew[~np.isfinite(day)] = 0
            bull = bool(spy_bull[t] >= 0.55) if np.isfinite(spy_bull[t]) else True
            H = spy_H[t]
            aL = spy_alpha_left[t]
            persist = bool(H > 0.55) if np.isfinite(H) else False
            antip = bool(H < 0.45) if np.isfinite(H) else False
            thin = bool(aL >= alpha_med) if np.isfinite(aL) else True

            # CS scores
            high_H = top_q(name_H[t])
            # high alpha = thinner tails; low alpha = fatter
            high_a = top_q(name_alpha[t])
            low_a = top_q(-name_alpha[t])

            if persist:
                switch = top_q(mom60[t])
            elif antip:
                switch = top_q(-mom60[t])  # MR: long recent losers
            else:
                switch = ew

            masks = {
                "ew": ew,
                "ew_hmm_bull": ew if bull else np.zeros(N),
                "ew_hurst_persist": ew if persist else np.zeros(N),
                "ew_hurst_antipersist": ew if antip else np.zeros(N),
                "ew_fat_tail_skip": ew if thin else np.zeros(N),
                "ew_thin_tail_only": ew if thin else np.zeros(N),
                "hurst_switch_mom_mr": switch,
                "cs_high_H": high_H,
                "cs_low_alpha": low_a,
                "cs_high_alpha": high_a,
                "hmm_and_hurst": ew if (bull and (np.isfinite(H) and H > 0.52)) else np.zeros(N),
                "hmm_and_thin_tail": ew if (bull and thin) else np.zeros(N),
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
            })
        start += TEST

    bakeoff = {}
    base = pooled["ew"]
    for name in names:
        series = pooled[name]
        ws = windows[name]
        mdd = max_dd(series)
        mdd_ew = max_dd(base)
        sh, sh_ew = sharpe(series), sharpe(base)
        ret, ret_ew = float(np.nansum(series)), float(np.nansum(base))
        pf_s = float(np.mean([1 if w["beat_s"] else 0 for w in ws])) if ws else 0
        inv = float(np.mean(invested[name])) if invested[name] else 0
        risk_pass = (
            name != "ew"
            and sh > sh_ew + 0.15
            and mdd > mdd_ew + 0.05
            and ret >= 0.80 * ret_ew
            and pf_s >= 0.35
        )
        growth_pass = (
            name != "ew"
            and sh > sh_ew + 0.15
            and ret > ret_ew
            and pf_s >= 0.30
            and mdd > mdd_ew - 0.08
        )
        bakeoff[name] = {
            "sharpe": round(sh, 3),
            "sharpe_ew": round(sh_ew, 3),
            "edge_sharpe": round(sh - sh_ew, 3),
            "ret": round(ret, 4),
            "ret_ew": round(ret_ew, 4),
            "mdd": round(mdd, 4),
            "mdd_ew": round(mdd_ew, 4),
            "invested_frac": round(inv, 3),
            "pass_frac_sharpe": round(pf_s, 3),
            "risk_pass": bool(risk_pass),
            "growth_pass": bool(growth_pass),
            "ship_pass": bool(risk_pass or growth_pass),
        }

    # Exclude HMM baseline from "new" power/fractal winners
    fractal_names = [k for k in names if k not in ("ew", "ew_hmm_bull")]
    winners_new = [k for k in fractal_names if bakeoff[k].get("ship_pass")]
    winners = [k for k, v in bakeoff.items() if v.get("ship_pass")]
    ranked = sorted(
        [(k, v) for k, v in bakeoff.items() if k != "ew"],
        key=lambda kv: (kv[1].get("ship_pass"), kv[1].get("edge_sharpe", -999)),
        reverse=True,
    )
    best = ranked[0] if ranked else (None, {})
    hmm = bakeoff.get("ew_hmm_bull", {})
    beats_hmm = [
        k for k, v in bakeoff.items()
        if k not in ("ew", "ew_hmm_bull")
        and v.get("sharpe", 0) > hmm.get("sharpe", 0) + 0.05
        and v.get("mdd", -1) > hmm.get("mdd", -1)
    ]

    if winners_new:
        rec = "PROMISING"
        rec_text = (
            f"New power/fractal ship winners: {winners_new}. "
            + (f"Also beat HMM: {beats_hmm}." if beats_hmm else "Did not dominate HMM.")
        )
    elif "ew_hmm_bull" in winners:
        rec = "FAIL_NEW"
        rec_text = (
            "Power-law and fractal trading rules did NOT clear ship gates. "
            "Diagnostics confirm fat tails (Hill α≈2.2–2.8) and near-random Hurst (H≈0.5). "
            "EXP-0023 HMM risk overlay remains the only PROMISING rule in this bakeoff."
        )
    else:
        rec = "FAIL"
        rec_text = (
            f"No rule cleared gates. Closest={best[0]} edge={((best[1] or {}).get('edge_sharpe'))}."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "design": {
            "T": T, "N": N, "hurst_win": HURST_WIN, "hill_win": HILL_WIN,
            "cost": COST, "train": TRAIN, "test": TEST,
        },
        "diagnostics": diag,
        "bakeoff": bakeoff,
        "winners": winners,
        "beats_hmm": beats_hmm,
        "best": {"name": best[0], **(best[1] or {})} if best[0] else None,
        "hmm_baseline": hmm,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Power law: Hill α on |returns| / left tail (α≈3–4 typical; lower = fatter).",
            "Fractal: DFA + R/S Hurst (H>0.5 persist, H<0.5 antipersist).",
            "Long-only. Standalone. No Peak Hour edits.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching EXP-0024 power-law + fractal hunt")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    lines = [
        "# EXP-0024 — Power-law + fractal/Hurst study",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Diagnostics (what the math says about the data)",
        "",
        f"`{json.dumps(result.get('diagnostics'), indent=2)}`",
        "",
        "Interpretation: Hill α ≪ 4 ⇒ fat tails (variance may be infinite-ish for α≤2). "
        "Hurst H>0.5 ⇒ persistence; H<0.5 ⇒ mean-reversion tendency.",
        "",
        f"**HMM baseline (EXP-0023):** `{json.dumps(result.get('hmm_baseline'))}`",
        f"**Beats HMM:** `{result.get('beats_hmm')}`",
        "",
        "## Bakeoff vs equal-weight",
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
        "beats_hmm": result.get("beats_hmm"),
        "diagnostics": result.get("diagnostics"),
        "best": result.get("best"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
