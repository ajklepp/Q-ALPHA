"""
Live Peak Hour — Polygon news velocity + StockTwits + optional X sentiment.

Never blocks entry: failures → zeros + social_missing=1.
Canonical module for live scans; EXP-0021 research imports from here.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

POLYGON_BASE = "https://api.polygon.io"
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
X_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

BULL_LEX = re.compile(
    r"\b(moon|rocket|breakout|long|bullish|calls|runner|squeeze|buy|upside|ripping)\b",
    re.I,
)
BEAR_LEX = re.compile(
    r"\b(put|puts|short|bearish|dump|crash|dilution|offering|bankrupt|scam|sell)\b",
    re.I,
)

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SEC = 900.0


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL_SEC:
        return None
    return dict(val)


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), dict(val))


def _x_bearer() -> str:
    return (
        os.environ.get("X_BEARER_TOKEN")
        or os.environ.get("TWITTER_BEARER_TOKEN")
        or ""
    ).strip()


def fetch_polygon_news_velocity(
    symbol: str,
    *,
    api_key: str,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Headline counts in 24h / 72h windows ending at as_of (UTC). Causal if as_of=signal time."""
    sym = symbol.upper()
    end = as_of or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    cache_key = f"news:{sym}:{end.strftime('%Y%m%d%H')}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    start_72 = end - timedelta(hours=72)
    url = f"{POLYGON_BASE}/v2/reference/news"
    params = {
        "ticker": sym,
        "published_utc.gte": start_72.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "published_utc.lte": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 50,
        "order": "desc",
        "apiKey": api_key,
    }
    n24 = 0
    n72 = 0
    dilution = False
    distress = False
    unresolved = False
    catalyst_type = "none"
    try:
        resp = requests.get(url, params=params, timeout=20)
        time.sleep(0.12)
        if resp.status_code == 200:
            articles = resp.json().get("results") or []
            cut24 = end - timedelta(hours=24)
            for a in articles:
                pub = str(a.get("published_utc") or "")
                try:
                    pts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except Exception:
                    pts = end
                n72 += 1
                if pts >= cut24:
                    n24 += 1
                title = str(a.get("title") or "")
                blob = title.lower()
                if any(k in blob for k in ("offering", "dilution", "atm ", "registered direct")):
                    dilution = True
                    catalyst_type = "offering"
                if any(k in blob for k in ("going concern", "bankrupt", "chapter 11", "delist")):
                    distress = True
                if any(k in blob for k in ("pdufa", "fda", "adcom", "vote", "shareholder")):
                    unresolved = True
                    if "fda" in blob or "pdufa" in blob:
                        catalyst_type = "FDA"
                if "earn" in blob:
                    catalyst_type = catalyst_type if catalyst_type != "none" else "earnings"
                if "contract" in blob or "award" in blob:
                    catalyst_type = catalyst_type if catalyst_type != "none" else "contract"
    except Exception:
        pass

    out = {
        "news_velocity_24h": float(n24),
        "news_velocity_72h": float(n72),
        "news_headline_count_48h": float(n24 + max(n72 - n24, 0)),
        "dilution_flag": int(dilution),
        "distress_flag": int(distress),
        "unresolved": int(unresolved),
        "catalyst_type": catalyst_type,
    }
    _cache_set(cache_key, out)
    return out


def fetch_stocktwits(symbol: str) -> dict[str, Any]:
    """Public StockTwits symbol stream — message count + bull/bear ratio (best-effort)."""
    sym = symbol.upper()
    cache_key = f"st:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    out = {"st_msg_24h": 0.0, "st_bull_ratio": 0.5, "st_ok": 0}
    try:
        resp = requests.get(STOCKTWITS_URL.format(symbol=sym), timeout=15)
        time.sleep(0.05)
        if resp.status_code == 200:
            msgs = resp.json().get("messages") or []
            bull = bear = 0
            for m in msgs:
                sent = (m.get("entities") or {}).get("sentiment") or {}
                basic = str(sent.get("basic") or "").lower()
                if basic == "bullish":
                    bull += 1
                elif basic == "bearish":
                    bear += 1
            total = bull + bear
            out = {
                "st_msg_24h": float(len(msgs)),
                "st_bull_ratio": (bull / total) if total else 0.5,
                "st_ok": 1,
            }
    except Exception:
        pass
    _cache_set(cache_key, out)
    return out


