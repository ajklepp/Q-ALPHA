"""
EXP-0021 — Belief-regime edge study (anticipation / digest / reacceleration / quiet).

Causal Polygon 90d lookback + rule labels for all admits; AI deep briefs on a
stratified ~300-row sample for agreement. Bakeoff soft demotes/boosts/filters
vs continuation_score_v1.1. NO live rewrite — report only.

Usage:
  .\\venv\\Scripts\\python.exe experiments/EXP-0021/study_belief_regime.py
  .\\venv\\Scripts\\python.exe experiments/EXP-0021/study_belief_regime.py --skip-ai
  .\\venv\\Scripts\\python.exe experiments/EXP-0021/study_belief_regime.py --max-symbol-days 200
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[1]
CAND = ROOT / "candidates"
sys.path.insert(0, str(CAND))
sys.path.insert(0, str(EXP_DIR))

from tsd_scan_pipeline.tsd_catalyst_deep import (  # noqa: E402
    fetch_polygon_news_lookback,
    research_deep_catalyst,
)
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    EXTENSION_SCAN_AUTO,
    compute_continuation_score_v1_1,
)
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

ET = pytz.timezone("America/New_York")
SLOTS = 2
OOS_CUT = "2026-08-11"
CORPUS = EXP_DIR / "corpus_htf_universe_social.csv"
if not CORPUS.exists():
    CORPUS = EXP_DIR / "corpus_htf_universe.csv"

CACHE_PATH = EXP_DIR / "belief_regime_cache.json"
OUT_MD = EXP_DIR / "STUDY_BELIEF_REGIME.md"
OUT_JSON = EXP_DIR / "study_belief_regime_metrics.json"

DIGEST_KW = (
    "miss", "plunged", "plunge", "slump", "cut guidance", "guidance cut",
    "downgrade", "dilution", "offering", "atm ", "lawsuit", "investigation",
    "disappoint", "selloff", "sell-off", "falls", "dropped", "tumbles",
)
POSITIVE_KW = (
    "beat", "raised", "upgrade", "approval", "contract", "partnership",
    "acquisition", "sold out", "capacity", "expansion", "record",
    "breakthrough", "wins", "award", "deal",
)
AI_SAMPLE_TARGET = 300
AGREEMENT_FLOOR = 0.60


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
        return {"n": 0, "wr": 0.0, "exp": 0.0, "capture": 0.0, "med_mfe": 0.0, "med_day_mfe": 0.0}
    wr = float(taken["hit_1r"].mean())
    exp = float(taken["r_multiple"].mean())
    med_mfe = float(taken["mfe"].median()) if "mfe" in taken.columns else 0.0
    med_day = float(taken["day_mfe"].median()) if "day_mfe" in taken.columns else 0.0
    cap = 0.0
    if expanders is not None and len(expanders):
        keys = set(zip(taken["signal_date"].astype(str), taken["symbol"].astype(str)))
        ekeys = set(zip(expanders["signal_date"].astype(str), expanders["symbol"].astype(str)))
        hit = len(keys & ekeys)
        cap = hit / max(len(ekeys), 1)
    return {
        "n": int(len(taken)),
        "wr": wr,
        "exp": exp,
        "capture": cap,
        "med_mfe": med_mfe,
        "med_day_mfe": med_day,
    }


def ship_pass(challenger: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Expectancy >= baseline and capture not worse than -1pp (or clear exp win)."""
    return (
        challenger["exp"] >= baseline["exp"] - 1e-9
        and challenger["capture"] >= baseline["capture"] - 0.01
    ) or (
        challenger["exp"] > baseline["exp"] + 0.01
        and challenger["capture"] >= baseline["capture"] - 0.02
    )


def _parse_signal_ts(row: dict[str, Any] | pd.Series) -> datetime:
    raw = row.get("signal_ts") or row.get("signal_date")
    ts = pd.Timestamp(raw)
    if ts.tzinfo is None:
        # Corpus bars are ET-aware strings usually; localize if naive
        try:
            return ET.localize(ts.to_pydatetime())
        except Exception:
            return ts.to_pydatetime().replace(tzinfo=ET)
    return ts.to_pydatetime().astimezone(ET)


def _blob_from_articles(arts: list[dict[str, Any]], *, max_age: float | None = None) -> str:
    parts = []
    for a in arts:
        age = float(a.get("age_days") or 0)
        if max_age is not None and age > max_age:
            continue
        parts.append(f"{a.get('title') or ''} {a.get('description') or ''}")
    return " ".join(parts).lower()


