"""
Q-ALPHA UTS v2 Phase 2 — quality + history gate (NOT a news veto).

Research filter: instrument safety, liquidity, profiler analog depth/win rate.
News/sentiment are context tags only — never block admission.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_launch_score import enrich_launch_fields
from tsd_scan_pipeline.tsd_profiler import MIN_TSD_ANALOGS
from tsd_scan_pipeline.universe_tsd import (
    MCAP_MIN,
    MIN_DOLLAR_VOL_20D,
    POLYGON_BASE,
    load_polygon_key,
    polygon_get,
)
from universe_filter import passes_instrument_safety

ET = pytz.timezone("America/New_York")

# Hard-block thresholds
ANALOG_WIN_RATE_MIN = 40.0  # percent of decisive analog outcomes (WIN vs LOSS)
MIN_PRICE_FLOOR = 5.0
LOW_FLOAT_SPEC_SHARES = 15_000_000
HIGH_SHORT_INTEREST_PCT = 20.0
SQUEEZE_SIZE_MULT = 1.15
LOW_FLOAT_SIZE_MULT = 0.5
CATALYST_DISPLAY_BONUS = 5
NEWS_LOOKBACK_HOURS = 48
FUNDAMENTAL_DISTRESS_SPEC_ONLY = True

TIER1_KEYWORDS = (
    "earnings beat", "fda", "approval", "contract", "acquisition",
    "guidance raised", "upgrade", "partnership", "revenue",
)
NEGATIVE_SENTIMENT_KEYWORDS = (
    "downgrade", "lawsuit", "offering", "dilution", "bankruptcy",
    "investigation", "recall", "miss", "cut guidance",
)
POSITIVE_SENTIMENT_KEYWORDS = (
    "beat", "approval", "upgrade", "raised", "surge", "record",
    "partnership", "contract", "breakthrough",
)
DISTRESS_KEYWORDS = ("offering", "dilution", "bankruptcy", "going concern", "negative equity")


def analog_outcome_label(
    mfe_pct: float | None,
    mae_pct: float | None,
    kill_pct: float,
) -> str:
    """WIN if MFE >= 2× kill before MAE breaches kill; else LOSS or FLAT."""
    if mfe_pct is None or mae_pct is None:
        return "UNKNOWN"
    if mfe_pct >= 2.0 * kill_pct and mae_pct < kill_pct:
        return "WIN"
    if mae_pct >= kill_pct:
        return "LOSS"
    return "FLAT"


def compute_analog_win_rate(profile: dict[str, Any]) -> float | None:
    """
    Win rate (%) from profile analog_win_rate or measured outcome stats.

    Decisive outcomes = WIN + LOSS (FLAT excluded).
    """
    if profile.get("analog_win_rate") is not None:
        return float(profile["analog_win_rate"])

    wins = profile.get("analog_wins")
    losses = profile.get("analog_losses")
    if wins is not None and losses is not None:
        decisive = int(wins) + int(losses)
        if decisive <= 0:
            return None
        return round(100.0 * int(wins) / decisive, 1)
    return None


def _profile_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    prof = candidate.get("tsd_profile")
    if isinstance(prof, dict):
        return prof
    gate = candidate.get("profiler") or {}
    nested = gate.get("profile")
    return nested if isinstance(nested, dict) else {}


def _analog_count(candidate: dict[str, Any], profile: dict[str, Any]) -> int:
    if profile.get("analog_count") is not None:
        return int(profile["analog_count"])
    gate = candidate.get("profiler") or {}
    if gate.get("analog_count") is not None:
        return int(gate["analog_count"])
    return 0


def detect_fundamental_distress(candidate: dict[str, Any], text: str = "") -> bool:
    """True when explicit flag or distress keywords in supplied text."""
    if candidate.get("fundamental_distress"):
        return True
    blob = " ".join(
        str(candidate.get(k) or "")
        for k in ("name", "news_summary", "fundamental_note")
    ).lower()
    blob = f"{blob} {text.lower()}".strip()
    return any(kw in blob for kw in DISTRESS_KEYWORDS)


def evaluate_quality_history_gate(
    candidate: dict[str, Any],
) -> tuple[bool, dict[str, bool], list[str]]:
    """
    Hard quality/history blocks only. News never vetoes.

    Returns (passed, gates_dict, reasons).
    """
    sym = str(candidate.get("symbol", "")).upper()
    row = enrich_launch_fields(candidate)
    profile = _profile_from_candidate(row)
    analog_count = _analog_count(row, profile)
    win_rate = compute_analog_win_rate(profile)

    mcap = row.get("market_cap")
    dollar_vol = row.get("dollar_vol_20d") or row.get("dollar_volume")
    price = float(row.get("close") or row.get("price") or 0)
    distress = detect_fundamental_distress(row)

    gates: dict[str, bool] = {
        "instrument_safety": passes_instrument_safety(sym, require_cs_cache=False) if sym else False,
        "mcap_floor": True if mcap is None else float(mcap) >= MCAP_MIN,
        "dollar_vol_floor": True if dollar_vol is None else float(dollar_vol) >= MIN_DOLLAR_VOL_20D,
        "price_floor": price >= MIN_PRICE_FLOOR if price > 0 else True,
        "analog_count": analog_count >= MIN_TSD_ANALOGS,
        "analog_win_rate": True if win_rate is None else win_rate >= ANALOG_WIN_RATE_MIN,
        "not_extension": row.get("phase") != "EXTENSION",
        "no_distress": not distress if not FUNDAMENTAL_DISTRESS_SPEC_ONLY else True,
    }

    reasons: list[str] = []
    if not gates["instrument_safety"]:
        reasons.append("instrument_safety_fail")
    if not gates["mcap_floor"]:
        reasons.append(f"mcap<{MCAP_MIN:.0f}")
    if not gates["dollar_vol_floor"]:
        reasons.append(f"dollar_vol<{MIN_DOLLAR_VOL_20D:.0f}")
    if not gates["price_floor"]:
        reasons.append(f"price<{MIN_PRICE_FLOOR:.0f}")
    if not gates["analog_count"]:
        reasons.append(f"analog_count<{MIN_TSD_ANALOGS}")
    if not gates["analog_win_rate"]:
        reasons.append(f"analog_win_rate<{ANALOG_WIN_RATE_MIN:.0f}%")
    if not gates["not_extension"]:
        reasons.append("extension_phase")
    if distress and not FUNDAMENTAL_DISTRESS_SPEC_ONLY:
        reasons.append("fundamental_distress")

    passed = all(gates.values())
    return passed, gates, reasons


def apply_soft_tags(
    candidate: dict[str, Any],
    news_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Soft tags + size_mult only — never blocks.

    Tags: pre_catalyst, catalyst_confirmed, squeeze_candidate, low_float_spec, spec_lane.
    """
    out = dict(candidate)
    ctx = news_ctx or {}
    tags: list[str] = list(out.get("tags") or [])
    size_mult = float(out.get("size_mult") or 1.0)
    launch_score = float(out.get("launch_score") or 0)
    display_bonus = 0.0

    pre_catalyst = bool(ctx.get("pre_catalyst", True))
    catalyst_tier = int(ctx.get("catalyst_tier") or 0)
    sentiment = float(ctx.get("sentiment_score") or 0.0)

    if pre_catalyst:
        if "pre_catalyst" not in tags:
            tags.append("pre_catalyst")
    if catalyst_tier >= 1:
        if "catalyst_confirmed" not in tags:
            tags.append("catalyst_confirmed")
        display_bonus += CATALYST_DISPLAY_BONUS

    si_pct = out.get("short_interest_pct")
    if si_pct is not None and float(si_pct) >= HIGH_SHORT_INTEREST_PCT:
        if "squeeze_candidate" not in tags:
            tags.append("squeeze_candidate")
        size_mult = max(size_mult, SQUEEZE_SIZE_MULT)

    float_shares = out.get("float_shares")
    if float_shares is not None and float(float_shares) < LOW_FLOAT_SPEC_SHARES:
        if "low_float_spec" not in tags:
            tags.append("low_float_spec")
        size_mult *= LOW_FLOAT_SIZE_MULT

    if detect_fundamental_distress(out, str(ctx.get("news_summary") or "")):
        if FUNDAMENTAL_DISTRESS_SPEC_ONLY:
            if "spec_lane" not in tags:
                tags.append("spec_lane")
            size_mult = min(size_mult, LOW_FLOAT_SIZE_MULT)

    out["tags"] = tags
    out["size_mult"] = round(size_mult, 3)
    out["launch_score_display"] = round(launch_score + display_bonus, 1)
    out["pre_catalyst"] = pre_catalyst
    out["catalyst_tier"] = catalyst_tier
    out["sentiment_score"] = round(sentiment, 2)
    out["news_summary"] = ctx.get("news_summary") or out.get("news_summary") or ""
    return out


