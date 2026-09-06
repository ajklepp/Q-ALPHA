"""
EXP-0021 Blind-spot #4 — Sector RS / peer co-move (Modal).

Polygon:
  - v3/reference/tickers/{sym} → sic_code
  - daily aggs for stock, SPY, sector ETFs

Causal features (prior closes only):
  rs_spy_5d / rs_spy_20d
  rs_sector_5d / rs_sector_20d  (SIC→XL* ETF map)
  peer_breadth_prior: fraction of same-SIC2 peers with + prior-day return
  alone_up: stock prior-day up while peer_breadth < 0.35

Bakeoff vs continuation_score v1.2. Research only until user approves.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0021/study_blindspot_04_sector_rs_modal.py
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

APP_NAME = "q-alpha-exp021-bs04-rs"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
ROOT = EXP_DIR.parents[1] if EXP_DIR.name == "EXP-0021" else Path("/data")

CORPUS_LOCAL = EXP_DIR / "corpus_htf_universe_social.csv"
SCORE_LOCAL = ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"
OUT_MD = EXP_DIR / "STUDY_BLINDSPOT_04_SECTOR_RS.md"
OUT_JSON = EXP_DIR / "study_blindspot_04_sector_rs_metrics.json"
CACHE_LOCAL = EXP_DIR / "blindspot_04_sector_rs_cache.json"

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
POLYGON = "https://api.polygon.io"
BENCH = ["SPY", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]


def sic_to_etf(sic: str | None) -> str:
    """Map SIC code → liquid sector ETF (coarse, research-grade)."""
    if not sic:
        return "SPY"
    s = str(sic).strip()
    if not s.isdigit():
        return "SPY"
    code = int(s[:2]) if len(s) >= 2 else int(s)
    if 10 <= code <= 14 or code == 29:
        return "XLE"
    if 15 <= code <= 17 or 30 <= code <= 39:
        if code in (35, 36):
            return "XLK"
        if code == 28:
            return "XLV"  # drugs/chemicals tilt health
        return "XLI"
    if 20 <= code <= 21:
        return "XLP"
    if 22 <= code <= 27 or code == 31:
        return "XLY"
    if 40 <= code <= 47:
        return "XLI"
    if 48 <= code <= 49:
        return "XLU" if code == 49 else "XLC"
    if 50 <= code <= 59:
        return "XLY"
    if 60 <= code <= 67:
        return "XLF"
    if 70 <= code <= 89:
        if code in (73, 737):
            return "XLK"
        if 80 <= code <= 8099:
            return "XLV"
        if code == 65 or code == 70:
            return "XLRE"
        return "XLC"
    return "SPY"


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
    return {"_error": str(last)[:120]}


@app.function(image=image, secrets=[polygon_secret], timeout=120, max_containers=40)
def fetch_symbol_meta(symbol: str) -> dict[str, Any]:
    """SIC + daily closes for one ticker."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    sym = symbol.upper()
    out: dict[str, Any] = {"symbol": sym, "sic": None, "sic2": None, "etf": "SPY", "closes": {}, "ok": 0}
    if not key:
        return out
    td = _get(f"{POLYGON}/v3/reference/tickers/{sym}", {"apiKey": key})
    time.sleep(0.12)
    res = td.get("results") or {}
    sic = str(res.get("sic_code") or "").strip() or None
    out["sic"] = sic
    out["sic2"] = (sic[:2] if sic and sic.isdigit() else None)
    out["etf"] = sic_to_etf(sic)
    out["sic_description"] = res.get("sic_description")

    start = "2026-04-01"
    end = "2026-09-05"
    ag = _get(
        f"{POLYGON}/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 200, "apiKey": key},
    )
    time.sleep(0.12)
    closes = {}
    for b in ag.get("results") or []:
        try:
            d = datetime.utcfromtimestamp(int(b["t"]) / 1000).date().isoformat()
            closes[d] = float(b["c"])
        except Exception:
            continue
    out["closes"] = closes
    out["ok"] = 1 if closes else 0
    return out