def _has_kw(blob: str, kws: tuple[str, ...]) -> bool:
    return any(k in blob for k in kws)


def rule_label_regime(
    articles: list[dict[str, Any]],
    *,
    scan_score: float,
    gap_pct: float,
    bar_state: str,
    dilution_flag: int = 0,
    guidance_cut: int = 0,
    distress_flag: int = 0,
) -> dict[str, Any]:
    """
    Pre-registered rule labels from plan.

    Priority: digest > reacceleration > anticipation > quiet
    """
    if not articles:
        return {
            "belief_regime": "quiet",
            "n_headlines": 0,
            "fresh_digest_kw": 0,
            "old_digest_kw": 0,
            "pos_kw": 0,
        }

    fresh_blob = _blob_from_articles(articles, max_age=2.0)
    mid_blob = _blob_from_articles(articles, max_age=15.0)
    # mid excluding fresh: use ages 3–15
    mid_only_parts = []
    for a in articles:
        age = float(a.get("age_days") or 0)
        if 3.0 <= age <= 15.0:
            mid_only_parts.append(f"{a.get('title') or ''} {a.get('description') or ''}")
    mid_only = " ".join(mid_only_parts).lower()
    all_blob = _blob_from_articles(articles)

    fresh_digest = (
        _has_kw(fresh_blob, DIGEST_KW)
        or int(dilution_flag) == 1
        or int(guidance_cut) == 1
        or int(distress_flag) == 1
    )
    # Large gap-down into signal as digest proxy even without keyword
    gap_digest = float(gap_pct or 0) <= -0.03
    hard_ext = float(scan_score or 0) >= EXTENSION_SCAN_AUTO or str(bar_state) == "extended"

    old_digest = _has_kw(mid_only, DIGEST_KW)
    pos_any = _has_kw(all_blob, POSITIVE_KW)
    pos_older = _has_kw(mid_only, POSITIVE_KW) or (
        _has_kw(all_blob, POSITIVE_KW) and not _has_kw(fresh_blob, DIGEST_KW)
    )

    if fresh_digest or (gap_digest and (pos_any or old_digest or len(articles) > 0)):
        regime = "digest"
    elif old_digest and not fresh_digest and not hard_ext and float(scan_score or 99) <= 55:
        # Belief returning after prior digestion; tape not extended
        regime = "reacceleration"
    elif pos_older and not fresh_digest and not hard_ext:
        regime = "anticipation"
    elif len(articles) == 0:
        regime = "quiet"
    else:
        # Headlines exist but no clear positive/digest pattern
        regime = "quiet" if not pos_any else "anticipation"

    return {
        "belief_regime": regime,
        "n_headlines": len(articles),
        "fresh_digest_kw": int(fresh_digest),
        "old_digest_kw": int(old_digest),
        "pos_kw": int(pos_any),
        "gap_digest": int(gap_digest),
    }


