"""
Deep catalyst lookback for Peak Hour — narrative + pending expectations.

Not a 48h “what gapped today” summarizer. Stocks often pump on:
  - older development / contract / pipeline news still in play
  - market expectation that results / delivery / data are coming soon
    even when no hard date is posted

Causal: only headlines with published_utc < as_of (signal time).
Soft context only — never hard-blocks admission.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import requests

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.universe_tsd import (  # noqa: E402
    POLYGON_BASE,
    load_polygon_key,
    polygon_get,
)

ET = pytz.timezone("America/New_York")

# Lookback for narrative / expectation research (not the thin 48h velocity window)
DEEP_LOOKBACK_DAYS = 90
DEEP_HEADLINE_CAP = 40
DEEP_CACHE_TTL_SEC = 6 * 3600  # per symbol per calendar day

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _as_et(dt: datetime | None) -> datetime:
    now = dt or datetime.now(ET)
    if now.tzinfo is None:
        return ET.localize(now)
    return now.astimezone(ET)


def fetch_polygon_news_lookback(
    symbol: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    lookback_days: int = DEEP_LOOKBACK_DAYS,
    limit: int = DEEP_HEADLINE_CAP,
) -> list[dict[str, Any]]:
    """
    Polygon news articles strictly before as_of, going back lookback_days.

    Returns newest-first list of {published_et, title, description, age_days}.
    """
    key = api_key or load_polygon_key()
    now = _as_et(as_of)
    start = (now - timedelta(days=int(lookback_days))).date().isoformat()
    # Exclusive upper bound: published before as_of
    end_utc = now.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{POLYGON_BASE}/v2/reference/news"
    params = {
        "ticker": symbol.upper(),
        "published_utc.gte": f"{start}T00:00:00Z",
        "published_utc.lt": end_utc,
        "limit": min(int(limit), 50),
        "sort": "published_utc",
        "order": "desc",
    }
    try:
        data = polygon_get(url, params, key)
        time.sleep(0.12)
    except Exception as exc:
        print(f"  deep news fetch {symbol}: {exc}")
        return []

    out: list[dict[str, Any]] = []
    for art in data.get("results") or []:
        title = str(art.get("title") or "").strip()
        if not title:
            continue
        pub = str(art.get("published_utc") or "")
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            continue
        if pub_dt >= now:
            continue
        age = max(0.0, (now - pub_dt).total_seconds() / 86400.0)
        desc = str(art.get("description") or "").strip()
        src = ""
        pub_obj = art.get("publisher") or {}
        if isinstance(pub_obj, dict):
            src = str(pub_obj.get("name") or "")
        out.append(
            {
                "published_et": pub_dt.isoformat(),
                "age_days": round(age, 1),
                "title": title,
                "description": desc[:280],
                "source": src,
            }
        )
    return out


def _call_openrouter(api_key: str, model: str, prompt: str, *, max_tokens: int = 450) -> str:
    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ajklepp/Q-ALPHA",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.15,
        },
        timeout=45,
    )
    if response.status_code == 429:
        raise RuntimeError(f"rate_limited:{model}")
    response.raise_for_status()
    result = response.json()
    msg = (result.get("choices") or [{}])[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _parse_deep_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    raw = m.group(0) if m else text
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _empty_brief(symbol: str, *, reason: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "deep_ok": 0,
        "deep_reason": reason,
        "narrative": "",
        "fresh_catalyst": 0,
        "stale_relevant": 0,
        "expectation_pending": 0,
        "expectation_what": "",
        "expectation_window": "unknown",
        "oldest_relevant_age_days": None,
        "key_headlines": [],
        "risk_flags": [],
        "catalyst_mode": "unknown",  # fresh | stale_relevant | expectation | quiet
        "deep_summary_line": "",
    }


def research_deep_catalyst(
    symbol: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    lookback_days: int = DEEP_LOOKBACK_DAYS,
    use_ai: bool = True,
) -> dict[str, Any]:
    """
    Deep lookback brief for one ticker as of signal time.

    Identifies fresh news, still-relevant older development news, and
    undated / soft-dated expectations the tape may be pricing.
    """
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    sym = symbol.upper()
    now = _as_et(as_of)
    cache_key = f"deep:{sym}:{now.date().isoformat()}:{lookback_days}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < DEEP_CACHE_TTL_SEC:
        return dict(hit[1])

    articles = fetch_polygon_news_lookback(
        sym,
        api_key=api_key,
        as_of=now,
        lookback_days=lookback_days,
    )
    if not articles:
        brief = _empty_brief(sym, reason="no_headlines_in_lookback")
        brief["catalyst_mode"] = "quiet"
        brief["deep_summary_line"] = "quiet tape — no catalyst trail in lookback"
        _CACHE[cache_key] = (time.time(), brief)
        return brief

    # Heuristic pre-tags (AI refines)
    fresh = any(float(a["age_days"]) <= 2.0 for a in articles)
    lines = []
    for a in articles[:25]:
        bit = f"[{a['age_days']}d] {a['title']}"
        if a.get("description"):
            bit += f" — {a['description'][:120]}"
        lines.append(bit)
    blob = "\n".join(lines)

    brief = _empty_brief(sym)
    brief["deep_ok"] = 1
    brief["key_headlines"] = [a["title"] for a in articles[:8]]
    brief["fresh_catalyst"] = int(fresh)
    brief["headline_count_lookback"] = len(articles)

    if not use_ai:
        brief["narrative"] = articles[0]["title"]
        brief["catalyst_mode"] = "fresh" if fresh else "stale_relevant"
        brief["deep_summary_line"] = brief["narrative"][:160]
        _CACHE[cache_key] = (time.time(), brief)
        return brief

    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        brief["deep_reason"] = "no_openrouter"
        brief["narrative"] = articles[0]["title"]
        brief["catalyst_mode"] = "fresh" if fresh else "stale_relevant"
        brief["deep_summary_line"] = brief["narrative"][:160]
        _CACHE[cache_key] = (time.time(), brief)
        return brief

    prompt = f"""You are a momentum-trading research analyst. As of {now.strftime('%Y-%m-%d %H:%M %Z')},