@app.function(image=image, secrets=[polygon_secret], timeout=180)
def fetch_benchmarks() -> dict[str, dict[str, float]]:
    """Daily closes for SPY + sector ETFs."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    out: dict[str, dict[str, float]] = {}
    for sym in BENCH:
        ag = _get(
            f"{POLYGON}/v2/aggs/ticker/{sym}/range/1/day/2026-04-01/2026-09-05",
            {"adjusted": "true", "sort": "asc", "limit": 200, "apiKey": key},
        )
        time.sleep(0.12)
        closes = {}
        for b in ag.get("results") or []:
            try:
                d = datetime.utcfromtimestamp(int(b["t"]) / 1000).date().isoformat()
                closes[d] = float(b["c"])
            except Exception:
                continue
        out[sym] = closes
    return out


def prior_dates(closes: dict[str, float], signal_date: str, n: int) -> list[str]:
    sd = signal_date[:10]
    keys = sorted(d for d in closes if d < sd)
    return keys[-n:] if keys else []


def ret_n(closes: dict[str, float], signal_date: str, n: int) -> float | None:
    """Return over last n prior sessions ending prior day (causal)."""
    keys = prior_dates(closes, signal_date, n + 1)
    if len(keys) < n + 1:
        return None
    a, b = closes[keys[0]], closes[keys[-1]]
    if a <= 0:
        return None
    return b / a - 1.0


def prior_1d(closes: dict[str, float], signal_date: str) -> float | None:
    keys = prior_dates(closes, signal_date, 2)
    if len(keys) < 2:
        return None
    a, b = closes[keys[-2]], closes[keys[-1]]
    if a <= 0:
        return None
    return b / a - 1.0


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 90, memory=4096)
def run_study() -> dict[str, Any]:
    """Attach RS/peer features; strata + bakeoff vs v1.2."""
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
    print(f"Fetching meta+closes for {len(symbols)} symbols + benchmarks…")

    benches = fetch_benchmarks.remote()
    fetched = list(fetch_symbol_meta.map(symbols, order_outputs=True, return_exceptions=True))
    meta: dict[str, Any] = {}
    err = 0
    for item in fetched:
        if isinstance(item, Exception):
            err += 1
            continue
        meta[str(item["symbol"]).upper()] = item
    print(f"meta ok={sum(1 for v in meta.values() if v.get('ok'))} errors={err}")

    # Prior-day returns table for peer breadth
    prior_ret: dict[tuple[str, str], float] = {}
    for sym, pack in meta.items():
        closes = pack.get("closes") or {}
        # all dates that appear as signal dates for this sym
        for sd in admits.loc[admits["symbol"] == sym, "signal_date"].astype(str).str[:10].unique():
            r1 = prior_1d(closes, sd)
            if r1 is not None:
                prior_ret[(sym, sd)] = r1

    # Peer universe: all symbols with meta, grouped by sic2
    by_sic2: dict[str, list[str]] = {}
    for sym, pack in meta.items():
        s2 = pack.get("sic2") or "NA"
        by_sic2.setdefault(str(s2), []).append(sym)

    rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        d["score_v12"] = float(compute_continuation_score_v1_1(d))
        sym = str(d["symbol"]).upper()
        sd = str(d["signal_date"])[:10]
        pack = meta.get(sym) or {}
        closes = pack.get("closes") or {}
        etf = pack.get("etf") or "SPY"
        etf_closes = benches.get(etf) or benches.get("SPY") or {}
        spy_closes = benches.get("SPY") or {}

        stock_5 = ret_n(closes, sd, 5)
        stock_20 = ret_n(closes, sd, 20)
        spy_5 = ret_n(spy_closes, sd, 5)
        spy_20 = ret_n(spy_closes, sd, 20)
        etf_5 = ret_n(etf_closes, sd, 5)
        etf_20 = ret_n(etf_closes, sd, 20)

        rs_spy_5 = (stock_5 - spy_5) if stock_5 is not None and spy_5 is not None else None
        rs_spy_20 = (stock_20 - spy_20) if stock_20 is not None and spy_20 is not None else None
        rs_sec_5 = (stock_5 - etf_5) if stock_5 is not None and etf_5 is not None else None
        rs_sec_20 = (stock_20 - etf_20) if stock_20 is not None and etf_20 is not None else None

        s2 = pack.get("sic2") or "NA"
        peers = [p for p in by_sic2.get(str(s2), []) if p != sym]
        peer_rets = [prior_ret[(p, sd)] for p in peers if (p, sd) in prior_ret]
        breadth = None
        if peer_rets:
            breadth = sum(1 for x in peer_rets if x > 0) / len(peer_rets)
        stock_prior = prior_ret.get((sym, sd))
        alone_up = 0
        if (
            stock_prior is not None
            and stock_prior > 0.01
            and breadth is not None
            and breadth < 0.35
        ):
            alone_up = 1

        d.update({
            "sic2": s2,
            "sector_etf": etf,
            "rs_spy_5d": rs_spy_5,
            "rs_spy_20d": rs_spy_20,
            "rs_sector_5d": rs_sec_5,
            "rs_sector_20d": rs_sec_20,
            "peer_n": len(peer_rets),
            "peer_breadth_prior": breadth,
            "alone_up": alone_up,
            "rs_ok": 1 if rs_spy_5 is not None else 0,
        })
        rows.append(d)

    scored = pd.DataFrame(rows)
    admits_s = scored[scored["all_hours_admit"] == 1].copy()
    labeled = admits_s[admits_s["rs_ok"] == 1]
    expanders = (
        admits_s.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    def strata_num(col: str, bins: list[tuple[str, float | None, float | None]]) -> list[dict]:
        out = []
        sub = labeled[labeled[col].notna()].copy()
        for name, lo, hi in bins:
            m = pd.Series(True, index=sub.index)
            if lo is not None:
                m &= sub[col] >= lo
            if hi is not None:
                m &= sub[col] < hi
            g = sub[m]
            if len(g) < 25:
                continue
            out.append({
                "bucket": name,
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()),
                "exp": float(g["r_multiple"].mean()),
                "med_mfe": float(g["mfe"].median()),
            })
        return out

    def strata_bool(col: str) -> list[dict]:
        out = []
        for val, g in labeled.groupby(col):
            if len(g) < 25:
                continue
            out.append({
                "bucket": str(val),
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()),
                "exp": float(g["r_multiple"].mean()),
                "med_mfe": float(g["mfe"].median()),
            })
        return out

    strata = {
        "rs_spy_5d": strata_num(
            "rs_spy_5d",
            [
                ("rs_spy_lag_< -3%", None, -0.03),
                ("rs_spy_flat", -0.03, 0.03),
                ("rs_spy_lead_>3%", 0.03, None),
            ],
        ),
        "rs_sector_5d": strata_num(
            "rs_sector_5d",
            [
                ("rs_sec_lag", None, -0.03),
                ("rs_sec_flat", -0.03, 0.03),
                ("rs_sec_lead", 0.03, None),
            ],
        ),
        "peer_breadth_prior": strata_num(
            "peer_breadth_prior",
            [
                ("breadth_<0.35", None, 0.35),
                ("breadth_0.35_0.6", 0.35, 0.6),
                ("breadth_>=0.6", 0.6, None),
            ],
        ),
        "alone_up": strata_bool("alone_up"),
    }

    base = scored.copy()
    m_v12 = metrics(simulate_slots(base, score_col="score_v12"), expanders)
    oos = base[base["signal_date"] >= OOS_CUT]
    m_v12_oos = metrics(simulate_slots(oos, score_col="score_v12"), expanders)

    def _f(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    def with_adj(fn):
        tmp = base.copy()
        tmp["score_x"] = [float(r["score_v12"]) + float(fn(r)) for _, r in tmp.iterrows()]
        m = metrics(simulate_slots(tmp, score_col="score_x"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_x"),
            expanders,
        )
        return m, o

    def skip_mask(mask):
        tmp = base.copy()
        tmp.loc[mask.fillna(False), "all_hours_admit"] = 0
        m = metrics(simulate_slots(tmp, score_col="score_v12"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_v12"),
            expanders,
        )
        return m, o

    def adj_rs_lead(r) -> float:
        if int(r.get("rs_ok") or 0) != 1:
            return 0.0
        rs = _f(r.get("rs_spy_5d"))
        if rs is None:
            return 0.0
        if rs >= 0.05:
            return 10.0
        if rs >= 0.03:
            return 5.0
        if rs <= -0.05:
            return -10.0
        return 0.0

    def adj_sector_lead(r) -> float:
        if int(r.get("rs_ok") or 0) != 1:
            return 0.0
        rs = _f(r.get("rs_sector_5d"))
        if rs is None:
            return 0.0
        if rs >= 0.04:
            return 8.0
        if rs <= -0.04:
            return -8.0
        return 0.0

    def adj_peer_pack(r) -> float:
        if int(r.get("alone_up") or 0) == 1:
            return -12.0
        br = _f(r.get("peer_breadth_prior"))
        if br is None:
            return 0.0
        if br >= 0.6:
            return 8.0
        if br < 0.35:
            return -5.0
        return 0.0

    def adj_combo(r) -> float:
        return adj_rs_lead(r) + 0.75 * adj_sector_lead(r) + adj_peer_pack(r)

    variants: dict[str, Any] = {"v12": {**m_v12, "oos": m_v12_oos, "pass_vs_v12": True}}
    for name, fn in [
        ("boost_rs_spy_lead", adj_rs_lead),
        ("boost_rs_sector_lead", adj_sector_lead),
        ("demote_alone_up", adj_peer_pack),
        ("combo_rs_peer", adj_combo),
    ]:
        m, o = with_adj(fn)
        variants[name] = {**m, "oos": o, "pass_vs_v12": ship_pass(m, m_v12)}

    for name, mask in [
        ("skip_alone_up", (base["all_hours_admit"] == 1) & (base["alone_up"] == 1)),
        ("skip_rs_spy_lag", (base["all_hours_admit"] == 1) & (base["rs_spy_5d"] <= -0.05)),
        ("skip_breadth_lt_0.35", (base["all_hours_admit"] == 1) & (base["peer_breadth_prior"] < 0.35)),
        ("prefer_breadth_ge_0.6", (base["all_hours_admit"] == 1) & ~(
            (base["peer_breadth_prior"].notna()) & (base["peer_breadth_prior"] >= 0.6)
        )),
    ]:
        m, o = skip_mask(mask)
        variants[name] = {**m, "oos": o, "pass_vs_v12": ship_pass(m, m_v12)}

    winners_raw = [k for k, v in variants.items() if k != "v12" and v.get("pass_vs_v12")]
    real_winners = []
    for k in winners_raw:
        v = variants[k]
        if (
            abs(float(v["exp"]) - float(m_v12["exp"])) < 1e-12
            and int(v["n"]) == int(m_v12["n"])
            and abs(float(v["capture"]) - float(m_v12["capture"])) < 1e-12
        ):
            continue
        real_winners.append(k)

    best = None
    if real_winners:
        best = max(
            real_winners,
            key=lambda k: float((variants[k].get("oos") or {}).get("exp", variants[k]["exp"])),
        )

    coverage = {
        "symbols": len(symbols),
        "meta_ok": int(sum(1 for v in meta.values() if v.get("ok"))),
        "fetch_errors": err,
        "admit_rows": int(len(admits_s)),
        "rs_labeled": int(len(labeled)),
        "rs_coverage": float(admits_s["rs_ok"].mean()),
        "alone_up_rate": float(admits_s["alone_up"].mean()),
    }

    if best is None:
        rec, rec_text = "HOLD", (
            "No sector-RS / peer variant beat v1.2 on the ship gate. Keep research-only."
        )
    elif str(best).startswith("skip_") or str(best).startswith("prefer_"):
        rec, rec_text = "HARD", f"Best real passer: **{best}**. Hard filter only if you approve."
    else:
        rec, rec_text = "SOFT", f"Best real passer: **{best}**. Soft overlay only if you approve."

    # slim cache for local (no full closes)
    slim = {
        s: {"sic": v.get("sic"), "sic2": v.get("sic2"), "etf": v.get("etf"), "ok": v.get("ok")}
        for s, v in meta.items()
    }

    return {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "coverage": coverage,
        "strata": strata,
        "baseline_v12": m_v12,
        "bakeoff": variants,
        "winners_raw": winners_raw,
        "winners": real_winners,
        "vacuous_winners": [k for k in winners_raw if k not in real_winners],
        "best_variant": best,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "SIC from Polygon ticker details; sector ETF via coarse SIC map.",
            "RS uses prior closes only (no look-ahead).",
            "Peer breadth = same SIC2 peers' prior-day up fraction.",
            "Baseline = live v1.2.",
        ],
        "cache": slim,
    }


@app.local_entrypoint()
def main():
    print("Launching Modal blind-spot #4: sector RS / peer co-move")
    result = run_study.remote()
    CACHE_LOCAL.write_text(
        json.dumps({"cache": result.get("cache") or {}, "meta": result.get("coverage")}, indent=2),
        encoding="utf-8",
    )
    metrics_out = {k: v for k, v in result.items() if k != "cache"}
    OUT_JSON.write_text(json.dumps(metrics_out, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0021 — Blind-spot #4: Sector RS / peer co-move",
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
        "- Lone-wolf spikes fade more than group moves (quant peer / industry momentum).",
        "- RS vs SPY / sector ETF + peer breadth as admit/rank context.",
        "- Live v1.2 has no sector/peer term today.",
        "",
        "## Strata (RS-labeled admits)",
        "",
    ]
    for key, title in [
        ("rs_spy_5d", "RS vs SPY (5d)"),
        ("rs_sector_5d", "RS vs sector ETF (5d)"),
        ("peer_breadth_prior", "Peer breadth (prior day)"),
        ("alone_up", "Alone-up (stock up, peers weak)"),
    ]:
        lines += [f"### {title}", "", "| Bucket | n | WR | Exp R | med MFE |", "|---|---:|---:|---:|---:|"]
        for r in (result.get("strata") or {}).get(key) or []:
            lines.append(
                f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
            )
        lines.append("")

    lines += [
        "## Bakeoff vs v1.2",
        "",
        "| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name, b in (result.get("bakeoff") or {}).items():
        oos = b.get("oos") if isinstance(b.get("oos"), dict) else {}
        flag = "—" if name == "v12" else ("YES" if b.get("pass_vs_v12") else "no")
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
        "coverage": result.get("coverage"),
        "baseline_exp": (result.get("baseline_v12") or {}).get("exp"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2))
