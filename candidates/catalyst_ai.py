"""
AI catalyst summarization for Peak Hour via OpenRouter.

Default: cheap paid model (mistral-nemo) — free :free tiers are often
rate-limited / poor at structured tags. Override with OPENROUTER_MODEL.
Optional: OPENROUTER_TRY_FREE=1 tries free models first, then falls back.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default: ~$0.02 / $0.03 per 1M tokens — pennies per scan day.
DEFAULT_MODEL = "mistralai/mistral-nemo"
# Free attempts (flaky; only if OPENROUTER_TRY_FREE=1)
FREE_MODELS = (
    "inclusionai/ling-3.0-flash-fin:free",
    "google/gemma-4-31b-it:free",
    "openrouter/free",
)
# Paid fallbacks if primary fails
PAID_FALLBACKS = (
    "meta-llama/llama-3.1-8b-instruct",
    "amazon/nova-micro-v1",
)

_TAG_RE = re.compile(
    r"PRINT:\s*(beat|miss|inline|unknown).*OUTLOOK:\s*"
    r"(raised|maintained|lowered|withdrawn|unknown)",
    re.I,
)


def _model_chain() -> list[str]:
    """Ordered models to try. Env OPENROUTER_MODEL pins first choice."""
    pinned = (os.environ.get("OPENROUTER_MODEL") or "").strip()
    try_free = (os.environ.get("OPENROUTER_TRY_FREE") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    chain: list[str] = []
    if pinned:
        chain.append(pinned)
    if try_free:
        chain.extend(FREE_MODELS)
    if DEFAULT_MODEL not in chain:
        chain.append(DEFAULT_MODEL)
    for m in PAID_FALLBACKS:
        if m not in chain:
            chain.append(m)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _looks_valid(summary: str) -> bool:
    if not summary or len(summary) < 8:
        return False
    # Reject chain-of-thought dumps from free reasoning models
    low = summary.lower()
    if "thinking process" in low or "analyze user input" in low:
        return False
    if len(summary) > 280:
        return False
    return bool(_TAG_RE.search(summary)) or ("|" in summary and "print" in low)


def _call_openrouter(api_key: str, model: str, prompt: str) -> str:
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
            "max_tokens": 80,
            "temperature": 0.1,
        },
        timeout=20,
    )
    if response.status_code == 429:
        raise RuntimeError(f"rate_limited:{model}")
    response.raise_for_status()
    result = response.json()
    msg = (result.get("choices") or [{}])[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def summarize_catalyst(ticker: str, headlines: list[str]) -> str:
    """
    Takes a list of news headlines for a ticker and returns
    a one-sentence AI summary of WHY the stock is gapping.

    Returns format: "EMOJI Type: reason | PRINT: x | OUTLOOK: y"
    """
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return "❓ Unknown: OpenRouter key not configured"

    if not headlines:
        return "🔀 No Catalyst: No news found — possible technical move"

    headlines_text = "\n".join(f"- {h}" for h in headlines[:5])

    prompt = f"""You are a financial analyst. A stock ({ticker}) is gapping up significantly in pre-market trading.

Here are the news headlines from today:
{headlines_text}

In ONE short sentence (max 12 words), explain the PRIMARY catalyst.
Also append exactly these two tags at the end of the same line:
  PRINT: beat|miss|inline|unknown
  OUTLOOK: raised|maintained|lowered|withdrawn|unknown
Start with one of these emojis based on catalyst type:
📈 for earnings/revenue beat
💊 for FDA/drug approval
🏛️ for government contract or deal
🤝 for partnership or acquisition
📰 for analyst upgrade
🔀 for short squeeze or technical (no fundamental news)
⚡ for product launch or major announcement
❓ for unclear catalyst

Reply with ONLY: emoji Type: reason | PRINT: x | OUTLOOK: y
Example: "📈 Earnings Beat: Q2 revenue surged 45% | PRINT: beat | OUTLOOK: raised"
"""

    last_err: Exception | None = None
    for model in _model_chain():
        try:
            summary = _call_openrouter(api_key, model, prompt)
            if not _looks_valid(summary):
                print(f"  catalyst_ai skip bad shape model={model}: {summary[:80]!r}")
                continue
            # Normalize to one line
            summary = " ".join(summary.split())
            print(f"  catalyst_ai model={model}")
            return summary
        except Exception as e:
            last_err = e
            print(f"  catalyst_ai fail model={model}: {e}")
            continue

    print(f"AI catalyst failed for {ticker}: {last_err}")
    return f"📰 News: {headlines[0][:60]}..." if headlines else "❓ Unknown"


def get_ticker_headlines(ticker: str, api_key: str) -> list[str]:
    """
    Fetches today's news headlines for a ticker from Polygon.
    Returns list of headline strings.
    """
    from datetime import date

    today = date.today().isoformat()

    try:
        url = "https://api.polygon.io/v2/reference/news"
        params = {
            "ticker": ticker,
            "published_utc.gte": today,
            "limit": 5,
            "apiKey": api_key,
        }
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        headlines = []
        for article in data.get("results", []):
            title = article.get("title", "")
            description = article.get("description", "")
            if title:
                headlines.append(title)
            if description and len(headlines) < 3:
                headlines.append(description[:100])

        return headlines

    except Exception as e:
        print(f"News fetch failed for {ticker}: {e}")
        return []


if __name__ == "__main__":
    headlines = [
        "NEBX Reports Record Q2 Revenue, Raises Full Year Guidance",
        "Nebius Group beats earnings estimates by 34%",
    ]
    result = summarize_catalyst("NEBX", headlines)
    print(f"Result: {result}")