ticker {sym} may be moving. News that moves stocks is often NOT only today's headline.

It is frequently:
- older development / contract / pipeline / capacity news still in the narrative
- an EXPECTATION that results, delivery, data, approval, or financing clarity are coming soon
  even when NO firm date is posted

Headlines + snippets from the last {lookback_days} days (age in days, newest first):
{blob}

Return ONLY valid JSON (no markdown) with these keys:
{{
  "narrative": "2-3 sentences: what story is the market trading?",
  "fresh_catalyst": 0 or 1,
  "stale_relevant": 0 or 1,
  "expectation_pending": 0 or 1,
  "expectation_what": "what people may be awaiting (or empty)",
  "expectation_window": "today|this_week|near_term|undated|unknown",
  "oldest_relevant_age_days": number or null,
  "risk_flags": ["dilution"|"distress"|"guidance_cut"|"lawsuit"|...] ,
  "catalyst_mode": "fresh"|"stale_relevant"|"expectation"|"mixed"|"quiet",
  "summary_line": "one line <= 140 chars for a trade ticket"
}}
Rules:
- stale_relevant=1 if older news (e.g. >3d) still explains WHY this name can run
- expectation_pending=1 if tape may be pricing upcoming clarity without a hard date
- Do not invent tickers or facts not supported by the headlines
- risk_flags only if clearly supported
"""

    model = (os.environ.get("OPENROUTER_MODEL") or "").strip() or DEFAULT_MODEL
    try:
        raw = _call_openrouter(or_key, model, prompt, max_tokens=450)
        parsed = _parse_deep_json(raw)
        if not parsed:
            raise RuntimeError(f"bad_json:{raw[:120]!r}")
        brief["narrative"] = str(parsed.get("narrative") or "").strip()
        brief["fresh_catalyst"] = int(bool(parsed.get("fresh_catalyst")))
        brief["stale_relevant"] = int(bool(parsed.get("stale_relevant")))
        brief["expectation_pending"] = int(bool(parsed.get("expectation_pending")))
        brief["expectation_what"] = str(parsed.get("expectation_what") or "").strip()
        brief["expectation_window"] = str(
            parsed.get("expectation_window") or "unknown"
        ).strip().lower()
        age = parsed.get("oldest_relevant_age_days")
        try:
            brief["oldest_relevant_age_days"] = float(age) if age is not None else None
        except (TypeError, ValueError):
            brief["oldest_relevant_age_days"] = None
        flags = parsed.get("risk_flags") or []
        brief["risk_flags"] = [str(x) for x in flags] if isinstance(flags, list) else []
        mode = str(parsed.get("catalyst_mode") or "unknown").strip().lower()
        brief["catalyst_mode"] = mode
        brief["deep_summary_line"] = str(
            parsed.get("summary_line") or brief["narrative"]
        ).strip()[:160]
        brief["deep_model"] = model
        print(f"  deep_catalyst {sym} mode={mode} expect={brief['expectation_pending']}")
    except Exception as exc:
        print(f"  deep_catalyst AI {sym}: {exc}")
        brief["deep_reason"] = str(exc)[:120]
        brief["narrative"] = articles[0]["title"]
        brief["stale_relevant"] = int(not fresh)
        brief["catalyst_mode"] = "fresh" if fresh else "stale_relevant"
        brief["deep_summary_line"] = brief["narrative"][:160]

    _CACHE[cache_key] = (time.time(), brief)
    return brief


def attach_deep_catalyst(
    rows: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
    max_names: int = 5,
) -> list[dict[str, Any]]:
    """
    Attach deep briefs to the first max_names rows (usually top ranked).

    Soft fields only; safe if AI/news fail.
    """
    out = list(rows)
    for i, row in enumerate(out):
        if i >= int(max_names):
            break
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            brief = research_deep_catalyst(sym, api_key=api_key, as_of=as_of)
        except Exception as exc:
            print(f"  deep_catalyst attach {sym}: {exc}")
            brief = _empty_brief(sym, reason=str(exc)[:80])
        row = dict(row)
        row["deep_catalyst"] = brief
        row["catalyst_mode"] = brief.get("catalyst_mode")
        row["expectation_pending"] = int(brief.get("expectation_pending") or 0)
        row["expectation_what"] = brief.get("expectation_what") or ""
        row["stale_relevant"] = int(brief.get("stale_relevant") or 0)
        row["deep_summary_line"] = brief.get("deep_summary_line") or ""
        row["deep_narrative"] = brief.get("narrative") or ""
        if "dilution" in (brief.get("risk_flags") or []):
            row["dilution_flag"] = 1
        if "distress" in (brief.get("risk_flags") or []):
            row["distress_flag"] = 1
        if "guidance_cut" in (brief.get("risk_flags") or []):
            row["guidance_cut"] = True
        out[i] = row
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Deep catalyst lookback for one ticker")
    ap.add_argument("symbol")
    ap.add_argument("--as-of", default="", help="ET ISO datetime, e.g. 2026-09-02T09:15:00")
    ap.add_argument("--days", type=int, default=DEEP_LOOKBACK_DAYS)
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()
    as_of = None
    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            as_of = ET.localize(as_of)
    brief = research_deep_catalyst(
        args.symbol,
        as_of=as_of,
        lookback_days=args.days,
        use_ai=not args.no_ai,
    )
    print(json.dumps(brief, indent=2, default=str))