def fetch_x_recent(symbol: str) -> dict[str, Any]:
    """
    X API v2 recent search if X_BEARER_TOKEN / TWITTER_BEARER_TOKEN set.
    Non-blocking: returns zeros + x_ok=0 when missing/down.
    """
    sym = symbol.upper()
    bearer = _x_bearer()
    base = {
        "x_posts_24h": 0.0,
        "x_authors_24h": 0.0,
        "x_engage_24h": 0.0,
        "x_sent_lex": 0.0,
        "x_ok": 0,
    }
    if not bearer:
        return base

    cache_key = f"x:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    query = f"(${sym} OR \"{sym} stock\") -is:retweet lang:en"
    headers = {"Authorization": f"Bearer {bearer}"}
    params = {
        "query": query,
        "max_results": 50,
        "tweet.fields": "public_metrics,author_id,created_at",
    }
    try:
        resp = requests.get(X_SEARCH_URL, headers=headers, params=params, timeout=20)
        time.sleep(0.2)
        if resp.status_code != 200:
            _cache_set(cache_key, base)
            return base
        data = resp.json().get("data") or []
        authors: set[str] = set()
        engage = 0
        bull = bear = 0
        for tw in data:
            authors.add(str(tw.get("author_id") or ""))
            m = tw.get("public_metrics") or {}
            engage += int(m.get("like_count") or 0)
            engage += int(m.get("retweet_count") or 0)
            engage += int(m.get("reply_count") or 0)
            text = str(tw.get("text") or "")
            if BULL_LEX.search(text):
                bull += 1
            if BEAR_LEX.search(text):
                bear += 1
        n = max(len(data), 1)
        sent = (bull - bear) / n
        out = {
            "x_posts_24h": float(len(data)),
            "x_authors_24h": float(len(authors)),
            "x_engage_24h": float(engage),
            "x_sent_lex": float(sent),
            "x_ok": 1,
        }
        _cache_set(cache_key, out)
        return out
    except Exception:
        _cache_set(cache_key, base)
        return base


def fetch_social_bundle(
    symbol: str,
    *,
    api_key: str,
    as_of: datetime | None = None,
    include_x: bool | None = None,
    include_st: bool = True,
    include_tws: bool = True,
    ib: Any | None = None,
) -> dict[str, Any]:
    """Combine Polygon + optional TWS news + StockTwits + optional X. Never raises.

    Note: StockTwits has no historical as_of — use include_st=False for
    backtests / corpus enrich to avoid look-ahead from the live stream.
    """
    if include_x is None:
        include_x = False
    out: dict[str, Any] = {
        "social_missing": 0,
        "guidance_cut": 0,
        "print": "unknown",
        "outlook": "unknown",
    }
    try:
        out.update(fetch_polygon_news_velocity(symbol, api_key=api_key, as_of=as_of))
    except Exception:
        out.update({
            "news_velocity_24h": 0.0,
            "news_velocity_72h": 0.0,
            "news_headline_count_48h": 0.0,
            "dilution_flag": 0,
            "distress_flag": 0,
            "unresolved": 0,
            "catalyst_type": "none",
        })

    if include_tws:
        try:
            from tsd_scan_pipeline.tsd_tws_news import fetch_tws_headlines

            tws = fetch_tws_headlines(symbol, ib=ib)
            out["tws_ok"] = int(tws.get("tws_ok") or 0)
            out["tws_headline_count"] = float(tws.get("tws_headline_count") or 0)
            out["tws_providers_hit"] = tws.get("tws_providers_hit") or []
            out["tws_headlines"] = tws.get("tws_headlines") or []
            if int(tws.get("dilution_flag") or 0):
                out["dilution_flag"] = 1
            if int(tws.get("distress_flag") or 0):
                out["distress_flag"] = 1
            tws_n = float(tws.get("tws_headline_count") or 0)
            if tws_n > 0:
                out["news_velocity_24h"] = float(out.get("news_velocity_24h") or 0) + min(tws_n, 5.0)
                out["news_velocity_72h"] = float(out.get("news_velocity_72h") or 0) + tws_n
                out["news_headline_count_48h"] = float(
                    out.get("news_headline_count_48h") or 0
                ) + tws_n
        except Exception:
            out.update({
                "tws_ok": 0,
                "tws_headline_count": 0.0,
                "tws_providers_hit": [],
                "tws_headlines": [],
            })
    else:
        out.update({
            "tws_ok": 0,
            "tws_headline_count": 0.0,
            "tws_providers_hit": [],
            "tws_headlines": [],
        })

    if include_st:
        try:
            out.update(fetch_stocktwits(symbol))
        except Exception:
            out.update({"st_msg_24h": 0.0, "st_bull_ratio": 0.5, "st_ok": 0})
    else:
        out.update({"st_msg_24h": 0.0, "st_bull_ratio": 0.5, "st_ok": 0})

    if include_x:
        try:
            out.update(fetch_x_recent(symbol))
        except Exception:
            out.update({
                "x_posts_24h": 0.0,
                "x_authors_24h": 0.0,
                "x_engage_24h": 0.0,
                "x_sent_lex": 0.0,
                "x_ok": 0,
            })
    else:
        out.update({
            "x_posts_24h": 0.0,
            "x_authors_24h": 0.0,
            "x_engage_24h": 0.0,
            "x_sent_lex": 0.0,
            "x_ok": 0,
        })

    st_ok = int(out.get("st_ok") or 0)
    x_ok = int(out.get("x_ok") or 0)
    tws_ok = int(out.get("tws_ok") or 0)
    if (
        st_ok == 0
        and x_ok == 0
        and tws_ok == 0
        and float(out.get("news_velocity_24h") or 0) == 0
    ):
        out["social_missing"] = 1
    return out


def attach_social_to_rows(
    rows: list[dict[str, Any]],
    *,
    api_key: str,
    as_of: datetime | None = None,
    include_x: bool | None = None,
    include_st: bool = True,
    include_tws: bool = True,
    ib: Any | None = None,
) -> list[dict[str, Any]]:
    """Mutate/return rows with social bundle fields (one fetch per symbol)."""
    cache: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            out.append(row)
            continue
        if sym not in cache:
            cache[sym] = fetch_social_bundle(
                sym,
                api_key=api_key,
                as_of=as_of,
                include_x=include_x,
                include_st=include_st,
                include_tws=include_tws,
                ib=ib,
            )
        merged = {**row, **cache[sym]}
        out.append(merged)
    return out