def ai_mode_to_regime(brief: dict[str, Any]) -> str:
    """Map deep_catalyst brief fields onto the four study regimes."""
    mode = str(brief.get("catalyst_mode") or "").lower()
    expect = int(brief.get("expectation_pending") or 0) == 1
    stale = int(brief.get("stale_relevant") or 0) == 1
    fresh = int(brief.get("fresh_catalyst") or 0) == 1
    flags = [str(x).lower() for x in (brief.get("risk_flags") or [])]
    risk = any(x in flags for x in ("dilution", "distress", "guidance_cut", "lawsuit"))
    narrative = f"{brief.get('narrative') or ''} {brief.get('deep_summary_line') or ''}".lower()
    digestish = risk or _has_kw(narrative, DIGEST_KW)

    if digestish and fresh:
        return "digest"
    if digestish and not expect:
        return "digest"
    if mode == "quiet":
        return "quiet"
    if mode == "expectation" or (expect and not digestish):
        return "anticipation"
    if mode in ("stale_relevant", "mixed") and expect:
        return "reacceleration" if stale or digestish else "anticipation"
    if mode == "stale_relevant" and stale:
        return "anticipation"
    if mode == "fresh" and not digestish:
        return "anticipation"
    if mode == "mixed" and digestish:
        return "reacceleration"
    return "quiet" if mode in ("", "unknown", "quiet") else "anticipation"


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {"symbol_days": {}, "ai_briefs": {}, "meta": {}}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def build_symbol_day_labels(
    admits: pd.DataFrame,
    *,
    api_key: str,
    cache: dict[str, Any],
    max_symbol_days: int | None = None,
) -> dict[str, Any]:
    """
    Fetch Polygon news once per symbol over the corpus window, then
    causally filter articles published before each signal (no look-ahead).
    """
    pairs = (
        admits[
            [
                "symbol", "signal_date", "signal_ts", "scan_score", "gap_pct",
                "bar_state", "dilution_flag", "guidance_cut", "distress_flag",
            ]
        ]
        .copy()
    )
    pairs["symbol"] = pairs["symbol"].astype(str).str.upper()
    pairs["signal_date"] = pairs["signal_date"].astype(str)
    pairs = pairs.sort_values(["symbol", "signal_date", "signal_ts"]).drop_duplicates(
        ["symbol", "signal_date"], keep="first"
    )
    if max_symbol_days is not None:
        pairs = pairs.head(int(max_symbol_days))

    sd = cache.setdefault("symbol_days", {})
    sym_hist = cache.setdefault("symbol_histories", {})

    # One wide fetch per symbol (min date - 90d through max signal)
    symbols = sorted(pairs["symbol"].unique())
    print(f"Symbol-days: {len(pairs)} · unique symbols: {len(symbols)}")

    for i, sym in enumerate(symbols):
        sub = pairs[pairs["symbol"] == sym]
        max_ts = max(_parse_signal_ts(r) for _, r in sub.iterrows())
        # Need articles back 90d from earliest signal too
        min_ts = min(_parse_signal_ts(r) for _, r in sub.iterrows())
        hist_key = f"{sym}|{min_ts.date().isoformat()}|{max_ts.date().isoformat()}"
        if hist_key not in sym_hist:
            try:
                # Fetch as-of max signal so all earlier articles included; filter per row
                arts = fetch_polygon_news_lookback(
                    sym,
                    api_key=api_key,
                    as_of=max_ts,
                    lookback_days=90 + max(0, (max_ts.date() - min_ts.date()).days),
                    limit=50,
                )
            except Exception as exc:
                print(f"  news fail {sym}: {exc}")
                arts = []
            sym_hist[hist_key] = {
                "articles": [
                    {
                        "published_et": a.get("published_et"),
                        "age_days_at_max": a.get("age_days"),
                        "title": a.get("title"),
                        "description": (a.get("description") or "")[:160],
                    }
                    for a in arts
                ],
                "as_of_max": max_ts.isoformat(),
            }
            if (i + 1) % 20 == 0:
                print(f"  symbol fetch {i+1}/{len(symbols)}")
                save_cache(cache)

        raw_arts = sym_hist[hist_key]["articles"]
        for _, r in sub.iterrows():
            key = f"{sym}|{r['signal_date']}"
            as_of = _parse_signal_ts(r)
            # Causal filter: published_et < as_of; recompute age_days
            causal = []
            for a in raw_arts:
                pub = a.get("published_et")
                if not pub:
                    continue
                try:
                    pub_dt = datetime.fromisoformat(str(pub))
                    if pub_dt.tzinfo is None:
                        pub_dt = ET.localize(pub_dt)
                    else:
                        pub_dt = pub_dt.astimezone(ET)
                except Exception:
                    continue
                if pub_dt >= as_of:
                    continue
                age = max(0.0, (as_of - pub_dt).total_seconds() / 86400.0)
                causal.append(
                    {
                        "age_days": round(age, 1),
                        "title": a.get("title"),
                        "description": a.get("description") or "",
                    }
                )
            causal.sort(key=lambda x: float(x.get("age_days") or 0))
            # newest first for labeling
            causal = list(reversed(causal))
            label = rule_label_regime(
                causal,
                scan_score=float(r.get("scan_score") or 0),
                gap_pct=float(r.get("gap_pct") or 0),
                bar_state=str(r.get("bar_state") or ""),
                dilution_flag=int(r.get("dilution_flag") or 0),
                guidance_cut=int(bool(r.get("guidance_cut"))),
                distress_flag=int(r.get("distress_flag") or 0),
            )
            sd[key] = {
                "articles": causal[:40],
                "rule": label,
                "as_of": as_of.isoformat(),
            }

    save_cache(cache)
    return cache


