"""
AI catalyst summarization for Q-Alpha morning scanner via OpenRouter.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def summarize_catalyst(ticker: str, headlines: list[str]) -> str:
    """
    Takes a list of news headlines for a ticker and returns
    a one-sentence AI summary of WHY the stock is gapping.

    Returns format: "EMOJI Type: reason"
    Examples:
      "📈 Earnings Beat: Q2 revenue +34% YoY, EPS beat by $0.18, guidance raised"
      "💊 FDA Approval: NDA approved for lead oncology drug"
      "🏛️ Contract Win: $45M government contract, 3x quarterly revenue"
      "🔀 No Catalyst: High short interest, possible technical squeeze"
      "📰 Analyst: Goldman Sachs upgrade to Buy, PT raised to $55"

    If no headlines: returns "❓ Unknown: No news found"
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
Start with one of these emojis based on catalyst type:
📈 for earnings/revenue beat
💊 for FDA/drug approval
🏛️ for government contract or deal
🤝 for partnership or acquisition
📰 for analyst upgrade
🔀 for short squeeze or technical (no fundamental news)
⚡ for product launch or major announcement
❓ for unclear catalyst

Reply with ONLY the emoji, catalyst type, colon, and reason.
Example: "📈 Earnings Beat: Q2 revenue surged 45%, guidance raised"
"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ajklepp/Q-ALPHA",
            },
            json={
                "model": "anthropic/claude-3-haiku",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 60,
                "temperature": 0.1,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        summary = result["choices"][0]["message"]["content"].strip()
        return summary

    except Exception as e:
        print(f"AI catalyst failed for {ticker}: {e}")
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
