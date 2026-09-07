"""
EXP-0022 — Daily Markov chain equity study (Modal / Polygon).

Standalone research. Does NOT touch Peak Hour / TSD live code.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0022/study_markov_daily_modal.py
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

APP_NAME = "q-alpha-exp022-markov-daily"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results.md"
OUT_JSON = EXP_DIR / "study_markov_daily_metrics.json"

app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "pandas", "numpy", "requests", "pytz", "tzdata",
])
polygon_secret = modal.Secret.from_name("polygon-api-key")

POLYGON = "https://api.polygon.io"
COST_PER_TRADE = 0.0015  # 0.15% round-trip per day in market (EXP law)
START = "2019-01-01"
END = "2026-09-05"
TRAIN_DAYS = 504  # ~2y trading days
TEST_DAYS = 63    # ~1 quarter
MIN_TRAIN_TRANS = 200
SLEEP = 0.12

# Liquid US universe — large + mid; SPY as index sleeve
UNIVERSE = [
    "SPY",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "NFLX",
    "AMD", "ORCL", "CRM", "ADBE", "CSCO", "INTC", "QCOM", "TXN", "AMAT", "MU",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "SCHW",
    "XOM", "CVX", "COP", "SLB", "OXY",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "AMGN", "TMO",
    "WMT", "COST", "HD", "TGT", "NKE", "SBUX", "MCD", "PG", "KO", "PEP",
    "CAT", "GE", "BA", "HON", "DE", "UPS", "FDX",
    "DIS", "CMCSA", "T", "VZ", "IBM", "NOW", "PANW", "CRWD",
    "UBER", "ABNB", "SHOP", "SQ", "COIN", "PLTR", "SNOW", "DDOG",
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
    """Fetch adjusted daily bars for one symbol."""
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
            bars.append({
                "date": d,
                "o": float(b["o"]),
                "h": float(b["h"]),
                "l": float(b["l"]),
                "c": float(b["c"]),
                "v": float(b.get("v") or 0),
            })
        except Exception:
            continue
    out["bars"] = bars
    out["ok"] = 1 if len(bars) >= 400 else 0
    return out


def _returns(closes: list[float]) -> list[float]:
    out = [float("nan")]
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1], closes[i]
        out.append((p1 / p0 - 1.0) if p0 > 0 else float("nan"))
    return out


def _edges_3(train_rets: list[float]) -> tuple[float, float]:
    """Symmetric flat band from train MAD-ish scale; fallback ±0.5%."""
    xs = sorted(x for x in train_rets if x == x)
    if len(xs) < 50:
        return (-0.005, 0.005)
    # Use 33rd/67th percentile of |r| as flat half-width floor, clipped
    abs_xs = sorted(abs(x) for x in xs)
    mid = abs_xs[len(abs_xs) // 3]
    half = max(0.003, min(0.012, mid))
    return (-half, half)


def _edges_5(train_rets: list[float]) -> list[float]:
    xs = sorted(x for x in train_rets if x == x)
    if len(xs) < 50:
        return [-0.02, -0.005, 0.005, 0.02]
    qs = [0.2, 0.4, 0.6, 0.8]
    edges = []
    for q in qs:
        idx = min(len(xs) - 1, max(0, int(q * (len(xs) - 1))))
        edges.append(xs[idx])
    # Ensure strictly increasing
    for i in range(1, len(edges)):
        if edges[i] <= edges[i - 1]:
            edges[i] = edges[i - 1] + 1e-6
    return edges


def _state_3(r: float, lo: float, hi: float) -> int | None:
    if r != r:
        return None
    if r < lo:
        return 0  # Down
    if r > hi:
        return 2  # Up
    return 1  # Flat


def _state_5(r: float, edges: list[float]) -> int | None:
    if r != r:
        return None
    for i, e in enumerate(edges):
        if r <= e:
            return i
    return len(edges)


def _tpm(states: list[int], n_states: int) -> list[list[float]]:
    counts = [[0.0] * n_states for _ in range(n_states)]
    for a, b in zip(states, states[1:]):
        if a is None or b is None:
            continue
        counts[a][b] += 1.0
    tpm = []
    for row in counts:
        s = sum(row)
        if s <= 0:
            tpm.append([1.0 / n_states] * n_states)
        else:
            tpm.append([c / s for c in row])
    return tpm


def _tpm2(states: list[int], n_states: int) -> dict[tuple[int, int], list[float]]:
    """Order-2: P(s_{t+1} | s_{t-1}, s_t)."""
    counts: dict[tuple[int, int], list[float]] = {}
    for i in range(2, len(states)):
        a, b, c = states[i - 2], states[i - 1], states[i]
        if a is None or b is None or c is None:
            continue
        key = (a, b)
        if key not in counts:
            counts[key] = [0.0] * n_states
        counts[key][c] += 1.0
    out: dict[tuple[int, int], list[float]] = {}
    for key, row in counts.items():
        s = sum(row)
        out[key] = [c / s for c in row] if s > 0 else [1.0 / n_states] * n_states
    return out


def _uncond_up(tpm: list[list[float]], up_idx: int) -> float:
    # Stationary approx: average P(up|.)
    return sum(row[up_idx] for row in tpm) / max(len(tpm), 1)


def _sharpe(rets: list[float]) -> float:
    xs = [x for x in rets if x == x]
    if len(xs) < 5:
        return 0.0
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
    if v <= 0:
        return 0.0
    return (m / math.sqrt(v)) * math.sqrt(252.0)


def _max_dd(rets: list[float]) -> float:
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
    return mdd


def _eval_symbol_windows(
    dates: list[str],
    closes: list[float],
    *,
    n_states: int,
    order: int,
    tau_mode: str,
) -> dict[str, Any]:
    """Walk-forward evaluate one symbol."""
    rets = _returns(closes)
    n = len(closes)
    if n < TRAIN_DAYS + TEST_DAYS + 5:
        return {"ok": 0, "windows": [], "pooled": {}}

    windows = []
    pooled_markov: list[float] = []
    pooled_always: list[float] = []
    pooled_correct = 0
    pooled_pred = 0
    start = TRAIN_DAYS
    while start + TEST_DAYS <= n - 1:
        tr0, tr1 = start - TRAIN_DAYS, start
        te0, te1 = start, start + TEST_DAYS
        train_rets = rets[tr0 + 1:tr1]
        if n_states == 3:
            lo, hi = _edges_3(train_rets)
            edges5 = None
            up_idx = 2

            def st(r):
                return _state_3(r, lo, hi)
        else:
            edges5 = _edges_5(train_rets)
            lo = hi = None
            up_idx = n_states - 1

            def st(r):
                return _state_5(r, edges5)

        train_states = [st(r) for r in rets[tr0:tr1]]
        # drop leading nan state
        ts_clean = [s for s in train_states if s is not None]
        if len(ts_clean) < MIN_TRAIN_TRANS:
            start += TEST_DAYS
            continue

        tpm = _tpm([s for s in train_states], n_states)
        tpm2 = _tpm2([s for s in train_states], n_states) if order == 2 else {}
        uncond = _uncond_up(tpm, up_idx)
        if tau_mode == "uncond":
            tau = uncond
        elif tau_mode == "uncond_plus":
            tau = min(0.95, uncond + 0.05)
        else:
            tau = 0.40 if n_states == 3 else 0.28

        m_rets = []
        a_rets = []
        correct = 0
        pred_n = 0
        trades = 0
        for i in range(te0, te1):
            # State known at close of day i uses return i (close_i / close_{i-1} - 1)
            # Trade decision for day i+1 uses only info through close i (no look-ahead)
            if i + 1 >= n:
                break
            s_i = st(rets[i])
            if s_i is None:
                continue
            # Prediction of next state
            if order == 1:
                probs = tpm[s_i]
            else:
                s_im1 = st(rets[i - 1]) if i - 1 >= 0 else None
                if s_im1 is None or (s_im1, s_i) not in tpm2:
                    probs = tpm[s_i]
                else:
                    probs = tpm2[(s_im1, s_i)]
            pred = max(range(n_states), key=lambda k: probs[k])
            s_next = st(rets[i + 1])
            if s_next is not None:
                pred_n += 1
                if pred == s_next:
                    correct += 1

            p_up = probs[up_idx]
            day_ret = rets[i + 1]
            if day_ret != day_ret:
                continue
            # Always-long baseline (same days)
            a_rets.append(day_ret)
            if p_up >= tau:
                # Pay cost on each day we are long (conservative)
                m_rets.append(day_ret - COST_PER_TRADE)
                trades += 1
            else:
                m_rets.append(0.0)

        if len(m_rets) < 10:
            start += TEST_DAYS
            continue

        w = {
            "train_end": dates[tr1 - 1],
            "test_start": dates[te0],
            "test_end": dates[te1 - 1],
            "n_days": len(m_rets),
            "trades": trades,
            "trade_rate": trades / len(m_rets),
            "acc": (correct / pred_n) if pred_n else 0.0,
            "pred_n": pred_n,
            "sharpe_m": _sharpe(m_rets),
            "sharpe_a": _sharpe(a_rets),
            "ret_m": float(sum(m_rets)),
            "ret_a": float(sum(a_rets)),
            "mdd_m": _max_dd(m_rets),
            "tau": tau,
            "uncond_up": uncond,
            "pass": (
                _sharpe(m_rets) > _sharpe(a_rets)
                and sum(m_rets) > 0
                and (correct / pred_n if pred_n else 0) > (1.0 / n_states + 0.05)
            ),
        }
        windows.append(w)
        pooled_markov.extend(m_rets)
        pooled_always.extend(a_rets)
        pooled_correct += correct
        pooled_pred += pred_n
        start += TEST_DAYS

    if not windows:
        return {"ok": 0, "windows": [], "pooled": {}}

    n_pass = sum(1 for w in windows if w["pass"])
    pooled = {
        "n_windows": len(windows),
        "n_pass": n_pass,
        "pass_frac": n_pass / len(windows),
        "sharpe_m": _sharpe(pooled_markov),
        "sharpe_a": _sharpe(pooled_always),
        "ret_m": float(sum(pooled_markov)),
        "ret_a": float(sum(pooled_always)),
        "mdd_m": _max_dd(pooled_markov),
        "acc": (pooled_correct / pooled_pred) if pooled_pred else 0.0,
        "beat_always": _sharpe(pooled_markov) > _sharpe(pooled_always) and sum(pooled_markov) > sum(pooled_always),
    }
    return {"ok": 1, "windows": windows, "pooled": pooled}


def _aggregate(symbol_results: dict[str, dict], *, n_states: int, order: int, tau_mode: str) -> dict[str, Any]:
    rows = []
    for sym, res in symbol_results.items():
        if not res.get("ok"):
            continue
        p = res["pooled"]
        rows.append({"symbol": sym, **p})
    if not rows:
        return {
            "variant": f"s{n_states}_o{order}_{tau_mode}",
            "n_symbols": 0,
            "gate_pass": False,
        }

    def avg(key):
        return sum(r[key] for r in rows) / len(rows)

    # SPY sleeve separate
    spy = next((r for r in rows if r["symbol"] == "SPY"), None)
    non_spy = [r for r in rows if r["symbol"] != "SPY"]

    # Gate: mean pooled Sharpe beats always-long; positive ret; acc > chance+; ≥2/3 symbols beat always
    chance = 1.0 / n_states
    beat_n = sum(1 for r in non_spy if r.get("beat_always"))
    mean_acc = avg("acc")
    mean_sm = avg("sharpe_m")
    mean_sa = avg("sharpe_a")
    mean_rm = avg("ret_m")
    mean_pass_frac = avg("pass_frac")

    gate = (
        mean_sm > mean_sa
        and mean_rm > 0
        and mean_acc > chance + 0.05
        and mean_pass_frac >= (2.0 / 3.0) * 0.5  # average window pass soft
        and (beat_n / max(len(non_spy), 1)) >= 0.55
    )
    # Stricter primary gate for recommendation
    ship = (
        mean_sm > mean_sa + 0.15
        and mean_rm > avg("ret_a")
        and mean_acc > chance + 0.08
        and (beat_n / max(len(non_spy), 1)) >= 0.60
    )

    # Persistence diagnostic: fraction of symbols where Up→Up is modal from last window TPM
    # (computed lightly via pooled acc & trade rate)
    return {
        "variant": f"s{n_states}_o{order}_{tau_mode}",
        "n_symbols": len(rows),
        "n_non_spy": len(non_spy),
        "mean_sharpe_m": round(mean_sm, 3),
        "mean_sharpe_a": round(mean_sa, 3),
        "mean_ret_m": round(mean_rm, 4),
        "mean_ret_a": round(avg("ret_a"), 4),
        "mean_mdd_m": round(avg("mdd_m"), 4),
        "mean_acc": round(mean_acc, 4),
        "chance_acc": round(chance, 4),
        "mean_pass_frac": round(mean_pass_frac, 3),
        "frac_beat_always": round(beat_n / max(len(non_spy), 1), 3),
        "spy": spy,
        "gate_pass": bool(gate),
        "ship_pass": bool(ship),
        "top_beat": sorted(non_spy, key=lambda r: r["sharpe_m"] - r["sharpe_a"], reverse=True)[:8],
        "worst": sorted(non_spy, key=lambda r: r["sharpe_m"] - r["sharpe_a"])[:5],
    }


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 45)
def run_study() -> dict[str, Any]:
    """Fetch Polygon daily bars and run Markov walk-forward bakeoff."""
    t0 = time.time()
    print(f"Fetching {len(UNIVERSE)} symbols daily {START}→{END}")
    fetched = list(fetch_daily.map(UNIVERSE))
    by_sym = {r["symbol"]: r for r in fetched}
    ok_syms = [s for s, r in by_sym.items() if r.get("ok")]
    print(f"OK symbols: {len(ok_syms)}/{len(UNIVERSE)}")

    # Build per-symbol series
    series: dict[str, tuple[list[str], list[float]]] = {}
    for sym in ok_syms:
        bars = by_sym[sym]["bars"]
        dates = [b["date"] for b in bars]
        closes = [b["c"] for b in bars]
        series[sym] = (dates, closes)

    variants = [
        (3, 1, "uncond"),
        (3, 1, "uncond_plus"),
        (3, 1, "fixed"),
        (3, 2, "uncond"),
        (5, 1, "uncond"),
        (5, 1, "uncond_plus"),
        (5, 2, "uncond"),
    ]

    bakeoff = {}
    best = None
    for n_states, order, tau_mode in variants:
        print(f"Variant s{n_states} o{order} {tau_mode}…")
        per_sym = {}
        for sym, (dates, closes) in series.items():
            per_sym[sym] = _eval_symbol_windows(
                dates, closes, n_states=n_states, order=order, tau_mode=tau_mode,
            )
        agg = _aggregate(per_sym, n_states=n_states, order=order, tau_mode=tau_mode)
        # Keep slim per-symbol for best later
        agg["per_symbol_ok"] = sum(1 for v in per_sym.values() if v.get("ok"))
        bakeoff[agg["variant"]] = agg
        if best is None or (
            (agg["ship_pass"], agg["gate_pass"], agg["mean_sharpe_m"] - agg["mean_sharpe_a"])
            > (best["ship_pass"], best["gate_pass"], best["mean_sharpe_m"] - best["mean_sharpe_a"])
        ):
            best = agg

    # Persistence map on SPY + median stock using full-sample 3-state (diagnostic, labeled in-sample)
    def full_tpm_3(dates, closes):
        rets = _returns(closes)
        lo, hi = _edges_3([r for r in rets if r == r])
        states = [_state_3(r, lo, hi) for r in rets]
        states = [s for s in states if s is not None]
        return _tpm(states, 3), lo, hi

    spy_tpm = None
    if "SPY" in series:
        spy_tpm, spy_lo, spy_hi = full_tpm_3(*series["SPY"])
    else:
        spy_lo = spy_hi = None

    # Cross-sectional median Up→Up
    up_persist = []
    for sym, (dates, closes) in series.items():
        if sym == "SPY":
            continue
        tpm, _, _ = full_tpm_3(dates, closes)
        up_persist.append(tpm[2][2])
    up_persist.sort()
    med_up_up = up_persist[len(up_persist) // 2] if up_persist else None

    ship_any = any(v.get("ship_pass") for v in bakeoff.values())
    gate_any = any(v.get("gate_pass") for v in bakeoff.values())
    if ship_any:
        rec = "PROMISING"
        rec_text = (
            "At least one daily Markov variant beat always-long after costs on walk-forward. "
            "Candidate for (a) soft 1H lookback weight from Up-persistence, or (b) separate daily side agent — "
            "NOT an automatic Peak Hour rewrite."
        )
    elif gate_any:
        rec = "WEAK"
        rec_text = (
            "Soft gate pass only — edge thin / unstable. Hold for live. "
            "May still inform lookback diagnostics, not sizing."
        )
    else:
        rec = "FAIL"
        rec_text = (
            "No daily Markov long-only rule beat always-long after 0.15% costs on temporal walk-forward "
            "with required accuracy lift. Do not wire into Peak Hour or launch a side agent from this run."
        )

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "design": {
            "start": START,
            "end": END,
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "cost": COST_PER_TRADE,
            "universe_n": len(UNIVERSE),
            "ok_n": len(ok_syms),
        },
        "coverage": {
            "ok_symbols": len(ok_syms),
            "failed": [s for s in UNIVERSE if s not in ok_syms],
        },
        "bakeoff": bakeoff,
        "best_variant": best,
        "diagnostics": {
            "spy_tpm_3_insample": spy_tpm,
            "spy_flat_band": [spy_lo, spy_hi],
            "median_stock_up_to_up": med_up_up,
            "state_labels_3": ["Down", "Flat", "Up"],
        },
        "recommendation": rec,
        "recommendation_text": rec_text,
        "implications": {
            "php_1h_lookback": (
                "If Up→Up persistence >> uncond, use as soft prior weight on 1H continuation rank — "
                "only after a PROMISING/WEAK confirmed variant; never hard filter from FAIL."
            ),
            "side_agent": (
                "A separate daily Markov sleeve is only justified on PROMISING. "
                "Keep capital and kill logic isolated from Peak Hour."
            ),
        },
        "notes": [
            "States/edges fit on train window only — no look-ahead into test.",
            "Decision at day-t close → earn day t+1 return; cost charged each long day.",
            "Long-only; no short entries.",
            "Standalone from TSD / Peak Hour codebase.",
        ],
    }


@app.local_entrypoint()
def main():
    print("Launching Modal EXP-0022 daily Markov study")
    result = run_study.remote()
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0022 — Daily Markov Chain Equity Study",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s (Modal)",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Design",
        "",
        f"`{json.dumps(result.get('design'))}`",
        "",
        "## Coverage",
        "",
        f"- OK symbols: {(result.get('coverage') or {}).get('ok_symbols')}",
        f"- Failed: `{(result.get('coverage') or {}).get('failed')}`",
        "",
        "## Bakeoff (walk-forward, after costs)",
        "",
        "| Variant | n | Sharpe M | Sharpe Always | Ret M | Ret Always | Acc | Chance | Beat Always % | Gate | Ship |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name, b in (result.get("bakeoff") or {}).items():
        lines.append(
            f"| {name} | {b.get('n_symbols', 0)} | {float(b.get('mean_sharpe_m') or 0):.2f} | "
            f"{float(b.get('mean_sharpe_a') or 0):.2f} | {float(b.get('mean_ret_m') or 0):.3f} | "
            f"{float(b.get('mean_ret_a') or 0):.3f} | {float(b.get('mean_acc') or 0):.1%} | "
            f"{float(b.get('chance_acc') or 0):.1%} | {float(b.get('frac_beat_always') or 0):.0%} | "
            f"{'YES' if b.get('gate_pass') else 'no'} | {'YES' if b.get('ship_pass') else 'no'} |"
        )

    best = result.get("best_variant") or {}
    lines += [
        "",
        "## Best variant",
        "",
        f"- `{best.get('variant')}`",
        f"- Gate: **{best.get('gate_pass')}** · Ship: **{best.get('ship_pass')}**",
        f"- Mean Sharpe Markov {best.get('mean_sharpe_m')} vs always-long {best.get('mean_sharpe_a')}",
        f"- Acc {best.get('mean_acc')} vs chance {best.get('chance_acc')}",
        "",
        "### Top beaters (vs always-long)",
        "",
        "| Symbol | Sharpe M | Sharpe A | Acc | Pass frac |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in best.get("top_beat") or []:
        lines.append(
            f"| {r.get('symbol')} | {float(r.get('sharpe_m') or 0):.2f} | "
            f"{float(r.get('sharpe_a') or 0):.2f} | {float(r.get('acc') or 0):.1%} | "
            f"{float(r.get('pass_frac') or 0):.0%} |"
        )

    diag = result.get("diagnostics") or {}
    lines += [
        "",
        "## Diagnostics (in-sample TPM — descriptive only)",
        "",
        f"- SPY 3-state TPM: `{diag.get('spy_tpm_3_insample')}` (rows=Down/Flat/Up → cols)",
        f"- SPY flat band: `{diag.get('spy_flat_band')}`",
        f"- Median stock Up→Up persistence: `{diag.get('median_stock_up_to_up')}`",
        "",
        "## Implications",
        "",
        f"- **1H PHP lookback:** {(result.get('implications') or {}).get('php_1h_lookback')}",
        f"- **Side agent:** {(result.get('implications') or {}).get('side_agent')}",
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
        "best_variant": (result.get("best_variant") or {}).get("variant"),
        "ship_pass": (result.get("best_variant") or {}).get("ship_pass"),
        "gate_pass": (result.get("best_variant") or {}).get("gate_pass"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2))