def attach_regimes(df: pd.DataFrame, cache: dict[str, Any]) -> pd.DataFrame:
    sd = cache.get("symbol_days") or {}
    regimes = []
    n_head = []
    for _, r in df.iterrows():
        key = f"{str(r['symbol']).upper()}|{str(r['signal_date'])}"
        entry = sd.get(key) or {}
        rule = entry.get("rule") or {}
        regimes.append(rule.get("belief_regime") or "quiet")
        n_head.append(int(rule.get("n_headlines") or 0))
    out = df.copy()
    out["belief_regime"] = regimes
    out["belief_n_headlines"] = n_head
    return out


def strata_table(df: pd.DataFrame, *, label: str) -> list[dict[str, Any]]:
    rows = []
    for reg, g in df.groupby("belief_regime"):
        rows.append({
            "slice": label,
            "regime": str(reg),
            "n": int(len(g)),
            "wr": float(g["hit_1r"].mean()) if len(g) else None,
            "exp": float(g["r_multiple"].mean()) if len(g) else None,
            "med_mfe": float(g["mfe"].median()) if len(g) else None,
            "med_day_mfe": float(g["day_mfe"].median()) if len(g) else None,
        })
    rows.sort(key=lambda x: x["regime"])
    return rows


def pick_ai_sample(
    scored: pd.DataFrame,
    taken_v11: pd.DataFrame,
    expanders: pd.DataFrame,
    *,
    target: int = AI_SAMPLE_TARGET,
    seed: int = 21,
) -> pd.DataFrame:
    """Stratified sample: taken slots + missed expanders + per-regime admits."""
    rng = random.Random(seed)
    parts: list[pd.DataFrame] = []

    if len(taken_v11):
        parts.append(taken_v11.copy())

    # Missed expanders = expander keys not in taken
    if len(expanders) and len(taken_v11):
        taken_keys = set(
            zip(taken_v11["signal_date"].astype(str), taken_v11["symbol"].astype(str))
        )
        miss = expanders[
            ~expanders.apply(
                lambda r: (str(r["signal_date"]), str(r["symbol"])) in taken_keys,
                axis=1,
            )
        ]
        parts.append(miss.head(80))

    admits = scored[scored["all_hours_admit"] == 1]
    per_reg = max(20, target // 8)
    for reg, g in admits.groupby("belief_regime"):
        idx = list(g.index)
        rng.shuffle(idx)
        parts.append(g.loc[idx[:per_reg]])

    if not parts:
        return admits.head(0)
    sample = pd.concat(parts, ignore_index=False)
    sample = sample[~sample.index.duplicated(keep="first")]
    if len(sample) > target:
        sample = sample.sample(n=target, random_state=seed)
    return sample


def run_ai_validation(
    sample: pd.DataFrame,
    cache: dict[str, Any],
    *,
    api_key: str,
) -> dict[str, Any]:
    briefs = cache.setdefault("ai_briefs", {})
    agree = 0
    total = 0
    confusion: dict[str, dict[str, int]] = {}
    details = []

    for i, (_, r) in enumerate(sample.iterrows()):
        key = f"{str(r['symbol']).upper()}|{str(r['signal_date'])}"
        rule_reg = str(r.get("belief_regime") or "quiet")
        if key in briefs and briefs[key].get("ai_regime"):
            ai_reg = briefs[key]["ai_regime"]
            brief = briefs[key].get("brief") or {}
        else:
            as_of = _parse_signal_ts(r)
            try:
                brief = research_deep_catalyst(
                    str(r["symbol"]),
                    api_key=api_key,
                    as_of=as_of,
                    use_ai=True,
                )
            except Exception as exc:
                brief = {"deep_ok": 0, "deep_reason": str(exc), "catalyst_mode": "quiet"}
            ai_reg = ai_mode_to_regime(brief)
            briefs[key] = {
                "ai_regime": ai_reg,
                "rule_regime": rule_reg,
                "brief": {
                    "catalyst_mode": brief.get("catalyst_mode"),
                    "expectation_pending": brief.get("expectation_pending"),
                    "stale_relevant": brief.get("stale_relevant"),
                    "fresh_catalyst": brief.get("fresh_catalyst"),
                    "deep_summary_line": brief.get("deep_summary_line"),
                    "risk_flags": brief.get("risk_flags"),
                },
            }
            if (i + 1) % 20 == 0:
                print(f"  AI briefs {i+1}/{len(sample)}")
                save_cache(cache)

        total += 1
        if ai_reg == rule_reg:
            agree += 1
        confusion.setdefault(rule_reg, {})
        confusion[rule_reg][ai_reg] = confusion[rule_reg].get(ai_reg, 0) + 1
        details.append({"key": key, "rule": rule_reg, "ai": ai_reg})

    save_cache(cache)
    rate = agree / total if total else 0.0
    return {
        "n": total,
        "agree": agree,
        "agreement_rate": rate,
        "trust_rule_bakeoffs": rate >= AGREEMENT_FLOOR,
        "confusion_rule_to_ai": confusion,
        "agreement_floor": AGREEMENT_FLOOR,
    }


def score_with_belief(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        d = r.to_dict()
        base = float(compute_continuation_score_v1_1(d))
        reg = str(d.get("belief_regime") or "quiet")
        d["score_v11"] = base
        d["score_demote_digest"] = base - (25.0 if reg == "digest" else 0.0)
        d["score_boost_reaccel"] = base + (10.0 if reg == "reacceleration" else 0.0)
        d["score_boost_anticip"] = base + (8.0 if reg == "anticipation" else 0.0)
        adj = 0.0
        if reg == "digest":
            adj -= 25.0
        elif reg == "reacceleration":
            adj += 10.0
        elif reg == "anticipation":
            adj += 8.0
        d["score_prefer_belief"] = base + adj
        d["admit_skip_digest"] = int(
            int(d.get("all_hours_admit") or 0) == 1 and reg != "digest"
        )
        rows.append(d)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ai", action="store_true", help="Skip OpenRouter validation sample")
    ap.add_argument("--max-symbol-days", type=int, default=0, help="Cap fetches (0=all)")
    ap.add_argument("--ai-sample", type=int, default=AI_SAMPLE_TARGET)
    ap.add_argument("--seed", type=int, default=21)
    args = ap.parse_args()

    t0 = time.time()
    random.seed(args.seed)
    df = pd.read_csv(CORPUS)
    print(f"Loaded {len(df)} from {CORPUS.name}")
    admits = df[df["all_hours_admit"] == 1].copy()
    print(f"Admits: {len(admits)}")

    api_key = load_polygon_key()
    cache = load_cache()
    max_sd = args.max_symbol_days if args.max_symbol_days > 0 else None
    cache = build_symbol_day_labels(
        admits, api_key=api_key, cache=cache, max_symbol_days=max_sd
    )

    scored = attach_regimes(df, cache)
    # If max_symbol_days limited, unlabeled admits stay quiet only if missing — mark unknown
    sd = cache.get("symbol_days") or {}
    if max_sd:
        known = set(sd.keys())
        mask = scored.apply(
            lambda r: f"{str(r['symbol']).upper()}|{str(r['signal_date'])}" in known,
            axis=1,
        )
        scored = scored[mask | (scored["all_hours_admit"] != 1)].copy()
        # drop admits without labels when capped
        scored = scored[
            (scored["all_hours_admit"] != 1)
            | scored.apply(
                lambda r: f"{str(r['symbol']).upper()}|{str(r['signal_date'])}" in known,
                axis=1,
            )
        ]

    scored = score_with_belief(scored)
    admits_s = scored[scored["all_hours_admit"] == 1]

    expanders = (
        admits_s.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    taken_v11 = simulate_slots(scored, score_col="score_v11")
    m_v11 = metrics(taken_v11, expanders)

    # AI validation
    ai_result: dict[str, Any]
    if args.skip_ai:
        ai_result = {
            "n": 0,
            "agree": 0,
            "agreement_rate": None,
            "trust_rule_bakeoffs": True,
            "skipped": True,
            "note": "AI skipped via --skip-ai",
        }
    else:
        sample = pick_ai_sample(
            scored, taken_v11, expanders, target=args.ai_sample, seed=args.seed
        )
        print(f"AI validation sample: {len(sample)}")
        ai_result = run_ai_validation(sample, cache, api_key=api_key)
        print(
            f"Agreement rule↔AI: {ai_result['agreement_rate']:.1%} "
            f"(floor {AGREEMENT_FLOOR:.0%}) trust={ai_result['trust_rule_bakeoffs']}"
        )

    # Strata
    strata_admits = strata_table(admits_s, label="admits")
    strata_taken = strata_table(
        attach_regimes(taken_v11, cache) if "belief_regime" not in taken_v11.columns
        else taken_v11,
        label="taken_v11",
    )
    # ensure taken has regime
    if "belief_regime" not in taken_v11.columns:
        taken_v11 = attach_regimes(taken_v11, cache)
        strata_taken = strata_table(taken_v11, label="taken_v11")

    # Bakeoffs
    variants = {
        "v11": "score_v11",
        "demote_digest": "score_demote_digest",
        "boost_reaccel": "score_boost_reaccel",
        "boost_anticip": "score_boost_anticip",
        "prefer_belief": "score_prefer_belief",
    }
    bakeoff = {}
    for name, col in variants.items():
        taken = simulate_slots(scored, score_col=col)
        m = metrics(taken, expanders)
        oos = scored[scored["signal_date"].astype(str) >= OOS_CUT]
        m_oos = metrics(simulate_slots(oos, score_col=col), expanders)
        bakeoff[name] = {
            **m,
            "oos": m_oos,
            "pass_vs_v11": ship_pass(m, m_v11) if name != "v11" else True,
        }

    # skip_digest: filter admits then rank v11
    skip_df = scored.copy()
    skip_df["all_hours_admit"] = skip_df["admit_skip_digest"]
    taken_skip = simulate_slots(skip_df, score_col="score_v11")
    m_skip = metrics(taken_skip, expanders)
    oos_skip = skip_df[skip_df["signal_date"].astype(str) >= OOS_CUT]
    bakeoff["skip_digest"] = {
        **m_skip,
        "oos": metrics(simulate_slots(oos_skip, score_col="score_v11"), expanders),
        "pass_vs_v11": ship_pass(m_skip, m_v11),
    }

    # Theory check on admits
    by_reg = {r["regime"]: r for r in strata_admits}
    digest_exp = (by_reg.get("digest") or {}).get("exp")
    reacc_exp = (by_reg.get("reacceleration") or {}).get("exp")
    anticip_exp = (by_reg.get("anticipation") or {}).get("exp")
    quiet_exp = (by_reg.get("quiet") or {}).get("exp")
    theory_ok = True
    theory_notes = []
    if digest_exp is not None and quiet_exp is not None and digest_exp > quiet_exp + 0.02:
        theory_ok = False
        theory_notes.append("digest exp not worse than quiet — hypothesis weak")
    if reacc_exp is not None and digest_exp is not None and reacc_exp <= digest_exp:
        theory_notes.append("reacceleration not clearly > digest")
    if anticip_exp is not None and digest_exp is not None and anticip_exp <= digest_exp:
        theory_notes.append("anticipation not clearly > digest")

    winners = [
        k for k, v in bakeoff.items()
        if k != "v11" and v.get("pass_vs_v11")
    ]
    # Prefer highest OOS exp among passers
    best = None
    if winners and ai_result.get("trust_rule_bakeoffs", True):
        best = max(winners, key=lambda k: bakeoff[k]["oos"]["exp"])

    if not ai_result.get("trust_rule_bakeoffs", True):
        recommendation = "MORE_LABELS"
        rec_text = (
            "Rule↔AI agreement below 60% — do **not** trust rule-only bakeoffs. "
            "Expand AI labeling before any soft/hard ship."
        )
    elif best is None:
        recommendation = "HOLD"
        rec_text = (
            "No belief-aware variant beat v1.1 on the pre-registered ship gate. "
            "Keep live on continuation_score_v1.1; use regimes for thesis/postmortem only."
        )
    elif best == "skip_digest":
        recommendation = "HARD"
        rec_text = (
            f"Best passer: **skip_digest** (exp={bakeoff[best]['exp']:.3f} vs "
            f"v1.1 {m_v11['exp']:.3f}). Hard-exclude digest only if you approve rewrite."
        )
    else:
        recommendation = "SOFT"
        rec_text = (
            f"Best passer: **{best}** (exp={bakeoff[best]['exp']:.3f} vs "
            f"v1.1 {m_v11['exp']:.3f}). Soft demote/boost only — no hard filter."
        )

    result = {
        "corpus": CORPUS.name,
        "runtime_sec": round(time.time() - t0, 1),
        "n_admits": int(len(admits_s)),
        "n_symbol_days_cached": len(sd),
        "regime_counts_admits": admits_s["belief_regime"].value_counts().to_dict(),
        "strata_admits": strata_admits,
        "strata_taken_v11": strata_taken,
        "theory_ok": theory_ok,
        "theory_notes": theory_notes,
        "ai_validation": ai_result,
        "baseline_v11": m_v11,
        "bakeoff": bakeoff,
        "winners_vs_v11": winners,
        "best_variant": best,
        "recommendation": recommendation,
        "recommendation_text": rec_text,
    }

    # Markdown report
    lines = [
        "# EXP-0021 — Belief-regime edge study",
        "",
        f"**Generated:** {datetime.now(ET).isoformat()}",
        f"**Corpus:** `{CORPUS.name}` · slots={SLOTS} · OOS≥{OOS_CUT}",
        f"**Runtime:** {result['runtime_sec']}s",
        f"**Recommendation:** **{recommendation}**",
        "",
        rec_text,
        "",
        "## Labeling",
        "",
        f"- Symbol-days cached: {result['n_symbol_days_cached']}",
        f"- Admit regime counts: `{json.dumps(result['regime_counts_admits'])}`",
        "",
        "### AI validation (rule ↔ deep brief)",
        "",
    ]
    if ai_result.get("skipped"):
        lines.append("- AI sample skipped (`--skip-ai`).")
    else:
        lines += [
            f"- n={ai_result['n']} · agreement={ai_result['agreement_rate']:.1%} "
            f"(floor {AGREEMENT_FLOOR:.0%})",
            f"- trust_rule_bakeoffs={ai_result['trust_rule_bakeoffs']}",
            f"- confusion (rule→ai): `{json.dumps(ai_result.get('confusion_rule_to_ai'))}`",
        ]

    lines += [
        "",
        "## A) Descriptive strata (hypothesis check)",
        "",
        "Hypothesis: digest worst; reacceleration / anticipation better than quiet/digest.",
        "",
        "### Admits",
        "",
        "| Regime | n | WR | Exp R | med MFE | med day MFE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in strata_admits:
        lines.append(
            f"| {r['regime']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['med_mfe']:.3f} | {r['med_day_mfe']:.3f} |"
        )
    lines += [
        "",
        "### Taken under v1.1",
        "",
        "| Regime | n | WR | Exp R | med MFE | med day MFE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in strata_taken:
        lines.append(
            f"| {r['regime']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | "
            f"{r['med_mfe']:.3f} | {r['med_day_mfe']:.3f} |"
        )
    if theory_notes:
        lines += ["", "Notes:"] + [f"- {n}" for n in theory_notes]

    lines += [
        "",
        "## B) Bakeoff vs v1.1 (pre-registered PASS gate)",
        "",
        "PASS: exp ≥ v1.1 AND capture ≥ v1.1 − 1pp (or clear exp win with capture −2pp).",
        "",
        "| Variant | n | WR | Exp R | Capture | OOS exp | PASS? |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for name in ["v11", "demote_digest", "boost_reaccel", "boost_anticip", "prefer_belief", "skip_digest"]:
        b = bakeoff[name]
        oos_exp = b["oos"]["exp"]
        flag = "—" if name == "v11" else ("YES" if b["pass_vs_v11"] else "no")
        lines.append(
            f"| {name} | {b['n']} | {b['wr']:.1%} | {b['exp']:.3f} | "
            f"{b['capture']:.1%} | {oos_exp:.3f} | {flag} |"
        )

    lines += [
        "",
        "## Recommended live action",
        "",
        f"- **{recommendation}** — {rec_text}",
        "- No live rewrite in this study. Decide after reading numbers.",
        "",
        "## Decision menu",
        "",
        "1. HOLD — narrative for thesis only",
        "2. SOFT — demote/boost only",
        "3. HARD — skip digest",
        "4. MORE_LABELS — expand AI labeling before ship",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    cache["meta"] = {
        "last_run": datetime.now(ET).isoformat(),
        "recommendation": recommendation,
        "agreement_rate": ai_result.get("agreement_rate"),
    }
    save_cache(cache)
    print(json.dumps({
        "recommendation": recommendation,
        "best_variant": best,
        "winners": winners,
        "agreement": ai_result.get("agreement_rate"),
        "baseline_exp": m_v11["exp"],
        "runtime_sec": result["runtime_sec"],
    }, indent=2))
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