def _classify_catalyst_tier(headlines: list[str], summary: str) -> int:
    """0 = none, 1 = tier-1 catalyst present."""
    if not headlines and not summary:
        return 0
    blob = f"{summary} {' '.join(headlines)}".lower()
    if "no catalyst" in blob or "no news found" in blob:
        return 0
    if any(kw in blob for kw in TIER1_KEYWORDS):
        return 1
    if headlines:
        return 1
    return 0


def _sentiment_from_text(text: str) -> float:
    """Simple -1..+1 sentiment from summary/headlines (never blocks)."""
    if not text:
        return 0.0
    low = text.lower()
    score = 0.0
    if any(kw in low for kw in NEGATIVE_SENTIMENT_KEYWORDS):
        score -= 0.6
    if any(kw in low for kw in POSITIVE_SENTIMENT_KEYWORDS):
        score += 0.6
    return max(-1.0, min(1.0, score))


def fetch_headlines_48h(symbol: str, api_key: str | None = None) -> list[str]:
    """Polygon news headlines in the last NEWS_LOOKBACK_HOURS."""
    key = api_key or load_polygon_key()
    sym = symbol.upper()
    since = (datetime.now(ET) - timedelta(hours=NEWS_LOOKBACK_HOURS)).strftime("%Y-%m-%d")
    url = f"{POLYGON_BASE}/v2/reference/news"
    params = {
        "ticker": sym,
        "published_utc.gte": f"{since}T00:00:00Z",
        "limit": 10,
        "sort": "published_utc",
        "order": "desc",
    }
    try:
        data = polygon_get(url, params, key)
        headlines: list[str] = []
        for article in data.get("results") or []:
            title = str(article.get("title") or "").strip()
            if title:
                headlines.append(title)
        return headlines
    except Exception as exc:
        print(f"  news fetch {sym}: {exc}")
        return []


