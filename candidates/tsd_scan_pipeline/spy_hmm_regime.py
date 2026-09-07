"""
SPY HMM regime — research display (EXP-0023).

2-state Gaussian HMM on SPY daily returns. Used for dashboard / pool snapshot
context only — does **not** gate Peak Hour entries.

Never returns UNKNOWN: on failure falls back to SPY vs SMA50, then BEAR.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

POLYGON = "https://api.polygon.io"
TRAIN_DAYS = 504
BULL_PROB = 0.55
SLEEP = 0.12


def _get(url: str, params: dict, retries: int = 3) -> dict:
    import requests

    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(0.6 * (i + 1))
                continue
            if r.status_code in (403, 404):
                return {"_status": r.status_code}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    return {"_error": str(last)[:160]}


def _fit_hmm2(obs: np.ndarray, n_iter: int = 12) -> dict[str, Any] | None:
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
                    log_xi[i, j] = (
                        log_alpha[t, i] + math.log(A[i, j] + 1e-12)
                        + logB[t + 1, j] + log_beta[t + 1, j]
                    )
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


def _filter_last(obs: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    mu, var, A, pi = model["mu"], model["var"], model["A"], model["pi"]
    prev = pi.copy()
    for t in range(len(obs)):
        if not np.isfinite(obs[t]):
            continue
        emit = np.array([
            math.exp(-0.5 * (math.log(2 * math.pi * var[k]) + (obs[t] - mu[k]) ** 2 / var[k]))
            for k in range(2)
        ])
        pred = A.T @ prev
        un = pred * emit
        s = un.sum()
        prev = un / s if s > 0 else pred
    return prev


def _sma50_fallback(closes: list[float]) -> tuple[str, dict[str, Any]]:
    if len(closes) < 50:
        return "BEAR", {"source": "fallback_default", "reason": "thin_history"}
    price = float(closes[-1])
    sma = sum(closes[-50:]) / 50.0
    label = "BULL" if price >= sma else "BEAR"
    return label, {
        "source": "sma50_fallback",
        "spy_price": price,
        "spy_sma50": sma,
    }


def fetch_spy_hmm_regime(api_key: str | None = None) -> dict[str, Any]:
    """
    Return SPY regime for dashboard.

    Always sets spy_regime to BULL or BEAR (never UNKNOWN / NO_KEY / ERR).
    """
    key = api_key or os.environ.get("POLYGON_API_KEY") or ""
    out: dict[str, Any] = {
        "spy_regime": "BEAR",
        "vix_regime": "NORMAL",
        "sizing_pct": "100%",
        "source": "fallback_default",
        "bull_prob": None,
        "model": "spy_hmm_v1",
        "research_only": True,
    }
    if not key:
        out["source"] = "fallback_default"
        out["reason"] = "no_api_key"
        return out

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=900)
    ag = _get(
        f"{POLYGON}/v2/aggs/ticker/SPY/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP)
    closes: list[float] = []
    for b in ag.get("results") or []:
        try:
            closes.append(float(b["c"]))
        except Exception:
            continue

    # VIX proxy from recent returns (same spirit as pre_market_scanner)
    if len(closes) >= 22:
        rets_short = []
        for i in range(1, 21):
            if closes[-i - 1] > 0:
                rets_short.append((closes[-i] - closes[-i - 1]) / closes[-i - 1])
        if rets_short:
            vol = float(np.std(rets_short, ddof=1) * math.sqrt(252) * 100)
            out["vix_proxy"] = round(vol, 1)
            out["vix_regime"] = "ELEVATED" if vol >= 25 else "NORMAL"

    if len(closes) < 80:
        label, meta = _sma50_fallback(closes)
        out.update(meta)
        out["spy_regime"] = label
        return out

    rets = np.full(len(closes), np.nan)
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets[i] = closes[i] / closes[i - 1] - 1.0
    train = rets[-TRAIN_DAYS:] if len(rets) >= TRAIN_DAYS else rets
    model = _fit_hmm2(train)
    if model is None:
        label, meta = _sma50_fallback(closes)
        out.update(meta)
        out["spy_regime"] = label
        return out

    filt = _filter_last(train, model)
    bull_idx = int(np.argmax(model["mu"]))
    bull_prob = float(filt[bull_idx])
    label = "BULL" if bull_prob >= BULL_PROB else "BEAR"
    out.update({
        "spy_regime": label,
        "source": "spy_hmm",
        "bull_prob": round(bull_prob, 4),
        "spy_price": float(closes[-1]),
        "state_means": [round(float(x), 6) for x in model["mu"]],
        "sizing_pct": "100%" if label == "BULL" else "research: defensive",
    })
    return out


def normalize_regime_label(raw: Any) -> str:
    """Map any stored/API label to BULL or BEAR for UI — never UNKNOWN."""
    s = str(raw or "").strip().upper()
    if s in ("BULL", "RISK_ON", "ON", "TRUE", "1"):
        return "BULL"
    if s in ("BEAR", "RISK_OFF", "OFF", "FALSE", "0"):
        return "BEAR"
    # UNKNOWN / NO_KEY / ERR / empty → defensive BEAR (caller should re-fetch HMM)
    return "BEAR"
