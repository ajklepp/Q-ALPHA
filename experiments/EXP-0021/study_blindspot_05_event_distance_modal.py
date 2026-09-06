"""
EXP-0021 Blind-spot #5 — Event distance / earnings calendar (Modal).

Data reality:
  - Benzinga earnings calendar: NOT AUTHORIZED (403)
  - TWS fundamentals: not allowed on this account
  - Proxy: Polygon vX quarterly financials `filing_date` as earnings-print date
  - Also use corpus `catalyst_type==earnings` when present

Causal features:
  days_since_earn  = signal_date − last filing_date < signal
  days_to_earn_est = next filing estimate from median gap between past filings
  pre_earn_5d / post_earn_3d flags

Bakeoff vs continuation_score v1.3. Research only until user approves.

Usage:
  .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0021/study_blindspot_05_event_distance_modal.py
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import modal

APP_NAME = "q-alpha-exp021-bs05-event"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
ROOT = EXP_DIR.parents[1] if EXP_DIR.name == "EXP-0021" else Path("/data")

CORPUS_LOCAL = EXP_DIR / "corpus_htf_universe_social.csv"
SCORE_LOCAL = ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"
OUT_MD = EXP_DIR / "STUDY_BLINDSPOT_05_EVENT_DISTANCE.md"
OUT_JSON = EXP_DIR / "study_blindspot_05_event_distance_metrics.json"
CACHE_LOCAL = EXP_DIR / "blindspot_05_event_cache.json"

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
DEFAULT_GAP_DAYS = 91  # quarterly cadence fallback


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
                time.sleep(0.5 * (i + 1))
                continue
            if r.status_code in (403, 404):
                return {"_status": r.status_code}
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.2 * (i + 1))
    return {"_error": str(last)[:120]}


@app.function(image=image, secrets=[polygon_secret], timeout=120, max_containers=40)
def fetch_filings(symbol: str) -> dict[str, Any]:
    """Quarterly filing dates for one ticker (earnings-print proxy)."""
    key = os.environ.get("POLYGON_API_KEY") or ""
    sym = symbol.upper()
    out: dict[str, Any] = {"symbol": sym, "filings": [], "ok": 0, "reason": ""}
    if not key:
        out["reason"] = "no_key"
        return out
    data = _get(
        f"{POLYGON}/vX/reference/financials",
        {
            "ticker": sym,
            "timeframe": "quarterly",
            "limit": 12,
            "sort": "filing_date",
            "order": "desc",
            "apiKey": key,
        },
    )
    time.sleep(0.12)
    if data.get("_status") == 403:
        out["reason"] = "forbidden"
        return out
    filings = []
    for row in data.get("results") or []:
        fd = str(row.get("filing_date") or "")[:10]
        if not fd or fd == "None":
            continue
        try:
            datetime.strptime(fd, "%Y-%m-%d")
        except Exception:
            continue
        filings.append(fd)
    filings = sorted(set(filings))
    out["filings"] = filings
    out["ok"] = 1 if filings else 0
    out["reason"] = "" if filings else "no_filings"
    return out


def event_feats(filings: list[str], signal_date: str) -> dict[str, Any]:
    """Causal days-since / days-to-est from filing history."""
    sd = datetime.strptime(signal_date[:10], "%Y-%m-%d").date()
    past = []
    for fd in filings:
        try:
            d = datetime.strptime(fd[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if d < sd:  # strict: filing known before signal day
            past.append(d)
    past = sorted(past)
    if not past:
        return {
            "event_ok": 0,
            "days_since_earn": None,
            "days_to_earn_est": None,
            "pre_earn_5d": 0,
            "post_earn_3d": 0,
            "earn_day": 0,
        }

    last = past[-1]
    days_since = (sd - last).days

    # Estimate next from median gap of past filings (causal history only)
    gaps = [(past[i] - past[i - 1]).days for i in range(1, len(past))]
    gap = int(statistics.median(gaps)) if gaps else DEFAULT_GAP_DAYS
    gap = max(60, min(120, gap))
    next_est = last + timedelta(days=gap)
    days_to = (next_est - sd).days

    return {
        "event_ok": 1,
        "days_since_earn": int(days_since),
        "days_to_earn_est": int(days_to),
        "pre_earn_5d": 1 if 0 < days_to <= 5 else 0,
        "post_earn_3d": 1 if 0 <= days_since <= 3 else 0,
        "earn_day": 1 if days_since == 0 else 0,  # rare with strict <
    }


@app.function(image=image, secrets=[polygon_secret], timeout=60 * 90, memory=4096)
def run_study() -> dict[str, Any]:
    """Attach event-distance features; strata + bakeoff vs v1.3."""
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
    print(f"Fetching quarterly filings for {len(symbols)} symbols…")

    fetched = list(fetch_filings.map(symbols, order_outputs=True, return_exceptions=True))
    cache: dict[str, Any] = {}
    err = 0
    for item in fetched:
        if isinstance(item, Exception):
            err += 1
            continue
        cache[str(item["symbol"]).upper()] = item
    ok_n = sum(1 for v in cache.values() if int(v.get("ok") or 0) == 1)
    print(f"filings ok={ok_n} errors={err}")

    rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        d["score_v13"] = float(compute_continuation_score_v1_1(d))
        sym = str(d["symbol"]).upper()
        sd = str(d["signal_date"])[:10]
        pack = cache.get(sym) or {}
        feat = event_feats(pack.get("filings") or [], sd)
        d.update(feat)
        ct = str(d.get("catalyst_type") or "").lower()
        d["catalyst_earnings"] = 1 if ct == "earnings" else 0
        rows.append(d)
    scored = pd.DataFrame(rows)

    admits_s = scored[scored["all_hours_admit"] == 1].copy()
    labeled = admits_s[admits_s["event_ok"] == 1]
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
            if len(g) < 20:
                continue
            out.append({
                "bucket": name,
                "n": int(len(g)),
                "wr": float(g["hit_1r"].mean()),
                "exp": float(g["r_multiple"].mean()),
                "med_mfe": float(g["mfe"].median()),
            })
        return out

    def strata_bool(col: str, src=None) -> list[dict]:
        out = []
        base = src if src is not None else labeled
        for val, g in base.groupby(col):
            if len(g) < 15:
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
        "days_since_earn": strata_num(
            "days_since_earn",
            [
                ("since_0_3", 0, 4),
                ("since_4_10", 4, 11),
                ("since_11_30", 11, 31),
                ("since_31_60", 31, 61),
                ("since_>=61", 61, None),
            ],
        ),
        "days_to_earn_est": strata_num(
            "days_to_earn_est",
            [
                ("to_1_5", 1, 6),
                ("to_6_15", 6, 16),
                ("to_16_45", 16, 46),
                ("to_>=46", 46, None),
            ],
        ),
        "pre_earn_5d": strata_bool("pre_earn_5d"),
        "post_earn_3d": strata_bool("post_earn_3d"),
        "catalyst_earnings": strata_bool("catalyst_earnings", src=admits_s),
    }

    base = scored.copy()
    m_v13 = metrics(simulate_slots(base, score_col="score_v13"), expanders)
    oos = base[base["signal_date"] >= OOS_CUT]
    m_v13_oos = metrics(simulate_slots(oos, score_col="score_v13"), expanders)

    def _f(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    def with_adj(fn):
        tmp = base.copy()
        tmp["score_x"] = [float(r["score_v13"]) + float(fn(r)) for _, r in tmp.iterrows()]
        m = metrics(simulate_slots(tmp, score_col="score_x"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_x"),
            expanders,
        )
        return m, o

    def skip_mask(mask):
        tmp = base.copy()
        tmp.loc[mask.fillna(False), "all_hours_admit"] = 0
        m = metrics(simulate_slots(tmp, score_col="score_v13"), expanders)
        o = metrics(
            simulate_slots(tmp[tmp["signal_date"] >= OOS_CUT], score_col="score_v13"),
            expanders,
        )
        return m, o

    def adj_pre_earn_demote(r) -> float:
        if int(r.get("event_ok") or 0) != 1:
            return 0.0
        if int(r.get("pre_earn_5d") or 0) == 1:
            return -12.0
        dto = _f(r.get("days_to_earn_est"))
        if dto is not None and 0 < dto <= 2:
            return -18.0
        return 0.0

    def adj_post_earn_boost(r) -> float:
        # PEAD-style: mild boost shortly after print
        if int(r.get("event_ok") or 0) != 1:
            return 0.0
        ds = _f(r.get("days_since_earn"))
        if ds is None:
            return 0.0
        if 1 <= ds <= 5:
            return 8.0
        if ds == 0:
            return -5.0  # print day chaos
        return 0.0

    def adj_catalyst_earn(r) -> float:
        if int(r.get("catalyst_earnings") or 0) == 1:
            return 6.0
        return 0.0

    def adj_combo(r) -> float:
        return adj_pre_earn_demote(r) + adj_post_earn_boost(r) + 0.5 * adj_catalyst_earn(r)

    variants: dict[str, Any] = {"v13": {**m_v13, "oos": m_v13_oos, "pass_vs_v13": True}}
    for name, fn in [
        ("demote_pre_earn", adj_pre_earn_demote),
        ("boost_post_earn", adj_post_earn_boost),
        ("boost_catalyst_earn", adj_catalyst_earn),
        ("combo_event", adj_combo),
    ]:
        m, o = with_adj(fn)
        variants[name] = {**m, "oos": o, "pass_vs_v13": ship_pass(m, m_v13)}

    for name, mask in [
        ("skip_pre_earn_5d", (base["all_hours_admit"] == 1) & (base["pre_earn_5d"] == 1)),
        ("skip_post_earn_3d", (base["all_hours_admit"] == 1) & (base["post_earn_3d"] == 1)),
        ("skip_since_0_3", (base["all_hours_admit"] == 1) & (base["days_since_earn"] >= 0) & (base["days_since_earn"] <= 3)),
        ("prefer_post_1_10", (base["all_hours_admit"] == 1) & ~(
            (base["days_since_earn"].notna())
            & (base["days_since_earn"] >= 1)
            & (base["days_since_earn"] <= 10)
        )),
    ]:
        m, o = skip_mask(mask)
        variants[name] = {**m, "oos": o, "pass_vs_v13": ship_pass(m, m_v13)}

    winners_raw = [k for k, v in variants.items() if k != "v13" and v.get("pass_vs_v13")]
    real_winners = []
    for k in winners_raw:
        v = variants[k]
        if (
            abs(float(v["exp"]) - float(m_v13["exp"])) < 1e-12
            and int(v["n"]) == int(m_v13["n"])
            and abs(float(v["capture"]) - float(m_v13["capture"])) < 1e-12
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
        "filings_ok": ok_n,
        "fetch_errors": err,
        "admit_rows": int(len(admits_s)),
        "event_labeled": int(len(labeled)),
        "event_coverage": float(admits_s["event_ok"].mean()),
        "pre_earn_rate": float(admits_s["pre_earn_5d"].mean()),
        "post_earn_rate": float(admits_s["post_earn_3d"].mean()),
        "catalyst_earn_n": int((admits_s["catalyst_earnings"] == 1).sum()),
        "source": "Polygon quarterly filing_date proxy (no Benzinga calendar)",
    }

    if ok_n < 50 or len(labeled) < 200:
        rec, rec_text = "HOLD", (
            "Event-distance coverage too thin or filings unavailable. Keep research-only."
        )
        best = None
        real_winners = []
    elif best is None:
        rec, rec_text = "HOLD", (
            "No event-distance variant beat v1.3 on the ship gate. Keep research-only."
        )
    elif str(best).startswith("skip_") or str(best).startswith("prefer_"):
        rec, rec_text = "HARD", f"Best real passer: **{best}**. Hard filter only if you approve."
    else:
        rec, rec_text = "SOFT", f"Best real passer: **{best}**. Soft overlay only if you approve."

    slim = {s: {"filings": v.get("filings"), "ok": v.get("ok")} for s, v in cache.items()}

    return {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "coverage": coverage,
        "strata": strata,
        "baseline_v13": m_v13,
        "bakeoff": variants,
        "winners_raw": winners_raw,
        "winners": real_winners,
        "vacuous_winners": [k for k in winners_raw if k not in real_winners],
        "best_variant": best,
        "recommendation": rec,
        "recommendation_text": rec_text,
        "notes": [
            "Benzinga earnings calendar not on Polygon plan; TWS fundamentals blocked.",
            "Proxy = quarterly filing_date; days_to_earn_est from median filing gap.",
            "Baseline = live v1.3 (gap soft-skip + RS soft).",
        ],
        "cache": slim,
    }


@app.local_entrypoint()
def main():
    print("Launching Modal blind-spot #5: event distance / earnings")
    result = run_study.remote()
    CACHE_LOCAL.write_text(
        json.dumps({"cache": result.get("cache") or {}, "meta": result.get("coverage")}, indent=2),
        encoding="utf-8",
    )
    metrics_out = {k: v for k, v in result.items() if k != "cache"}
    OUT_JSON.write_text(json.dumps(metrics_out, indent=2, default=str), encoding="utf-8")

    lines = [
        "# EXP-0021 — Blind-spot #5: Event distance / earnings",
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
        "- Pre-event anticipation vs post-print digest/PEAD timing.",
        "- No live Benzinga calendar — filing-date proxy only.",
        "- Live v1.3 has no event-distance term today.",
        "",
        "## Strata",
        "",
    ]
    for key, title in [
        ("days_since_earn", "Days since last filing (proxy earn)"),
        ("days_to_earn_est", "Days to estimated next filing"),
        ("pre_earn_5d", "Pre-earn window (≤5d to est)"),
        ("post_earn_3d", "Post-earn window (≤3d since)"),
        ("catalyst_earnings", "Corpus catalyst_type=earnings"),
    ]:
        lines += [f"### {title}", "", "| Bucket | n | WR | Exp R | med MFE |", "|---|---:|---:|---:|---:|"]
        for r in (result.get("strata") or {}).get(key) or []:
            lines.append(
                f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
            )
        lines.append("")

    lines += [
        "## Bakeoff vs v1.3",
        "",
        "| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name, b in (result.get("bakeoff") or {}).items():
        oos = b.get("oos") if isinstance(b.get("oos"), dict) else {}
        flag = "—" if name == "v13" else ("YES" if b.get("pass_vs_v13") else "no")
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
        "baseline_exp": (result.get("baseline_v13") or {}).get("exp"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD),
    }, indent=2))