def fetch_news_context(
    symbol: str,
    *,
    polygon_key: str | None = None,
    summarize: bool = True,
) -> dict[str, Any]:
    """
    Fetch news + optional AI summary AFTER quality pass.

    Context only — never used to veto admission.
    """
    sym = symbol.upper()
    key = polygon_key or load_polygon_key()
    headlines = fetch_headlines_48h(sym, key)
    pre_catalyst = len(headlines) == 0

    summary = ""
    if summarize:
        try:
            from catalyst_ai import summarize_catalyst

            summary = summarize_catalyst(sym, headlines)
        except Exception as exc:
            print(f"  catalyst_ai {sym}: {exc}")
            summary = headlines[0] if headlines else "🔀 No Catalyst: No news found — possible technical move"
    elif headlines:
        summary = headlines[0]

    tier = _classify_catalyst_tier(headlines, summary)
    sentiment = _sentiment_from_text(summary)

    return {
        "news_summary": summary,
        "catalyst_tier": tier,
        "sentiment_score": sentiment,
        "pre_catalyst": pre_catalyst,
        "headline_count": len(headlines),
    }


def enrich_queue_row(
    candidate: dict[str, Any],
    *,
    polygon_key: str | None = None,
    fetch_news: bool = True,
) -> tuple[dict[str, Any], bool, dict[str, bool], list[str]]:
    """
    Full Phase 2 pipeline: quality gate → news context → soft tags.

    Returns (row, passed, gates, reasons).
    """
    passed, gates, reasons = evaluate_quality_history_gate(candidate)
    if not passed:
        return dict(candidate), False, gates, reasons

    row = enrich_launch_fields(dict(candidate))
    news_ctx: dict[str, Any] = {}
    if fetch_news:
        try:
            news_ctx = fetch_news_context(
                str(row.get("symbol", "")),
                polygon_key=polygon_key,
                summarize=True,
            )
            time.sleep(0.12)
        except Exception as exc:
            print(f"  news context skipped: {exc}")
            news_ctx = {
                "news_summary": "",
                "catalyst_tier": 0,
                "sentiment_score": 0.0,
                "pre_catalyst": True,
                "headline_count": 0,
            }

    row = apply_soft_tags(row, news_ctx)
    row["quality_gates"] = gates
    row["analog_count"] = _analog_count(row, _profile_from_candidate(row))
    row["analog_win_rate"] = compute_analog_win_rate(_profile_from_candidate(row))
    return row, True, gates, reasons
