"""
EXP-0021 — Belief labels v2 on Modal (structured AI tags; no live rewrite).

Status: RESEARCH ONLY (user 2026-09-05). Do not wire into live scoring.
Keep collecting labels for thesis/further study; live stays on continuation_score_v1.1.
  event_family × info_hardness × story_phase × expectation_gap (+ attrs)

Runs on Modal (parallel OpenRouter labeling). Local PC only launches the job.

Usage:
  .\\venv\\Scripts\\modal.exe run experiments/EXP-0021/study_belief_labels_v2_modal.py
  .\\venv\\Scripts\\modal.exe run experiments/EXP-0021/study_belief_labels_v2_modal.py --sample-size 400
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import modal

APP_NAME = "q-alpha-exp021-belief-v2"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
# Client: experiments/EXP-0021/... ; Modal mount: /root/<file>.py (shallow)
if EXP_DIR.name == "EXP-0021":
    ROOT = EXP_DIR.parents[1]
    CAND = ROOT / "candidates"
else:
    ROOT = Path("/data")
    CAND = Path("/pkg")
SCORE_LOCAL = CAND / "tsd_scan_pipeline" / "tsd_launch_score.py"
if not SCORE_LOCAL.exists():
    SCORE_LOCAL = Path("/pkg/tsd_launch_score.py")
CORPUS_LOCAL = EXP_DIR / "corpus_htf_universe_social.csv"
if not CORPUS_LOCAL.exists():
    CORPUS_LOCAL = Path("/data/corpus.csv")
CACHE_LOCAL = EXP_DIR / "belief_regime_cache.json"
OUT_MD_LOCAL = EXP_DIR / "STUDY_BELIEF_LABELS_V2.md"
OUT_JSON_LOCAL = EXP_DIR / "study_belief_labels_v2_metrics.json"
LABELS_LOCAL = EXP_DIR / "belief_labels_v2_cache.json"

app = modal.App(APP_NAME)

_image = modal.Image.debian_slim(python_version="3.12").pip_install([
    "pandas",
    "numpy",
    "requests",
    "pytz",
    "tzdata",
])
# Mounts only resolve on the local client during `modal run`
if EXP_DIR.name == "EXP-0021":
    _image = (
        _image
        .add_local_file(str(CORPUS_LOCAL), remote_path="/data/corpus.csv")
        .add_local_file(
            str(CACHE_LOCAL) if CACHE_LOCAL.exists() else str(CORPUS_LOCAL),
            remote_path="/data/belief_cache.json",
        )
        .add_local_file(
            str(ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_launch_score.py"),
            remote_path="/pkg/tsd_launch_score.py",
        )
    )
image = _image

polygon_secret = modal.Secret.from_name("polygon-api-key")
qalpha_secrets = modal.Secret.from_name("q-alpha-secrets")

SLOTS = 2
OOS_CUT = "2026-08-11"
SAMPLE_DEFAULT = 0  # 0 = all admits (Modal parallel)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"
POLYGON_BASE = "https://api.polygon.io"
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

EVENT_FAMILIES = (
    "earnings_results",
    "guidance_outlook",
    "analyst_action",
    "contract_customer",
    "financing_dilution",
    "legal_regulatory",
    "product_launch_story",
    "m_and_a",
    "price_commentary",
    "promo",
    "none",
)
STORY_PHASES = (
    "pre_event_anticipation",
    "fresh_print",
    "post_print_digest",
    "follow_up_coverage",
    "stale_narrative",
    "quiet",
)
EXPECT_GAPS = ("above_hopes", "in_line", "below_hopes", "unknown")
HARDNESS = ("hard_quantified", "soft_narrative", "unknown")


def _empty_label(symbol: str, reason: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "label_ok": 0,
        "reason": reason,
        "event_family": "none",
        "info_hardness": "unknown",
        "story_phase": "quiet",
        "expectation_gap": "unknown",
        "rumor_or_speculation": 0,
        "forward_looking": 0,
        "primary_source": 0,
        "uncertainty_resolving": 0,
        "horizon": "undated",
        "derived_mode": "quiet",
        "summary_line": "",
    }


def derive_mode(lab: dict[str, Any]) -> str:
    """Human-facing mode derived from orthogonal tags (not used as sole score key)."""
    fam = str(lab.get("event_family") or "none")
    hard = str(lab.get("info_hardness") or "unknown")
    phase = str(lab.get("story_phase") or "quiet")
    gap = str(lab.get("expectation_gap") or "unknown")
    rumor = int(lab.get("rumor_or_speculation") or 0) == 1

    if fam in ("price_commentary", "promo"):
        return "junk"
    if phase == "quiet" and fam == "none":
        return "quiet"
    if gap == "below_hopes" or (
        phase == "post_print_digest" and hard == "hard_quantified"
    ):
        return "digest"
    if phase == "pre_event_anticipation" or (
        int(lab.get("forward_looking") or 0) == 1 and phase in (
            "pre_event_anticipation", "stale_narrative"
        )
    ):
        return "anticipation"
    if hard == "hard_quantified" and phase in ("fresh_print", "follow_up_coverage"):
        return "continuation_hard"
    if hard == "soft_narrative" or fam == "product_launch_story" or rumor:
        return "hype_soft"
    if phase == "stale_narrative":
        return "stale_narrative"
    return "other"


def belief_score_adj(lab: dict[str, Any]) -> float:
    """
    Literature-inspired soft adjustments (offline bakeoff only).
    Hard quantified continuation: mild boost; soft hype / junk: demote;
    below_hopes digest: demote; rumor: demote.
    """
    mode = str(lab.get("derived_mode") or derive_mode(lab))
    adj = 0.0
    if mode == "continuation_hard":
        adj += 10.0
    elif mode == "anticipation":
        adj += 0.0  # literature: do NOT auto-boost anticipation
    elif mode == "digest":
        adj -= 20.0
    elif mode == "hype_soft":
        adj -= 12.0
    elif mode == "junk":
        adj -= 25.0
    if int(lab.get("rumor_or_speculation") or 0) == 1:
        adj -= 8.0
    if str(lab.get("expectation_gap") or "") == "below_hopes":
        adj -= 10.0
    if str(lab.get("expectation_gap") or "") == "above_hopes" and str(
        lab.get("info_hardness")
    ) == "hard_quantified":
        adj += 6.0
    return adj


def simulate_slots(df, *, score_col: str):
    import pandas as pd

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


def ship_pass(challenger: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        challenger["exp"] >= baseline["exp"] - 1e-9
        and challenger["capture"] >= baseline["capture"] - 0.01
    ) or (
        challenger["exp"] > baseline["exp"] + 0.01
        and challenger["capture"] >= baseline["capture"] - 0.02
    )


def fetch_news(symbol: str, as_of_iso: str, api_key: str, lookback_days: int = 90) -> list[dict]:
    import pytz
    import requests

    ET = pytz.timezone("America/New_York")
    as_of = datetime.fromisoformat(as_of_iso)
    if as_of.tzinfo is None:
        as_of = ET.localize(as_of)
    else:
        as_of = as_of.astimezone(ET)
    start = (as_of - timedelta(days=lookback_days)).date().isoformat()
    end_utc = as_of.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{POLYGON_BASE}/v2/reference/news"
    params = {
        "ticker": symbol.upper(),
        "published_utc.gte": f"{start}T00:00:00Z",
        "published_utc.lt": end_utc,
        "limit": 40,
        "sort": "published_utc",
        "order": "desc",
        "apiKey": api_key,
    }
    try:
        r = requests.get(url, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out = []
    for art in data.get("results") or []:
        title = str(art.get("title") or "").strip()
        if not title:
            continue
        pub = str(art.get("published_utc") or "")
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
        if pub_dt >= as_of:
            continue
        age = max(0.0, (as_of - pub_dt).total_seconds() / 86400.0)
        out.append(
            {
                "age_days": round(age, 1),
                "title": title,
                "description": str(art.get("description") or "")[:160],
            }
        )
    return out


def call_openrouter(api_key: str, prompt: str, model: str) -> str:
    import requests

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ajklepp/Q-ALPHA",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.1,
        },
        timeout=60,
    )
    if resp.status_code == 429:
        raise RuntimeError("rate_limited")
    resp.raise_for_status()
    msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def parse_label_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    raw = m.group(0) if m else text
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def articles_from_cache(cache: dict, symbol: str, signal_date: str) -> list[dict]:
    sd = (cache or {}).get("symbol_days") or {}
    entry = sd.get(f"{symbol.upper()}|{signal_date}") or {}
    return list(entry.get("articles") or [])


@app.function(
    image=image,
    secrets=[polygon_secret, qalpha_secrets],
    timeout=180,
    retries=2,
    max_containers=50,
)
def label_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Label a single admit with structured AI tags (causal news < signal)."""
    import pytz

    ET = pytz.timezone("America/New_York")
    sym = str(payload["symbol"]).upper()
    signal_date = str(payload["signal_date"])
    signal_ts = str(payload.get("signal_ts") or signal_date)
    poly = os.environ.get("POLYGON_API_KEY") or ""
    or_key = os.environ.get("OPENROUTER_API_KEY") or ""
    model = (os.environ.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_MODEL

    # Prefer pre-fetched causal articles when provided
    arts = payload.get("articles") or []
    if not arts and poly:
        arts = fetch_news(sym, signal_ts, poly, lookback_days=90)
        time.sleep(0.12)

    if not arts:
        lab = _empty_label(sym, reason="no_headlines")
        lab.update(
            {
                "signal_date": signal_date,
                "hour": payload.get("hour"),
                "row_id": payload.get("row_id"),
            }
        )
        return lab

    lines = []
    for a in arts[:25]:
        bit = f"[{a.get('age_days')}d] {a.get('title')}"
        if a.get("description"):
            bit += f" — {str(a.get('description'))[:100]}"
        lines.append(bit)
    blob = "\n".join(lines)

    if not or_key:
        lab = _empty_label(sym, reason="no_openrouter")
        lab["event_family"] = "none"
        lab["story_phase"] = "stale_narrative" if arts else "quiet"
        lab["summary_line"] = str(arts[0].get("title") or "")[:140]
        lab["derived_mode"] = derive_mode(lab)
        lab.update({"signal_date": signal_date, "hour": payload.get("hour"), "row_id": payload.get("row_id")})
        return lab

    prompt = f"""You label financial news for a short-horizon momentum system.
As of signal time {signal_ts} for ticker {sym}, use ONLY these headlines (age in days):
{blob}

Return ONLY valid JSON (no markdown):
{{
  "event_family": one of {list(EVENT_FAMILIES)},
  "info_hardness": one of {list(HARDNESS)},
  "story_phase": one of {list(STORY_PHASES)},
  "expectation_gap": one of {list(EXPECT_GAPS)},
  "rumor_or_speculation": 0 or 1,
  "forward_looking": 0 or 1,
  "primary_source": 0 or 1,
  "uncertainty_resolving": 0 or 1,
  "horizon": "intraday"|"days"|"weeks"|"undated",
  "summary_line": "<=140 chars what the market is trading"
}}

Rules:
- hard_quantified = earnings/guidance/analyst/dividends with numbers or clear beat/miss
- soft_narrative = launches, capacity stories, vibe, commentary without hard surprise
- pre_event_anticipation = expectation of upcoming clarity WITHOUT the hard result yet
- post_print_digest = after a print, market fighting hope vs outcome
- expectation_gap = vs what people seemed to want (not whether company is 'good')
- rumor_or_speculation=1 only if coverage is speculative / unconfirmed
- Do not invent facts beyond headlines; use none/quiet/unknown when unclear
"""
    try:
        raw = None
        for attempt in range(4):
            try:
                raw = call_openrouter(or_key, prompt, model)
                break
            except RuntimeError as exc:
                if "rate_limited" in str(exc) and attempt < 3:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        if raw is None:
            raise RuntimeError("openrouter_failed")
        parsed = parse_label_json(raw)
        if not parsed:
            raise RuntimeError(f"bad_json:{raw[:100]!r}")
        lab = _empty_label(sym)
        lab["label_ok"] = 1
        lab["event_family"] = str(parsed.get("event_family") or "none")
        if lab["event_family"] not in EVENT_FAMILIES:
            lab["event_family"] = "none"
        lab["info_hardness"] = str(parsed.get("info_hardness") or "unknown")
        if lab["info_hardness"] not in HARDNESS:
            lab["info_hardness"] = "unknown"
        lab["story_phase"] = str(parsed.get("story_phase") or "quiet")
        if lab["story_phase"] not in STORY_PHASES:
            lab["story_phase"] = "quiet"
        lab["expectation_gap"] = str(parsed.get("expectation_gap") or "unknown")
        if lab["expectation_gap"] not in EXPECT_GAPS:
            lab["expectation_gap"] = "unknown"
        for k in (
            "rumor_or_speculation",
            "forward_looking",
            "primary_source",
            "uncertainty_resolving",
        ):
            lab[k] = int(bool(parsed.get(k)))
        lab["horizon"] = str(parsed.get("horizon") or "undated")
        lab["summary_line"] = str(parsed.get("summary_line") or "")[:160]
        lab["derived_mode"] = derive_mode(lab)
        lab["model"] = model
    except Exception as exc:
        lab = _empty_label(sym, reason=str(exc)[:120])
        lab["summary_line"] = str(arts[0].get("title") or "")[:140]
        lab["derived_mode"] = derive_mode(lab)

    lab.update(
        {
            "signal_date": signal_date,
            "hour": payload.get("hour"),
            "row_id": payload.get("row_id"),
            "n_headlines": len(arts),
        }
    )
    return lab


@app.function(
    image=image,
    secrets=[polygon_secret, qalpha_secrets],
    timeout=60 * 120,
    memory=4096,
)
def run_study(sample_size: int = SAMPLE_DEFAULT, seed: int = 21) -> dict[str, Any]:
    """Label admits (all or sample), parallel on Modal, bakeoff vs v1.1."""
    import pandas as pd
    import pytz

    t0 = time.time()
    ET = pytz.timezone("America/New_York")
    random.seed(seed)

    df = pd.read_csv("/data/corpus.csv")
    admits = df[df["all_hours_admit"] == 1].copy()
    admits["symbol"] = admits["symbol"].astype(str).str.upper()
    admits["signal_date"] = admits["signal_date"].astype(str)

    cache = {}
    try:
        with open("/data/belief_cache.json", encoding="utf-8") as f:
            maybe = json.load(f)
        if isinstance(maybe, dict) and "symbol_days" in maybe:
            cache = maybe
    except Exception:
        cache = {}

    admits = admits.reset_index(drop=True)
    admits["row_id"] = admits.index.astype(int)

    if sample_size and sample_size > 0 and sample_size < len(admits):
        admits["_has_news"] = (
            admits.get("news_velocity_24h", pd.Series(0, index=admits.index)).fillna(0) > 0
        ).astype(int)
        try:
            admits["_mfe_bin"] = pd.qcut(
                admits["day_mfe"].rank(method="first"), q=3, labels=["lo", "mid", "hi"]
            )
        except Exception:
            admits["_mfe_bin"] = "mid"
        parts = []
        per = max(40, sample_size // 6)
        for _, g in admits.groupby(["_has_news", "_mfe_bin"]):
            take_n = min(len(g), per)
            parts.append(g.sample(n=take_n, random_state=seed))
        sample = pd.concat(parts).drop_duplicates(subset=["row_id"])
        if len(sample) > sample_size:
            sample = sample.sample(n=sample_size, random_state=seed)
        sample = sample.reset_index(drop=True)
        print(f"Stratified sample: {len(sample)} / admits {len(admits)}")
    else:
        sample = admits
        print(f"Labeling ALL admits: {len(sample)}")

    # One AI label per symbol-day (sticky narrative). as_of = earliest admit
    # signal_ts that day — no look-ahead vs first appearance.
    symday = (
        sample.sort_values(["signal_date", "hour"])
        .groupby(["symbol", "signal_date"], as_index=False)
        .agg(
            signal_ts=("signal_ts", "first"),
            hour=("hour", "first"),
            row_id=("row_id", "first"),
        )
    )
    payloads = []
    for _, r in symday.iterrows():
        arts = articles_from_cache(cache, str(r["symbol"]), str(r["signal_date"]))
        payloads.append(
            {
                "row_id": int(r["row_id"]),
                "symbol": str(r["symbol"]),
                "signal_date": str(r["signal_date"]),
                "signal_ts": str(r.get("signal_ts") or r["signal_date"]),
                "hour": int(r.get("hour") or 0),
                "articles": arts,
            }
        )

    print(
        f"Parallel labeling {len(payloads)} symbol-days "
        f"(from {len(sample)} admit rows) on Modal…"
    )
    labels = list(label_one.map(payloads, order_outputs=True, return_exceptions=True))
    clean_labels = []
    errors = 0
    for lab in labels:
        if isinstance(lab, Exception):
            errors += 1
            continue
        clean_labels.append(lab)
    print(f"Labeled ok={len(clean_labels)} errors={errors}")

    lab_df = pd.DataFrame(clean_labels)
    if lab_df.empty:
        return {"error": "no_labels", "runtime_sec": time.time() - t0}

    import sys

    sys.path.insert(0, "/pkg")
    from tsd_launch_score import compute_continuation_score_v1_1  # noqa: E402

    scored = df.copy()
    scored["symbol"] = scored["symbol"].astype(str).str.upper()
    scored["signal_date"] = scored["signal_date"].astype(str)
    scored["score_v11"] = [
        float(compute_continuation_score_v1_1(r.to_dict()))
        for _, r in scored.iterrows()
    ]

    lab_by_key = {
        f"{str(r['symbol']).upper()}|{str(r['signal_date'])}": r
        for _, r in lab_df.iterrows()
    }

    adjs = []
    modes = []
    fams = []
    hards = []
    phases = []
    gaps = []
    for _, r in scored.iterrows():
        key = f"{str(r['symbol']).upper()}|{str(r['signal_date'])}"
        lab = lab_by_key.get(key)
        # Only attach labels to rows in the sampled admit set's symbol-days
        # (or all admits when full run). Non-admits stay unlabeled.
        if lab is None or int(r.get("all_hours_admit") or 0) != 1:
            adjs.append(0.0)
            modes.append("unlabeled")
            fams.append("unlabeled")
            hards.append("unlabeled")
            phases.append("unlabeled")
            gaps.append("unlabeled")
        else:
            adjs.append(float(belief_score_adj(lab)))
            modes.append(str(lab.get("derived_mode") or "other"))
            fams.append(str(lab.get("event_family") or "none"))
            hards.append(str(lab.get("info_hardness") or "unknown"))
            phases.append(str(lab.get("story_phase") or "quiet"))
            gaps.append(str(lab.get("expectation_gap") or "unknown"))

    scored["belief_adj"] = adjs
    scored["derived_mode"] = modes
    scored["event_family"] = fams
    scored["info_hardness"] = hards
    scored["story_phase"] = phases
    scored["expectation_gap"] = gaps
    scored["score_belief_v2"] = scored["score_v11"] + scored["belief_adj"]

    # For fair bakeoff: only re-rank hours where at least one labeled admit exists;
    # unlabeled keep base score (adj=0). Same as soft overlay when coverage partial.

    admits_s = scored[scored["all_hours_admit"] == 1]
    expanders = (
        admits_s.sort_values(["signal_date", "day_mfe"], ascending=[True, False])
        .groupby("signal_date", as_index=False)
        .head(3)
    )

    m_v11 = metrics(simulate_slots(scored, score_col="score_v11"), expanders)
    m_v2 = metrics(simulate_slots(scored, score_col="score_belief_v2"), expanders)
    oos = scored[scored["signal_date"].astype(str) >= OOS_CUT]
    m_v11_oos = metrics(simulate_slots(oos, score_col="score_v11"), expanders)
    m_v2_oos = metrics(simulate_slots(oos, score_col="score_belief_v2"), expanders)

    # Hard filters among labeled-only simulation: rebuild admit mask
    def filter_sim(mask_mode: str | None = None, mask_hard: str | None = None):
        tmp = scored.copy()
        if mask_mode:
            # skip these modes (treat as non-admit)
            bad = tmp["derived_mode"] == mask_mode
            tmp.loc[bad, "all_hours_admit"] = 0
        if mask_hard:
            bad = tmp["info_hardness"] == mask_hard
            # only skip soft among labeled
            labeled = tmp["derived_mode"] != "unlabeled"
            tmp.loc[bad & labeled, "all_hours_admit"] = 0
        return metrics(simulate_slots(tmp, score_col="score_v11"), expanders)

    m_skip_digest = filter_sim(mask_mode="digest")
    m_skip_hype = filter_sim(mask_mode="hype_soft")
    m_skip_junk = filter_sim(mask_mode="junk")

    # Strata on labeled sample only
    labeled_admits = admits_s[admits_s["derived_mode"] != "unlabeled"].copy()

    def strata(col: str) -> list[dict[str, Any]]:
        rows = []
        for val, g in labeled_admits.groupby(col):
            rows.append(
                {
                    "bucket": str(val),
                    "n": int(len(g)),
                    "wr": float(g["hit_1r"].mean()),
                    "exp": float(g["r_multiple"].mean()),
                    "med_mfe": float(g["mfe"].median()),
                }
            )
        rows.sort(key=lambda x: -x["n"])
        return rows

    bakeoff = {
        "v11": {**m_v11, "oos": m_v11_oos, "pass_vs_v11": True},
        "belief_v2_adj": {
            **m_v2,
            "oos": m_v2_oos,
            "pass_vs_v11": ship_pass(m_v2, m_v11),
        },
        "skip_digest": {
            **m_skip_digest,
            "oos": filter_sim(mask_mode="digest"),  # approx; full OOS omitted for speed
            "pass_vs_v11": ship_pass(m_skip_digest, m_v11),
        },
        "skip_hype_soft": {
            **m_skip_hype,
            "pass_vs_v11": ship_pass(m_skip_hype, m_v11),
        },
        "skip_junk": {
            **m_skip_junk,
            "pass_vs_v11": ship_pass(m_skip_junk, m_v11),
        },
    }
    # Fix skip_digest oos properly
    oos_skip = oos.copy()
    oos_skip.loc[oos_skip["derived_mode"] == "digest", "all_hours_admit"] = 0
    bakeoff["skip_digest"]["oos"] = metrics(
        simulate_slots(oos_skip, score_col="score_v11"), expanders
    )

    winners = [k for k, v in bakeoff.items() if k != "v11" and v.get("pass_vs_v11")]
    best = None
    if winners:
        best = max(winners, key=lambda k: bakeoff[k].get("oos", bakeoff[k])["exp"]
                   if isinstance(bakeoff[k].get("oos"), dict)
                   else bakeoff[k]["exp"])

    if best is None:
        recommendation = "HOLD"
        rec_text = (
            "No structured-label variant beat v1.1 on the ship gate. "
            "Keep live on continuation_score_v1.1; keep labels for thesis/research."
        )
    elif str(best).startswith("skip_"):
        recommendation = "HARD"
        rec_text = f"Best passer: **{best}**. Hard filter only if you approve a rewrite."
    else:
        recommendation = "SOFT"
        rec_text = f"Best passer: **{best}**. Soft score overlay only if you approve."

    result = {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": round(time.time() - t0, 1),
        "sample_size": int(len(sample)),
        "symbol_days_labeled": int(len(payloads)),
        "labeled_ok": int(len(clean_labels)),
        "label_errors": int(errors),
        "label_ok_rate": float(lab_df["label_ok"].mean()) if "label_ok" in lab_df.columns else 0.0,
        "mode_counts": labeled_admits["derived_mode"].value_counts().to_dict(),
        "family_counts": labeled_admits["event_family"].value_counts().to_dict(),
        "hardness_counts": labeled_admits["info_hardness"].value_counts().to_dict(),
        "phase_counts": labeled_admits["story_phase"].value_counts().to_dict(),
        "gap_counts": labeled_admits["expectation_gap"].value_counts().to_dict(),
        "strata_mode": strata("derived_mode"),
        "strata_hardness": strata("info_hardness"),
        "strata_phase": strata("story_phase"),
        "strata_gap": strata("expectation_gap"),
        "strata_family": strata("event_family"),
        "baseline_v11": m_v11,
        "bakeoff": bakeoff,
        "winners": winners,
        "best_variant": best,
        "recommendation": recommendation,
        "recommendation_text": rec_text,
        "labels": clean_labels,
    }
    return result


@app.local_entrypoint()
def main(sample_size: int = SAMPLE_DEFAULT, seed: int = 21):
    """Launch Modal study and write local markdown/json outputs."""
    print(
        f"Launching Modal belief-labels v2 "
        f"(sample_size={sample_size or 'ALL admits'})"
    )
    result = run_study.remote(sample_size=sample_size, seed=seed)
    if result.get("error"):
        print("ERROR", result)
        return

    # Persist labels cache
    LABELS_LOCAL.write_text(
        json.dumps(
            {"labels": result.get("labels") or [], "meta": {
                "generated": result.get("generated"),
                "sample_size": result.get("sample_size"),
            }},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    # Metrics without huge labels blob
    metrics_out = {k: v for k, v in result.items() if k != "labels"}
    OUT_JSON_LOCAL.write_text(
        json.dumps(metrics_out, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# EXP-0021 — Belief labels v2 (Modal)",
        "",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s · sample={result.get('sample_size')} · "
        f"labeled_ok={result.get('labeled_ok')} · label_ok_rate={result.get('label_ok_rate', 0):.1%}",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        str(result.get("recommendation_text") or ""),
        "",
        "## Schema",
        "",
        "Orthogonal AI tags: `event_family` × `info_hardness` × `story_phase` × "
        "`expectation_gap` (+ rumor/forward/primary/horizon). Derived mode for humans only.",
        "",
        "## Counts (labeled admits)",
        "",
        f"- derived_mode: `{json.dumps(result.get('mode_counts'))}`",
        f"- info_hardness: `{json.dumps(result.get('hardness_counts'))}`",
        f"- story_phase: `{json.dumps(result.get('phase_counts'))}`",
        f"- expectation_gap: `{json.dumps(result.get('gap_counts'))}`",
        "",
        "## Strata by derived_mode",
        "",
        "| Mode | n | WR | Exp R | med MFE |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in result.get("strata_mode") or []:
        lines.append(
            f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
        )
    lines += [
        "",
        "## Strata by info_hardness",
        "",
        "| Hardness | n | WR | Exp R | med MFE |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in result.get("strata_hardness") or []:
        lines.append(
            f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
        )
    lines += [
        "",
        "## Strata by expectation_gap",
        "",
        "| Gap | n | WR | Exp R | med MFE |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in result.get("strata_gap") or []:
        lines.append(
            f"| {r['bucket']} | {r['n']} | {r['wr']:.1%} | {r['exp']:.3f} | {r['med_mfe']:.3f} |"
        )
    lines += [
        "",
        "## Bakeoff vs v1.1",
        "",
        "| Variant | n | WR | Exp R | Capture | PASS? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in ["v11", "belief_v2_adj", "skip_digest", "skip_hype_soft", "skip_junk"]:
        b = (result.get("bakeoff") or {}).get(name) or {}
        flag = "—" if name == "v11" else ("YES" if b.get("pass_vs_v11") else "no")
        lines.append(
            f"| {name} | {b.get('n', 0)} | {float(b.get('wr') or 0):.1%} | "
            f"{float(b.get('exp') or 0):.3f} | {float(b.get('capture') or 0):.1%} | {flag} |"
        )
    lines += [
        "",
        "## Recommended live action",
        "",
        f"- **{result.get('recommendation')}** — {result.get('recommendation_text')}",
        "- No live rewrite from this run.",
        "",
        "## Note on compute",
        "",
        "This study ran on **Modal** (parallel `label_one.map`). "
        "Local machine only launched the job and wrote results.",
        "",
    ]
    OUT_MD_LOCAL.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "recommendation": result.get("recommendation"),
        "best_variant": result.get("best_variant"),
        "winners": result.get("winners"),
        "baseline_exp": (result.get("baseline_v11") or {}).get("exp"),
        "runtime_sec": result.get("runtime_sec"),
        "wrote": str(OUT_MD_LOCAL),
    }, indent=2))
