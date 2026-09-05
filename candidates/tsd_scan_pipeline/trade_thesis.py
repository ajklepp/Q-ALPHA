"""
Trade thesis — plain-English why-card from multi-source decision fields.

No scores, hours, kill/1R, or gate names — story + evidence buckets only.
Frozen at decision time (entry or miss); do not recompute with look-ahead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz

ET = pytz.timezone("America/New_York")

BUCKET_ORDER = ("Tape", "Trend", "Catalyst", "History", "Liquidity")


def _finite(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _present(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    if isinstance(val, (list, dict)) and not val:
        return False
    return True


def _tape_text(row: dict[str, Any]) -> tuple[str, bool]:
    """Return (text, source_used)."""
    bar = str(row.get("bar_state") or "").lower()
    buy = bool(row.get("buy_signal") or row.get("htf_1h_buy_signal"))
    early = bool(row.get("early_bull"))
    used = bool(bar) or buy or early or row.get("open") is not None or row.get("close") is not None

    parts: list[str] = []
    if bar == "yellow":
        parts.append("constructive signal bar")
    elif bar == "green":
        parts.append("strong green signal bar")
    elif bar == "red":
        parts.append("red signal bar — still admitted")
    elif bar == "orange":
        parts.append("quiet / doji-like signal bar")
    elif bar == "extended":
        parts.append("extended tape — deprioritized")
    elif bar:
        parts.append("signal bar reviewed")

    if buy:
        parts.append("trigger present")
    elif early:
        parts.append("early-bull tape")

    scan = _finite(row.get("scan_score") or row.get("htf_1h_scan_score"))
    if scan is not None:
        if scan >= 75:
            parts.append("already stretched")
        elif scan <= 55:
            parts.append("not extended")

    if not parts:
        return ("tape fields thin / unknown", used)
    return (" · ".join(parts), used)


def _trend_text(row: dict[str, Any]) -> tuple[str, bool]:
    used = any(
        _present(row.get(k))
        for k in (
            "htf_close_above_sma50",
            "htf_sma20_rising",
            "htf_dist_sma50_pct",
            "htf_range_20d_pct",
            "dist_20d_high_pct",
            "close_vs_sma50",
            "phase",
            "phase_3h",
        )
    )
    parts: list[str] = []
    above = row.get("htf_close_above_sma50")
    if above is True:
        parts.append("above mid trend")
    elif above is False:
        parts.append("below mid trend")

    rising = row.get("htf_sma20_rising")
    if rising is True:
        parts.append("trend slope up")
    elif rising is False:
        parts.append("trend slope flat/down")

    room = _finite(row.get("dist_20d_high_pct") or row.get("htf_dist_52w_high_pct"))
    if room is not None:
        if room > 0.08:
            parts.append("room vs recent highs")
        elif room < 0.03:
            parts.append("near recent highs")

    rng = _finite(row.get("htf_range_20d_pct"))
    if rng is not None and rng >= 0.25:
        parts.append("active range regime")

    phase = str(row.get("phase_3h") or row.get("phase") or "").upper()
    if phase and phase not in ("", "NONE", "UNKNOWN"):
        # Soft label only — no strategy jargon
        if phase in ("LAUNCH", "EARLY", "EARLY_BULL"):
            parts.append("early-phase structure")
        elif phase in ("NEUTRAL",):
            parts.append("neutral structure")
        elif "EXT" in phase:
            parts.append("later-phase structure")

    if not parts:
        return ("trend context incomplete", used)
    return (" · ".join(parts), used)


def _catalyst_text(row: dict[str, Any]) -> tuple[str, bool]:
    print_tag = row.get("print") or row.get("print_tag")
    outlook = str(row.get("outlook") or "").lower().strip()
    news_n = _finite(row.get("news_headline_count_48h") or row.get("news_velocity_24h"))
    guidance = bool(row.get("guidance_cut"))
    st_msg = _finite(row.get("st_msg_24h"))
    st_bull = _finite(row.get("st_bull_ratio"))
    cat_type = row.get("catalyst_type")
    used = (
        _present(print_tag)
        or _present(outlook)
        or news_n is not None
        or "guidance_cut" in row
        or st_msg is not None
        or _present(cat_type)
    )

    parts: list[str] = []
    if guidance or outlook in ("lowered", "withdrawn"):
        parts.append("guidance cut — caution")
    elif outlook in ("raised", "increased", "positive"):
        parts.append("outlook constructive")
    elif outlook and outlook not in ("unknown", "none", "null"):
        parts.append("outlook noted")

    if bool(row.get("dilution_flag")):
        parts.append("dilution / offering headline — caution")
    if bool(row.get("distress_flag")):
        parts.append("distress headline — caution")

    if _present(print_tag) and str(print_tag).lower() not in ("unknown", "none", "null"):
        parts.append(f"print: {str(print_tag).replace('_', ' ')}")
    elif news_n is not None and news_n <= 0:
        parts.append("no fresh headlines in window")
    elif news_n is not None and news_n > 0:
        parts.append(f"recent headlines present ({int(news_n)})")

    if _present(cat_type) and str(cat_type).lower() not in ("none", "unknown", "null", ""):
        parts.append(f"catalyst type: {str(cat_type)}")

    if st_msg is not None and st_msg > 0:
        if st_bull is not None and st_bull >= 0.6:
            parts.append("social tape bullish")
        elif st_bull is not None and st_bull <= 0.4:
            parts.append("social tape mixed-soft")
        else:
            parts.append("social chatter present")

    if not parts:
        parts.append("catalyst quiet / unknown")

    pre = row.get("pre_catalyst")
    if pre is True:
        parts.append("pre-event window")

    return (" · ".join(parts), used)


def _history_text(row: dict[str, Any]) -> tuple[str, bool]:
    wr = _finite(row.get("analog_win_rate") or row.get("ticker_prior_hit1r_rate"))
    mfe = _finite(row.get("analog_mfe_p50") or row.get("ticker_prior_mfe_p50"))
    n_analog = row.get("analog_count") or row.get("n_analogs_measured")
    used = wr is not None or mfe is not None or _present(n_analog)

    parts: list[str] = []
    if wr is not None:
        # wr may be 0–1 or 0–100
        rate = wr if wr <= 1.0 else wr / 100.0
        if rate >= 0.55:
            parts.append("similar past days: constructive")
        elif rate >= 0.40:
            parts.append("similar past days: mixed-positive")
        else:
            parts.append("similar past days: mixed-soft")
    if mfe is not None and mfe > 0:
        parts.append("prior runs had follow-through")
    if _present(n_analog):
        try:
            n = int(n_analog)
            if n <= 0:
                parts.append("thin history sample")
        except (TypeError, ValueError):
            pass

    if not parts:
        return ("ticker history thin / unused", used)
    return (" · ".join(parts), used)


def _liquidity_text(row: dict[str, Any]) -> tuple[str, bool]:
    dv = _finite(row.get("dollar_vol_1h") or row.get("dollar_vol_20d_avg"))
    float_sh = _finite(row.get("float_shares"))
    used = dv is not None or float_sh is not None or _present(row.get("htf_1h_close"))

    parts: list[str] = []
    if dv is not None:
        if dv >= 5_000_000:
            parts.append("session dollar volume adequate")
        elif dv >= 1_000_000:
            parts.append("session dollar volume usable")
        else:
            parts.append("session dollar volume light")
    else:
        # Presence of a tradeable ref price implies we sized somehow
        ref = _finite(row.get("htf_1h_close") or row.get("entry_price") or row.get("close") or row.get("ref_price"))
        if ref is not None and ref > 0:
            parts.append("liquidity checked at size time")

    if float_sh is not None and float_sh > 0:
        parts.append("float known")

    if not parts:
        return ("liquidity not recorded", used)
    return (" · ".join(parts), used)


def _headline_from_bullets(
    bullets: list[dict[str, str]],
    *,
    outcome: str,
) -> str:
    by = {b["bucket"]: b["text"] for b in bullets}
    tape = by.get("Tape", "")
    trend = by.get("Trend", "")
    cat = by.get("Catalyst", "")
    liq = by.get("Liquidity", "")

    bits: list[str] = []
    if "constructive" in tape or "strong green" in tape or "trigger" in tape:
        bits.append("Constructive tape")
    elif "quiet" in tape or "doji" in tape:
        bits.append("Quiet base / soft tape")
    elif "extended" in tape or "stretched" in tape:
        bits.append("Extended tape")
    else:
        bits.append("Setup reviewed")

    if "above mid" in trend or "slope up" in trend:
        bits.append("trend intact")
    elif "below mid" in trend:
        bits.append("trend soft")

    if "guidance cut" in cat:
        bits.append("guidance caution")
    elif "no fresh" in cat or "quiet" in cat:
        bits.append("no adverse headline")
    elif "print:" in cat:
        bits.append("catalyst noted")

    if "adequate" in liq or "usable" in liq or "checked" in liq:
        bits.append("liquidity fine to size")

    core = "; ".join(bits) + "."
    if outcome.upper() == "MISSED":
        return f"Saw it, did not take — {core[0].lower()}{core[1:]}"
    return core


def build_trade_thesis(
    row: dict[str, Any],
    *,
    outcome: str = "TAKEN",
    asof: datetime | None = None,
) -> dict[str, Any]:
    """
    Build a frozen thesis card from decision-time fields.

    Returns:
      headline, bullets[{bucket,text}], sources_used, sources_missing, asof, outcome
    """
    when = asof or datetime.now(ET)
    if when.tzinfo is None:
        when = ET.localize(when)
    else:
        when = when.astimezone(ET)

    builders = {
        "Tape": _tape_text,
        "Trend": _trend_text,
        "Catalyst": _catalyst_text,
        "History": _history_text,
        "Liquidity": _liquidity_text,
    }
    bullets: list[dict[str, str]] = []
    used_flags: dict[str, bool] = {}
    for bucket in BUCKET_ORDER:
        text, used = builders[bucket](row)
        bullets.append({"bucket": bucket, "text": text})
        used_flags[bucket] = used

    # Map buckets → human source labels (no internal module names)
    source_labels = {
        "Tape": "intraday tape",
        "Trend": "daily structure",
        "Catalyst": "news window",
        "History": "ticker history",
        "Liquidity": "liquidity / size",
    }
    sources_used = [source_labels[b] for b in BUCKET_ORDER if used_flags[b]]
    sources_missing = [source_labels[b] for b in BUCKET_ORDER if not used_flags[b]]

    # Social is optional third-party — always call out if absent
    social_val = row.get("st_msg_24h") or row.get("social_msg_24h")
    if _present(social_val):
        if "social" not in " ".join(sources_used):
            sources_used.append("social")
    else:
        sources_missing.append("social")

    # Dedupe missing while preserving order
    seen: set[str] = set()
    missing_clean: list[str] = []
    for s in sources_missing:
        if s not in seen:
            seen.add(s)
            missing_clean.append(s)

    return {
        "headline": _headline_from_bullets(bullets, outcome=outcome),
        "bullets": bullets,
        "sources_used": sources_used,
        "sources_missing": missing_clean,
        "asof": when.isoformat(),
        "outcome": str(outcome or "TAKEN").upper(),
        "symbol": str(row.get("symbol") or "").upper() or None,
    }


def thesis_from_leg(leg: dict[str, Any], *, outcome: str = "TAKEN") -> dict[str, Any]:
    """Build thesis from a book leg (merge trail-agnostic fields)."""
    row = dict(leg)
    if "print_tag" in row and not row.get("print"):
        row["print"] = row.get("print_tag")
    return build_trade_thesis(row, outcome=outcome)
