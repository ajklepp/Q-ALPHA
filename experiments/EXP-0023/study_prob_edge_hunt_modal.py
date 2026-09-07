"""
EXP-0023 — High-math probability edge hunt (Modal / Polygon).

Standalone. Does NOT touch Peak Hour / TSD live code.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0023/study_prob_edge_hunt_modal.py
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import modal

APP_NAME = "q-alpha-exp023-prob-edge"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results.md"
OUT_JSON = EXP_DIR / "study_prob_edge_metrics.json"

app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "numpy", "pandas", "requests", "pytz", "tzdata", "scipy",
])
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
COST = 0.0015
START = "2019-01-01"
END = "2026-09-05"
TRAIN_DAYS = 504
TEST_DAYS = 63
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
            bars.append({"date": d, "c": float(b["c"]), "v": float(b.get("v") or 0)})
        except Exception:
            continue
    out["bars"] = bars
    out["ok"] = 1 if len(bars) >= 400 else 0
    return out


# ---------------------------------------------------------------------------
# Math utilities (numpy)
# ---------------------------------------------------------------------------

def _np():
    import numpy as np
    return np


def sharpe(rets) -> float:
    np = _np()
    x = np.asarray(rets, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return 0.0
    s = float(x.std(ddof=1))
    if s <= 0:
        return 0.0
    return float(x.mean() / s * math.sqrt(252.0))


def max_dd(rets) -> float:
    np = _np()
    eq = np.cumprod(1.0 + np.asarray(rets, dtype=float))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1.0).min()) if len(eq) else 0.0


def apply_turnover_cost(pos, rets, *, entry_cost_once: bool = False) -> list[float]:
    """pos[t]=1 means long overnight into earning rets[t] (next-day return index aligned)."""
    np = _np()
    pos = np.asarray(pos, dtype=float)
    rets = np.asarray(rets, dtype=float)
    out = []
    prev = 0.0
    for i, (p, r) in enumerate(zip(pos, rets)):
        if not np.isfinite(r):
            out.append(0.0)
            prev = p
            continue
        c = 0.0
        if entry_cost_once:
            if i == 0 and p > 0:
                c += COST
        elif abs(p - prev) > 1e-9:
            c += COST * abs(p - prev)
        out.append(float(p * r - c))
        prev = p
    return out


def rolling_std(x, w: int):
    np = _np()
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(w - 1, len(x)):
        sl = x[i - w + 1:i + 1]
        if np.isfinite(sl).sum() >= w // 2:
            out[i] = np.nanstd(sl, ddof=1)
    return out


def rolling_mean(x, w: int):
    np = _np()
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(w - 1, len(x)):
        sl = x[i - w + 1:i + 1]
        if np.isfinite(sl).sum() >= w // 2:
            out[i] = np.nanmean(sl)
    return out


def sma(closes, w: int):
    return rolling_mean(closes, w)


def mutual_info_binary(x_bin, y_bin) -> float:
    """MI for two binary series (nats)."""
    np = _np()
    x = np.asarray(x_bin, dtype=int)
    y = np.asarray(y_bin, dtype=int)
    m = (x >= 0) & (y >= 0)
    x, y = x[m], y[m]
    if len(x) < 50:
        return 0.0
    joint = np.zeros((2, 2))
    for a, b in zip(x, y):
        joint[a, b] += 1.0
    joint /= joint.sum()
    px = joint.sum(axis=1)
    py = joint.sum(axis=0)
    mi = 0.0
    for i in range(2):
        for j in range(2):
            if joint[i, j] > 0 and px[i] > 0 and py[j] > 0:
                mi += joint[i, j] * math.log(joint[i, j] / (px[i] * py[j]))
    return float(mi)


def fit_gaussian_hmm2(obs, n_iter: int = 12):
    """2-state Gaussian HMM via EM. Returns (means, vars, trans, start, posteriors)."""
    np = _np()
    x = np.asarray(obs, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 80:
        return None
    # init by median split
    med = float(np.median(x))
    mu = np.array([x[x <= med].mean() if (x <= med).any() else x.mean(),
                   x[x > med].mean() if (x > med).any() else x.mean()], dtype=float)
    var = np.array([
        max(float(x[x <= med].var()) if (x <= med).sum() > 2 else float(x.var()), 1e-8),
        max(float(x[x > med].var()) if (x > med).sum() > 2 else float(x.var()), 1e-8),
    ])
    A = np.array([[0.9, 0.1], [0.1, 0.9]], dtype=float)
    pi = np.array([0.5, 0.5], dtype=float)

    def emit(xx):
        # log N
        out = np.zeros((len(xx), 2))
        for k in range(2):
            out[:, k] = -0.5 * (math.log(2 * math.pi * var[k]) + (xx - mu[k]) ** 2 / var[k])
        return out

    xx = np.asarray(obs, dtype=float)
    mask = np.isfinite(xx)
    # replace nan with 0 for EM path length continuity but ignore in M-step via weights
    xx2 = np.where(mask, xx, 0.0)
    T = len(xx2)
    gamma = np.zeros((T, 2))
    for _ in range(n_iter):
        logB = emit(xx2)
        # forward
        log_alpha = np.zeros((T, 2))
        log_alpha[0] = np.log(pi + 1e-12) + logB[0]
        for t in range(1, T):
            for j in range(2):
                log_alpha[t, j] = logB[t, j] + np.logaddexp(
                    log_alpha[t - 1, 0] + math.log(A[0, j] + 1e-12),
                    log_alpha[t - 1, 1] + math.log(A[1, j] + 1e-12),
                )
        # backward
        log_beta = np.zeros((T, 2))
        for t in range(T - 2, -1, -1):
            for i in range(2):
                log_beta[t, i] = np.logaddexp(
                    math.log(A[i, 0] + 1e-12) + logB[t + 1, 0] + log_beta[t + 1, 0],
                    math.log(A[i, 1] + 1e-12) + logB[t + 1, 1] + log_beta[t + 1, 1],
                )
        # gamma
        for t in range(T):
            log_g = log_alpha[t] + log_beta[t]
            log_g -= np.logaddexp(log_g[0], log_g[1])
            gamma[t] = np.exp(log_g)
        # xi for A
        num = np.zeros((2, 2))
        for t in range(T - 1):
            log_xi = np.zeros((2, 2))
            for i in range(2):
                for j in range(2):
                    log_xi[i, j] = (
                        log_alpha[t, i] + math.log(A[i, j] + 1e-12) + logB[t + 1, j] + log_beta[t + 1, j]
                    )
            m = np.max(log_xi)
            xi = np.exp(log_xi - m)
            xi /= xi.sum() + 1e-12
            num += xi
        A = num / (num.sum(axis=1, keepdims=True) + 1e-12)
        pi = gamma[0] / (gamma[0].sum() + 1e-12)
        # M-step means/vars using finite obs only
        for k in range(2):
            w = gamma[:, k] * mask
            sw = w.sum()
            if sw < 5:
                continue
            mu[k] = float((w * xx2).sum() / sw)
            var[k] = max(float((w * (xx2 - mu[k]) ** 2).sum() / sw), 1e-8)
    return {"mu": mu, "var": var, "A": A, "pi": pi, "gamma": gamma}


def filter_hmm_gamma(obs, model):
    """One-pass filtered state probs using fixed params (causal)."""
    np = _np()
    if model is None:
        return None
    mu, var, A, pi = model["mu"], model["var"], model["A"], model["pi"]
    xx = np.asarray(obs, dtype=float)
    T = len(xx)
    filt = np.full((T, 2), np.nan)
    # recursive filter
    prev = pi.copy()
    for t in range(T):
        if not np.isfinite(xx[t]):
            filt[t] = prev
            continue
        emit = np.zeros(2)
        for k in range(2):
            emit[k] = math.exp(
                -0.5 * (math.log(2 * math.pi * var[k]) + (xx[t] - mu[k]) ** 2 / var[k])
            )
        pred = A.T @ prev
        unnorm = pred * emit
        s = unnorm.sum()
        prev = unnorm / s if s > 0 else pred
        filt[t] = prev
    return filt


def kalman_trend(closes, q=1e-5, r=1e-3):
    """Local linear trend; returns trend component (causal)."""
    np = _np()
    y = np.asarray(closes, dtype=float)
    n = len(y)
    level = np.full(n, np.nan)
    trend = np.full(n, np.nan)
    if not np.isfinite(y[0]):
        return trend
    x = np.array([y[0], 0.0])  # level, slope
    P = np.eye(2)
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([1.0, 0.0])
    Q = q * np.array([[1.0, 0.5], [0.5, 1.0]])
    for t in range(n):
        # predict
        x = F @ x
        P = F @ P @ F.T + Q
        if not np.isfinite(y[t]):
            level[t], trend[t] = x[0], x[1]
            continue
        # update
        inn = y[t] - H @ x
        S = H @ P @ H.T + r
        K = (P @ H) / S
        x = x + K * inn
        P = (np.eye(2) - np.outer(K, H)) @ P
        level[t], trend[t] = x[0], x[1]
    return trend


def cusum_state(rets, threshold: float):
    """+1 in up regime, 0 in down; causal CUSUM."""
    np = _np()
    x = np.asarray(rets, dtype=float)
    pos = np.zeros(len(x))
    gp = gn = 0.0
    state = 1
    for i, r in enumerate(x):
        if not np.isfinite(r):
            pos[i] = state
            continue
        gp = max(0.0, gp + r)
        gn = min(0.0, gn + r)
        if gp > threshold:
            state = 1
            gp = 0.0
        elif gn < -threshold:
            state = 0
            gn = 0.0
        pos[i] = state
    return pos


def rolling_entropy_signs(rets, w: int = 20):
    np = _np()
    x = np.asarray(rets, dtype=float)
    out = np.full(len(x), np.nan)
    for i in range(w - 1, len(x)):
        sl = x[i - w + 1:i + 1]
        sl = sl[np.isfinite(sl)]
        if len(sl) < w // 2:
            continue
        p = (sl > 0).mean()
        p = min(max(p, 1e-6), 1 - 1e-6)
        out[i] = float(-(p * math.log(p) + (1 - p) * math.log(1 - p)))
    return out


# ---------------------------------------------------------------------------
# Signal builders: return position array aligned so pos[i] decided at close i
# earns rets[i+1]. We build pos for indices 0..n-2 corresponding to decisions.
# ---------------------------------------------------------------------------

def build_signals(closes, spy_closes=None) -> dict[str, Any]:
    np = _np()
    c = np.asarray(closes, dtype=float)
    n = len(c)
    rets = np.full(n, np.nan)
    rets[1:] = c[1:] / c[:-1] - 1.0

    sig: dict[str, np.ndarray] = {}
    # decision at i uses info through i; length n (last unused)
    always = np.ones(n)
    sig["always_long"] = always

    # momentum
    for w in (10, 20, 60):
        mom = np.full(n, np.nan)
        for i in range(w, n):
            if c[i - w] > 0 and np.isfinite(c[i]):
                mom[i] = c[i] / c[i - w] - 1.0
        sig[f"mom_{w}"] = (mom > 0).astype(float)

    # SMA trend (close vs SMA using closes through i)
    for w in (50, 100):
        s = sma(c, w)
        sig[f"sma_{w}"] = ((c > s) & np.isfinite(s)).astype(float)

    # vol filter: low vol
    vol20 = rolling_std(rets, 20)
    sig["vol_low_20"] = np.zeros(n)
    # filled later with train median in WF

    # z-score MR
    m20 = rolling_mean(c, 20)
    s20 = rolling_std(c, 20)
    z = (c - m20) / np.where(s20 > 0, s20, np.nan)
    sig["z_mr_neg"] = (z < -1.25).astype(float)
    sig["z_mr_deep"] = (z < -1.75).astype(float)

    # conditional bounce / momentum
    sig["cond_bounce"] = np.zeros(n)  # filled in WF with train sigma
    sig["cond_cont"] = np.zeros(n)
    sig["streak_bounce"] = np.zeros(n)
    down1 = (rets < 0).astype(float)
    streak = np.zeros(n)
    run = 0
    for i in range(n):
        if np.isfinite(rets[i]) and rets[i] < 0:
            run += 1
        else:
            run = 0
        streak[i] = run
    sig["_streak"] = streak
    sig["_rets"] = rets
    sig["_vol20"] = vol20
    sig["_z"] = z

    # entropy + mom
    ent = rolling_entropy_signs(rets, 20)
    mom20 = sig["mom_20"]
    # low entropy ~ more predictable signs; with positive mom
    sig["entropy_trend"] = ((ent < math.log(2) * 0.85) & (mom20 > 0)).astype(float)

    # Kalman trend
    kt = kalman_trend(c)
    sig["kalman_up"] = (kt > 0).astype(float)

    # CUSUM placeholder threshold set in WF
    sig["_cusum_raw"] = rets

    # Bayes P(up) rolling
    bayes = np.zeros(n)
    a, b = 2.0, 2.0  # prior
    # will reset each WF window; store helper only
    sig["_for_bayes"] = rets

    # RS vs SPY
    if spy_closes is not None and len(spy_closes) == n:
        sc = np.asarray(spy_closes, dtype=float)
        sr = np.full(n, np.nan)
        sr[1:] = sc[1:] / sc[:-1] - 1.0
        # 20d RS
        rs = np.full(n, np.nan)
        for i in range(20, n):
            if sc[i - 20] > 0 and c[i - 20] > 0:
                rs[i] = (c[i] / c[i - 20] - 1.0) - (sc[i] / sc[i - 20] - 1.0)
        sig["rs_spy_20"] = (rs > 0).astype(float)
        sig["rs_spy_strong"] = (rs > 0.03).astype(float)
        # stock up with SPY up
        sig["co_up"] = ((rets > 0) & (sr > 0)).astype(float)
        sig["_spy_rets"] = sr
    else:
        sig["rs_spy_20"] = np.zeros(n)
        sig["rs_spy_strong"] = np.zeros(n)
        sig["co_up"] = np.zeros(n)

    # EVT skip: long unless left-tail day
    sig["evt_skip"] = np.ones(n)  # punched in WF

    # HMM placeholders
    sig["_closes"] = c
    return sig


def wf_positions_for_window(
    sig: dict,
    tr0: int,
    tr1: int,
    te0: int,
    te1: int,
    name: str,
) -> Any:
    """Build positions for test days te0..te1-1 (decision indices)."""
    np = _np()
    rets = sig["_rets"]
    n = len(rets)
    pos = np.zeros(n)

    train_rets = rets[tr0 + 1:tr1]
    train_rets = train_rets[np.isfinite(train_rets)]
    sig_train = float(np.std(train_rets)) if len(train_rets) > 10 else 0.01
    vol_train = sig["_vol20"][tr0:tr1]
    vol_med = float(np.nanmedian(vol_train)) if np.isfinite(vol_train).any() else 0.01

    if name == "always_long":
        pos[te0:te1] = 1.0
        return pos

    if name.startswith("mom_") or name.startswith("sma_") or name in (
        "z_mr_neg", "z_mr_deep", "entropy_trend", "kalman_up",
        "rs_spy_20", "rs_spy_strong", "co_up",
    ):
        raw = sig[name]
        pos[te0:te1] = raw[te0:te1]
        return pos

    if name == "vol_low_20":
        v = sig["_vol20"]
        pos[te0:te1] = ((v[te0:te1] < vol_med) & np.isfinite(v[te0:te1])).astype(float)
        return pos

    if name == "vol_high_skip":
        # always long except high vol
        v = sig["_vol20"]
        pos[te0:te1] = ((v[te0:te1] <= vol_med * 1.25) | ~np.isfinite(v[te0:te1])).astype(float)
        return pos

    if name == "cond_bounce":
        thr = -1.5 * sig_train
        pos[te0:te1] = (rets[te0:te1] < thr).astype(float)
        return pos

    if name == "cond_cont":
        thr = 1.5 * sig_train
        pos[te0:te1] = (rets[te0:te1] > thr).astype(float)
        return pos

    if name == "streak_bounce":
        st = sig["_streak"]
        pos[te0:te1] = (st[te0:te1] >= 2).astype(float)
        return pos

    if name == "evt_skip":
        thr = -2.5 * sig_train
        # long unless today is extreme down
        pos[te0:te1] = (rets[te0:te1] > thr).astype(float)
        return pos

    if name == "bayes_pup":
        # causal beta update using train prior counts + stream
        a = 2.0 + float((train_rets > 0).sum())
        b = 2.0 + float((train_rets <= 0).sum())
        for i in range(te0, te1):
            p_up = a / (a + b)
            pos[i] = 1.0 if p_up >= 0.55 else 0.0
            r = rets[i]
            if np.isfinite(r):
                if r > 0:
                    a += 1.0
                else:
                    b += 1.0
        return pos

    if name == "cusum_long":
        thr = max(2.0 * sig_train, 0.02)
        # fit path from train start for continuity
        full = cusum_state(rets, thr)
        pos[te0:te1] = full[te0:te1]
        return pos

    if name in ("hmm_bull", "hmm_lowvol"):
        model = fit_gaussian_hmm2(rets[tr0:tr1])
        filt = filter_hmm_gamma(rets, model)
        if filt is None:
            return pos
        # identify bull = higher mean; lowvol = lower var
        if name == "hmm_bull":
            bull = int(np.argmax(model["mu"]))
            pos[te0:te1] = (filt[te0:te1, bull] >= 0.55).astype(float)
        else:
            lowv = int(np.argmin(model["var"]))
            pos[te0:te1] = (filt[te0:te1, lowv] >= 0.55).astype(float)
        return pos

    if name == "mi_lag_rule":
        # on train, pick lag 1..5 maximizing MI(sign(r_{t-lag}), sign(r_t))
        best_lag, best_mi = 1, -1.0
        for lag in range(1, 6):
            xs, ys = [], []
            for i in range(tr0 + lag, tr1):
                if np.isfinite(rets[i]) and np.isfinite(rets[i - lag]):
                    xs.append(1 if rets[i - lag] > 0 else 0)
                    ys.append(1 if rets[i] > 0 else 0)
            mi = mutual_info_binary(xs, ys)
            if mi > best_mi:
                best_mi, best_lag = mi, lag
        up1 = up0 = n1 = n0 = 0
        for i in range(tr0 + best_lag, tr1):
            if not (np.isfinite(rets[i]) and np.isfinite(rets[i - best_lag])):
                continue
            if rets[i - best_lag] > 0:
                n1 += 1
                up1 += int(rets[i] > 0)
            else:
                n0 += 1
                up0 += int(rets[i] > 0)
        p1 = up1 / max(n1, 1)
        p0 = up0 / max(n0, 1)
        follow = p1 >= p0
        for i in range(te0, te1):
            j = i - best_lag
            if j < 0 or not np.isfinite(rets[j]):
                pos[i] = 0.0
                continue
            up_signal = rets[j] > 0
            if follow:
                pos[i] = 1.0 if up_signal else 0.0
            else:
                pos[i] = 0.0 if up_signal else 1.0
        return pos

    if name == "trend_vol_combo":
        s = sig["sma_50"]
        v = sig["_vol20"]
        pos[te0:te1] = ((s[te0:te1] > 0) & (v[te0:te1] < vol_med * 1.1)).astype(float)
        return pos

    if name == "hmm_bull_vol":
        # intersection
        pb = wf_positions_for_window(sig, tr0, tr1, te0, te1, "hmm_bull")
        pv = wf_positions_for_window(sig, tr0, tr1, te0, te1, "vol_low_20")
        pos[te0:te1] = ((pb[te0:te1] > 0) & (pv[te0:te1] > 0)).astype(float)
        return pos

    return pos


STRATEGIES = [
    "always_long",
    "mom_10", "mom_20", "mom_60",
    "sma_50", "sma_100",
    "vol_low_20", "vol_high_skip",
    "z_mr_neg", "z_mr_deep",
    "cond_bounce", "cond_cont", "streak_bounce",
    "evt_skip",
    "bayes_pup",
    "cusum_long",
    "entropy_trend",
    "kalman_up",
    "rs_spy_20", "rs_spy_strong", "co_up",
    "mi_lag_rule",
    "hmm_bull", "hmm_lowvol",
    "trend_vol_combo", "hmm_bull_vol",
]


def eval_symbol(closes, spy_closes=None) -> dict[str, Any]:
    np = _np()
    c = np.asarray(closes, dtype=float)
    n = len(c)
    if n < TRAIN_DAYS + TEST_DAYS + 5:
        return {"ok": 0}
    sig = build_signals(c, spy_closes)
    rets = sig["_rets"]

    results = {name: {"windows": [], "pooled_m": [], "pooled_a": []} for name in STRATEGIES}
    # Cache HMM models per train window to avoid double fit (bull + lowvol + combo)
    hmm_cache: dict[tuple[int, int], Any] = {}

    def hmm_model(tr0, tr1):
        key = (tr0, tr1)
        if key not in hmm_cache:
            hmm_cache[key] = fit_gaussian_hmm2(rets[tr0:tr1])
        return hmm_cache[key]

    start = TRAIN_DAYS
    while start + TEST_DAYS <= n - 1:
        tr0, tr1 = start - TRAIN_DAYS, start
        te0, te1 = start, start + TEST_DAYS
        always_pos = np.ones(te1 - te0)
        day_rets = np.array([rets[i + 1] if i + 1 < n else np.nan for i in range(te0, te1)])
        a_pnls = apply_turnover_cost(always_pos, day_rets, entry_cost_once=True)

        # precompute HMM filter once per window
        model = hmm_model(tr0, tr1)
        filt = filter_hmm_gamma(rets, model) if model is not None else None
        bull_idx = int(np.argmax(model["mu"])) if model is not None else 0
        lowv_idx = int(np.argmin(model["var"])) if model is not None else 0

        for name in STRATEGIES:
            if name == "hmm_bull" and filt is not None:
                pos_full = np.zeros(n)
                pos_full[te0:te1] = (filt[te0:te1, bull_idx] >= 0.55).astype(float)
                pos = pos_full[te0:te1]
            elif name == "hmm_lowvol" and filt is not None:
                pos_full = np.zeros(n)
                pos_full[te0:te1] = (filt[te0:te1, lowv_idx] >= 0.55).astype(float)
                pos = pos_full[te0:te1]
            elif name == "hmm_bull_vol" and filt is not None:
                pos_full = wf_positions_for_window(sig, tr0, tr1, te0, te1, "vol_low_20")
                pos = ((filt[te0:te1, bull_idx] >= 0.55) & (pos_full[te0:te1] > 0)).astype(float)
            else:
                pos_full = wf_positions_for_window(sig, tr0, tr1, te0, te1, name)
                pos = pos_full[te0:te1]

            if name == "always_long":
                pnls = a_pnls
            else:
                pnls = apply_turnover_cost(pos, day_rets, entry_cost_once=False)
            rate = float(np.mean(pos)) if len(pos) else 0.0
            w = {
                "sharpe": sharpe(pnls),
                "ret": float(np.nansum(pnls)),
                "mdd": max_dd(pnls),
                "rate": rate,
                "beat": sharpe(pnls) > sharpe(a_pnls) and float(np.nansum(pnls)) > float(np.nansum(a_pnls)),
                "pass": (
                    sharpe(pnls) > sharpe(a_pnls)
                    and float(np.nansum(pnls)) > 0
                    and 0.15 <= rate <= 0.95
                ),
            }
            results[name]["windows"].append(w)
            results[name]["pooled_m"].extend(pnls)
            results[name]["pooled_a"].extend(a_pnls)

        start += TEST_DAYS

    out = {"ok": 1, "strategies": {}}
    for name, pack in results.items():
        if not pack["windows"]:
            continue
        n_pass = sum(1 for w in pack["windows"] if w["pass"])
        out["strategies"][name] = {
            "n_windows": len(pack["windows"]),
            "pass_frac": n_pass / len(pack["windows"]),
            "sharpe": sharpe(pack["pooled_m"]),
            "sharpe_a": sharpe(pack["pooled_a"]),
            "ret": float(np.nansum(pack["pooled_m"])),
            "ret_a": float(np.nansum(pack["pooled_a"])),
            "mdd": max_dd(pack["pooled_m"]),
            "mean_rate": float(np.mean([w["rate"] for w in pack["windows"]])),
            "frac_beat": float(np.mean([1.0 if w["beat"] else 0.0 for w in pack["windows"]])),
            "beat_always": sharpe(pack["pooled_m"]) > sharpe(pack["pooled_a"])
            and float(np.nansum(pack["pooled_m"])) > float(np.nansum(pack["pooled_a"])),
        }
    return out


def aggregate(per_symbol: dict[str, dict]) -> dict[str, Any]:
    np = _np()
    # exclude SPY from equity mean; keep spy metrics separate
    names = [s for s in STRATEGIES]
    rows = {n: [] for n in names}
    spy_row = {}
    for sym, res in per_symbol.items():
        if not res.get("ok"):
            continue
        for n, m in (res.get("strategies") or {}).items():
            if sym == "SPY":
                spy_row[n] = m
            else:
                rows[n].append(m)

    bakeoff = {}
    for n, lst in rows.items():
        if not lst:
            continue
        beat_n = sum(1 for m in lst if m.get("beat_always"))
        mean_s = float(np.mean([m["sharpe"] for m in lst]))
        mean_sa = float(np.mean([m["sharpe_a"] for m in lst]))
        mean_r = float(np.mean([m["ret"] for m in lst]))
        mean_ra = float(np.mean([m["ret_a"] for m in lst]))
        mean_rate = float(np.mean([m["mean_rate"] for m in lst]))
        mean_pf = float(np.mean([m["pass_frac"] for m in lst]))
        frac_beat = beat_n / len(lst)
        gate = (
            mean_s > mean_sa + 0.10
            and mean_r > mean_ra
            and frac_beat >= 0.50
            and 0.15 <= mean_rate <= 0.95
            and mean_pf >= 0.25
        )
        ship = (
            mean_s > mean_sa + 0.20
            and mean_r > mean_ra
            and frac_beat >= 0.55
            and 0.20 <= mean_rate <= 0.90
            and mean_pf >= 0.35
        )
        bakeoff[n] = {
            "n": len(lst),
            "mean_sharpe": round(mean_s, 3),
            "mean_sharpe_a": round(mean_sa, 3),
            "mean_ret": round(mean_r, 4),
            "mean_ret_a": round(mean_ra, 4),
            "mean_rate": round(mean_rate, 3),
            "mean_pass_frac": round(mean_pf, 3),
            "frac_beat_always": round(frac_beat, 3),
            "spy": spy_row.get(n),
            "gate_pass": bool(gate),
            "ship_pass": bool(ship),
            "edge": round(mean_s - mean_sa, 3),
        }
    return bakeoff


@app.function(image=image, timeout=900, max_containers=40)
def eval_symbol_job(payload: dict) -> dict[str, Any]:
    """Parallel per-symbol walk-forward eval."""
    sym = payload["symbol"]
    try:
        res = eval_symbol(payload["closes"], payload.get("spy_closes"))
        res["symbol"] = sym
        return res
    except Exception as exc:
        return {"symbol": sym, "ok": 0, "error": str(exc)[:200]}


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 45)
def run_study() -> dict[str, Any]:
    np = _np()
    t0 = time.time()
    print(f"Fetching {len(UNIVERSE)} symbols…")
    fetched = list(fetch_daily.map(UNIVERSE))
    by = {r["symbol"]: r for r in fetched}
    ok = [s for s, r in by.items() if r.get("ok")]
    print(f"OK {len(ok)}/{len(UNIVERSE)}")

    spy_bars = by["SPY"]["bars"]
    spy_dates = [b["date"] for b in spy_bars]
    spy_map = {b["date"]: b["c"] for b in spy_bars}

    payloads = []
    for sym in ok:
        bars = by[sym]["bars"]
        cmap = {b["date"]: b["c"] for b in bars}
        dates = [d for d in spy_dates if d in cmap]
        if len(dates) < TRAIN_DAYS + TEST_DAYS + 5:
            continue
        payloads.append({
            "symbol": sym,
            "closes": [cmap[d] for d in dates],
            "spy_closes": [spy_map[d] for d in dates],
        })

    print(f"Evaluating {len(payloads)} symbols in parallel…")
    evaluated = list(eval_symbol_job.map(payloads))
    per_symbol = {r["symbol"]: r for r in evaluated if r.get("symbol")}

    bakeoff = aggregate(per_symbol)
    winners_gate = [k for k, v in bakeoff.items() if v.get("gate_pass") and k != "always_long"]
    winners_ship = [k for k, v in bakeoff.items() if v.get("ship_pass") and k != "always_long"]
    ranked = sorted(
        [(k, v) for k, v in bakeoff.items() if k != "always_long"],
        key=lambda kv: (kv[1].get("ship_pass"), kv[1].get("gate_pass"), kv[1].get("edge", -999)),
        reverse=True,
    )
    best = ranked[0] if ranked else (None, {})

    ensemble_names = winners_ship or winners_gate
    if not ensemble_names:
        ensemble_names = [
            k for k, v in ranked
            if 0.15 <= float(v.get("mean_rate") or 0) <= 0.95
        ][:3]

    ens_metrics = None
    if ensemble_names:
        ens_rows = []
        for sym, res in per_symbol.items():
            if sym == "SPY" or not res.get("ok"):
                continue
            members = [res["strategies"][n] for n in ensemble_names if n in (res.get("strategies") or {})]
            if not members:
                continue
            ens_rows.append({
                "sharpe": float(np.mean([m["sharpe"] for m in members])),
                "sharpe_a": float(np.mean([m["sharpe_a"] for m in members])),
                "ret": float(np.mean([m["ret"] for m in members])),
                "ret_a": float(np.mean([m["ret_a"] for m in members])),
                "beat_always": all(m.get("beat_always") for m in members),
            })
        if ens_rows:
            mean_s = float(np.mean([r["sharpe"] for r in ens_rows]))
            mean_sa = float(np.mean([r["sharpe_a"] for r in ens_rows]))
            mean_r = float(np.mean([r["ret"] for r in ens_rows]))
            mean_ra = float(np.mean([r["ret_a"] for r in ens_rows]))
            frac_beat = float(np.mean([1 if r["beat_always"] else 0 for r in ens_rows]))
            ens_metrics = {
                "members": ensemble_names,
                "mean_sharpe": round(mean_s, 3),
                "mean_sharpe_a": round(mean_sa, 3),
                "mean_ret": round(mean_r, 4),
                "mean_ret_a": round(mean_ra, 4),
                "edge": round(mean_s - mean_sa, 3),
                "frac_beat_always": round(frac_beat, 3),
                "note": "soft ensemble = average of member pooled metrics",
            }

    if winners_ship:
        rec = "PROMISING"
        rec_text = (
            f"Ship-gate cleared by: {winners_ship}. "
            "True edge vs always-long after flip-costs on walk-forward. "
            "Still research-only — do not auto-wire Peak Hour."
        )
    elif winners_gate:
        rec = "WEAK"
        rec_text = (
            f"Soft gate only: {winners_gate}. Edge thin — hold for live; candidate for more scrutiny."
        )
    else:
        rec = "FAIL"
        rec_text = (
            "No high-math family beat always-long after costs on temporal walk-forward with "
            "required breadth. Closest: "
            f"{best[0]} (edge={((best[1] or {}).get('edge'))}). "
            "Do not add a side agent or PHP weight from this hunt."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "design": {
            "start": START, "end": END,
            "train_days": TRAIN_DAYS, "test_days": TEST_DAYS,
            "cost_on": "position_flips", "cost": COST,
            "strategies": STRATEGIES,
            "ok_n": len([1 for r in per_symbol.values() if r.get("ok")]),
        },
        "bakeoff": bakeoff,
        "winners_gate": winners_gate,
        "winners_ship": winners_ship,
        "best": {"name": best[0], **(best[1] or {})} if best[0] else None,
        "ensemble": ens_metrics,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Long-only. No short entries.",
            "HMM/Bayes/CUSUM/MI/Kalman/EVT/RS/vol/momentum/MR battery.",
            "Costs on flips; always-long pays one entry cost per test window.",
            "Standalone from Peak Hour live code.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching EXP-0023 high-math probability edge hunt")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0023 — High-math probability edge hunt",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        f"**Ship winners:** `{result.get('winners_ship')}`",
        f"**Gate winners:** `{result.get('winners_gate')}`",
        "",
        "## Bakeoff vs always-long (flip costs)",
        "",
        "| Strategy | n | Sharpe | Always | Edge | Ret | Ret A | Rate | Beat% | Gate | Ship |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    items = sorted(
        (result.get("bakeoff") or {}).items(),
        key=lambda kv: kv[1].get("edge", -999),
        reverse=True,
    )
    for name, b in items:
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('mean_sharpe') or 0):.2f} | "
            f"{float(b.get('mean_sharpe_a') or 0):.2f} | {float(b.get('edge') or 0):+.2f} | "
            f"{float(b.get('mean_ret') or 0):.3f} | {float(b.get('mean_ret_a') or 0):.3f} | "
            f"{float(b.get('mean_rate') or 0):.0%} | {float(b.get('frac_beat_always') or 0):.0%} | "
            f"{'YES' if b.get('gate_pass') else 'no'} | {'YES' if b.get('ship_pass') else 'no'} |"
        )
    lines += [
        "",
        "## Ensemble",
        "",
        f"`{json.dumps(result.get('ensemble'))}`",
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
        "winners_ship": result.get("winners_ship"),
        "winners_gate": result.get("winners_gate"),
        "best": result.get("best"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2, default=str))
