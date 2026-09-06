"""
EXP-0021 Blind-spot #3 — Options via TWS (IV / HV).

TWS (127.0.0.1:7497) provides causal daily:
  OPTION_IMPLIED_VOLATILITY on the underlying
  HISTORICAL_VOLATILITY on the underlying

Polygon options snapshots are NOT on this plan; Modal cannot reach TWS.
This study runs LOCALLY with TWS open.

Features (prior session only — no look-ahead):
  iv_prior, hv_prior, iv_hv_spread, iv_rank_20, iv_chg_5d

Bakeoff vs continuation_score v1.2. Research only until user approves.

Usage (TWS paper API on 7497):
  .\\venv\\Scripts\\python.exe experiments/EXP-0021/study_blindspot_03_options_tws.py
  .\\venv\\Scripts\\python.exe experiments/EXP-0021/study_blindspot_03_options_tws.py --max-symbols 80
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[1]
CAND = ROOT / "candidates"
sys.path.insert(0, str(CAND))

from tsd_scan_pipeline.tsd_launch_score import compute_continuation_score_v1_1  # noqa: E402

ET = pytz.timezone("America/New_York")
CORPUS = EXP_DIR / "corpus_htf_universe_social.csv"
OUT_MD = EXP_DIR / "STUDY_BLINDSPOT_03_OPTIONS.md"
OUT_JSON = EXP_DIR / "study_blindspot_03_options_metrics.json"
CACHE = EXP_DIR / "blindspot_03_options_tws_cache.json"

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 94
SLOTS = 2
OOS_CUT = "2026-08-11"
SLEEP = 0.55


def ship_pass(challenger: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        challenger["exp"] >= baseline["exp"] - 1e-9
        and challenger["capture"] >= baseline["capture"] - 0.01
    ) or (
        challenger["exp"] > baseline["exp"] + 0.01
        and challenger["capture"] >= baseline["capture"] - 0.02
    )


def simulate_slots(df: pd.DataFrame, *, score_col: str) -> pd.DataFrame:
    work = df[df["all_hours_admit"] == 1].copy()
    if work.empty:
        return work
    work = work.sort_values(
        ["signal_date", "hour", score_col], ascending=[True, True, False],
    )
    return work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS)


def metrics(taken: pd.DataFrame, expanders: pd.DataFrame | None = None) -> dict[str, Any]:
    if taken is None or taken.empty:
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


def _bar_date(b) -> str:
    d = b.date
    if hasattr(d, "date"):
        return d.date().isoformat()
    return str(d)[:10]


def fetch_iv_hv(ib, symbol: str, start: str, end: str) -> dict[str, Any]:
    """Pull daily IV + HV series for symbol covering [start, end]."""
    from ib_insync import Stock

    out: dict[str, Any] = {"symbol": symbol.upper(), "iv": {}, "hv": {}, "ok": 0, "reason": ""}
    stk = Stock(symbol.upper(), "SMART", "USD")
    q = ib.qualifyContracts(stk)
    if not q:
        out["reason"] = "qualify_fail"
        return out
    stk = q[0]
    # IB wants yyyymmdd hh:mm:ss US/Eastern (no dashes in date)
    end_dt = f"{end.replace('-', '')} 16:00:00 US/Eastern"
    # duration covers start..end with buffer for rank lookback
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        days = max(40, (d1 - d0).days + 35)
    except Exception:
        days = 90
    duration = f"{min(days, 365)} D"

    for what, key in (
        ("OPTION_IMPLIED_VOLATILITY", "iv"),
        ("HISTORICAL_VOLATILITY", "hv"),
    ):
        try:
            bars = ib.reqHistoricalData(
                stk,
                endDateTime=end_dt,
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow=what,
                useRTH=True,
                formatDate=1,
            )
            series = {}
            for b in bars or []:
                try:
                    series[_bar_date(b)] = float(b.close)
                except Exception:
                    continue
            out[key] = series
        except Exception as exc:
            out["reason"] = f"{what}:{exc}"[:120]
        time.sleep(SLEEP)

    out["ok"] = 1 if out["iv"] else 0
    if not out["iv"]:
        out["reason"] = out["reason"] or "no_iv"
    return out


def prior_date(series: dict[str, float], signal_date: str) -> str | None:
    """Latest date in series strictly before signal_date."""
    sd = signal_date[:10]
    keys = sorted(d for d in series if d < sd)
    return keys[-1] if keys else None


def feat_for_day(iv: dict[str, float], hv: dict[str, float], signal_date: str) -> dict[str, Any]:
    """Causal IV/HV features as-of signal_date (uses prior session only)."""
    empty = {
        "opt_ok": 0,
        "iv_prior": None,
        "hv_prior": None,
        "iv_hv_spread": None,
        "iv_rank_20": None,
        "iv_chg_5d": None,
        "prior_date": None,
    }
    pd0 = prior_date(iv, signal_date)
    if not pd0:
        return empty
    iv_p = float(iv[pd0])
    hv_p = float(hv[pd0]) if pd0 in hv else None
    # IV rank among last 20 prior IV points ending at pd0
    hist = sorted(d for d in iv if d <= pd0)[-20:]
    vals = [float(iv[d]) for d in hist]
    rank = None
    if len(vals) >= 5:
        rank = sum(1 for v in vals if v <= iv_p) / len(vals)
    # 5-session change
    chg = None
    if len(hist) >= 6:
        iv_old = float(iv[hist[-6]])
        if iv_old > 1e-9:
            chg = iv_p / iv_old - 1.0
    spread = (iv_p - hv_p) if hv_p is not None else None
    return {
        "opt_ok": 1,
        "iv_prior": iv_p,
        "hv_prior": hv_p,
        "iv_hv_spread": spread,
        "iv_rank_20": rank,
        "iv_chg_5d": chg,
        "prior_date": pd0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=0, help="0 = all admit symbols")
    ap.add_argument("--client-id", type=int, default=TWS_CLIENT_ID)
    args = ap.parse_args()

    t0 = time.time()
    df = pd.read_csv(CORPUS)
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["signal_date"] = df["signal_date"].astype(str)
    admits = df[df["all_hours_admit"] == 1].copy()
    counts = admits.groupby("symbol").size().sort_values(ascending=False)
    symbols = counts.index.tolist()
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    print(f"TWS options/IV study: {len(symbols)} symbols (TWS :{TWS_PORT})")

    from ib_insync import IB, util

    util.startLoop()
    ib = IB()
    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=args.client_id, timeout=12)
    except Exception as exc:
        print(f"CONNECT_FAIL: {exc}")
        print("Open TWS paper, enable API on 7497, retry.")
        return 1
    print("Connected", ib.isConnected())

    date_min = admits["signal_date"].min()[:10]
    date_max = admits["signal_date"].max()[:10]
    cache: dict[str, Any] = {}
    if CACHE.exists():
        try:
            prev = json.loads(CACHE.read_text(encoding="utf-8"))
            if isinstance(prev.get("cache"), dict):
                cache = prev["cache"]
                print(f"Loaded cache entries: {len(cache)}")
        except Exception:
            cache = {}

    for i, sym in enumerate(symbols, 1):
        if sym in cache and int((cache[sym] or {}).get("ok") or 0) == 1 and (cache[sym] or {}).get("iv"):
            if i % 25 == 0:
                print(f"  [{i}/{len(symbols)}] {sym} cache-hit")
            continue
        pack = fetch_iv_hv(ib, sym, date_min, date_max)
        cache[sym] = pack
        if i % 10 == 0 or i == len(symbols):
            ok = sum(1 for v in cache.values() if int(v.get("ok") or 0) == 1)
            print(f"  [{i}/{len(symbols)}] last={sym} ok={ok} reason={pack.get('reason') or 'ok'}")
            CACHE.write_text(
                json.dumps({"cache": cache, "updated": datetime.now(ET).isoformat()}, indent=2),
                encoding="utf-8",
            )

    ib.disconnect()
    print("TWS disconnected")

    # Attach features
    rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        d["score_v12"] = float(compute_continuation_score_v1_1(d))
        sym = str(d["symbol"]).upper()
        sd = str(d["signal_date"])[:10]
        pack = cache.get(sym) or {}
        feat = feat_for_day(pack.get("iv") or {}, pack.get("hv") or {}, sd)
        d.update(feat)
        rows.append(d)
    scored = pd.DataFrame(rows)

    admits_s = scored[scored["all_hours_admit"] == 1].copy()
    labeled = admits_s[admits_s["opt_ok"] == 1]
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

    strata = {
        "iv_rank_20": strata_num(
            "iv_rank_20",
            [
                ("iv_rank_<0.3", None, 0.3),
                ("iv_rank_0.3_0.7", 0.3, 0.7),
                ("iv_rank_>=0.7", 0.7, None),
            ],
        ),
        "iv_hv_spread": strata_num(
            "iv_hv_spread",
            [
                ("iv_below_hv", None, -0.05),
                ("iv_near_hv", -0.05, 0.10),
                ("iv_rich_>0.10", 0.10, None),
            ],
        ),
        "iv_chg_5d": strata_num(
            "iv_chg_5d",
            [
                ("iv_falling", None, -0.05),
                ("iv_flat", -0.05, 0.05),
                ("iv_rising", 0.05, None),
            ],
        ),
        "iv_prior": strata_num(
            "iv_prior",
            [
                ("iv_<0.4", None, 0.4),
                ("iv_0.4_0.8", 0.4, 0.8),
                ("iv_>=0.8", 0.8, None),
            ],
        ),
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

    def adj_demote_high_iv_rank(r) -> float:
        if int(r.get("opt_ok") or 0) != 1:
            return 0.0
        rk = _f(r.get("iv_rank_20"))
        if rk is None:
            return 0.0
        if rk >= 0.85:
            return -15.0
        if rk >= 0.70:
            return -8.0
        if rk <= 0.25:
            return 5.0
        return 0.0

    def adj_demote_iv_rich(r) -> float:
        if int(r.get("opt_ok") or 0) != 1:
            return 0.0
        sp = _f(r.get("iv_hv_spread"))
        if sp is None:
            return 0.0
        if sp >= 0.20:
            return -12.0
        if sp >= 0.10:
            return -6.0
        if sp <= -0.10:
            return 4.0
        return 0.0

    def adj_rising_iv(r) -> float:
        if int(r.get("opt_ok") or 0) != 1:
            return 0.0
        ch = _f(r.get("iv_chg_5d"))
        if ch is None:
            return 0.0
        if ch >= 0.15:
            return 8.0  # squeeze / event risk fuel
        if ch <= -0.15:
            return -5.0
        return 0.0

    def adj_combo(r) -> float:
        return adj_demote_high_iv_rank(r) + adj_demote_iv_rich(r) + 0.5 * adj_rising_iv(r)

    variants: dict[str, Any] = {"v12": {**m_v12, "oos": m_v12_oos, "pass_vs_v12": True}}
    for name, fn in [
        ("demote_high_iv_rank", adj_demote_high_iv_rank),
        ("demote_iv_rich", adj_demote_iv_rich),
        ("boost_rising_iv", adj_rising_iv),
        ("combo_iv", adj_combo),
    ]:
        m, o = with_adj(fn)
        variants[name] = {**m, "oos": o, "pass_vs_v12": ship_pass(m, m_v12)}

    for name, mask in [
        ("skip_iv_rank_ge_0.85", (base["all_hours_admit"] == 1) & (base["opt_ok"] == 1) & (base["iv_rank_20"] >= 0.85)),
        ("skip_iv_rich_ge_0.20", (base["all_hours_admit"] == 1) & (base["opt_ok"] == 1) & (base["iv_hv_spread"] >= 0.20)),
        ("skip_iv_ge_0.8", (base["all_hours_admit"] == 1) & (base["opt_ok"] == 1) & (base["iv_prior"] >= 0.8)),
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
        "symbols_ok": int(sum(1 for v in cache.values() if int(v.get("ok") or 0) == 1)),
        "admit_rows": int(len(admits_s)),
        "opt_labeled_admits": int(len(labeled)),
        "opt_coverage": float(admits_s["opt_ok"].mean()),
        "source": "TWS OPTION_IMPLIED_VOLATILITY + HISTORICAL_VOLATILITY",
    }

    if len(labeled) < 100:
        rec, rec_text = "HOLD", "IV coverage too thin. Keep research-only."
        best = None
        real_winners = []
    elif best is None:
        rec, rec_text = "HOLD", (
            "No TWS IV variant beat v1.2 on the ship gate. Keep options/IV research-only."
        )
    elif str(best).startswith("skip_"):
        rec, rec_text = "HARD", f"Best real passer: **{best}**. Hard filter only if you approve."
    else:
        rec, rec_text = "SOFT", f"Best real passer: **{best}**. Soft overlay only if you approve."

    result = {
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
            "Source: TWS local (not Modal — IB Gateway unreachable from cloud).",
            "Causal IV/HV = prior session close of IB historical series.",
            "OPTION_VOLUME / OI on underlying rejected by this IB account.",
            "Baseline = live v1.2 (extreme-gap soft demote included).",
        ],
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    CACHE.write_text(
        json.dumps({"cache": cache, "meta": coverage}, indent=2, default=str),
        encoding="utf-8",
    )

    lines = [
        "# EXP-0021 — Blind-spot #3: Options / IV (TWS)",
        "",
        f"**Generated:** {result['generated']}",
        f"**Runtime:** {result['runtime_sec']}s (local TWS)",
        f"**Recommendation:** **{rec}**",
        "",
        rec_text,
        "",
        "## Coverage",
        "",
        f"`{json.dumps(coverage)}`",
        "",
        "## How this maps to Peak Hour",
        "",
        "- Polygon options snapshots: not entitled.",
        "- **TWS** supplies daily `OPTION_IMPLIED_VOLATILITY` + `HISTORICAL_VOLATILITY` on the stock.",
        "- Features use **prior session** only (no look-ahead).",
        "- Live has no IV term today; wire only if you approve after bakeoff.",
        "",
        "## Strata (IV-labeled admits)",
        "",
    ]
    for key, title in [
        ("iv_rank_20", "IV rank (20d)"),
        ("iv_hv_spread", "IV − HV spread"),
        ("iv_chg_5d", "IV 5-session change"),
        ("iv_prior", "Raw prior IV"),
    ]:
        lines += [f"### {title}", "", "| Bucket | n | WR | Exp R | med MFE |", "|---|---:|---:|---:|---:|"]
        for r in strata.get(key) or []:
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
    for name, b in variants.items():
        oos_m = b.get("oos") if isinstance(b.get("oos"), dict) else {}
        flag = "—" if name == "v12" else ("YES" if b.get("pass_vs_v12") else "no")
        if name in result["vacuous_winners"]:
            flag = "vacuous"
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('exp') or 0):.3f} | {float(b.get('capture') or 0):.1%} | "
            f"{float(oos_m.get('exp') or 0):.3f} | {flag} |"
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
    for n in result["notes"]:
        lines.append(f"- {n}")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": rec,
        "best_variant": best,
        "winners": real_winners,
        "coverage": coverage,
        "baseline_exp": m_v12["exp"],
        "runtime_sec": result["runtime_sec"],
        "wrote": str(OUT_MD),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
