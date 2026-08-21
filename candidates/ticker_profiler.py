"""
Q-ALPHA ticker_profiler — analog finder + MAE/MFE profile (read-only).

Commit 1: find historical "analog" gap days (gap>3% + unusual vol), weighted
by recency + gentle magnitude-similarity.

Commit 2: for each analog, pull Polygon 1-min RTH bars, measure MAE/MFE from
a ~9:33 ET entry proxy, aggregate weighted/unweighted percentiles, and DERIVE
informational tier stops + target. NO order wiring — research/measurement only.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ── Paths ──────────────────────────────────────────────────────────────────
CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
PROFILES_DIR = ROOT / "profiles"

POLYGON_BASE = "https://api.polygon.io"
ET = ZoneInfo("America/New_York")

# ── Tunable parameters (retune without rewriting logic) ─────────────────────
HISTORY_CALENDAR_DAYS = 365 * 2 + 30          # ~2 years (+ cushion for weekends)
VOLUME_BASELINE_DAYS = 5                      # trailing avg vol window (excl. day)
MIN_GAP_PCT = 0.03                            # >3% open gap vs prior close
VOL_MULT = 1.75                               # unusual-vol: day vol >= this × baseline
RECENCY_HALF_LIFE_DAYS = 180.0                # exp decay: weight halves every N days
# Similarity distance scales (gap in fraction, vol_ratio in multiples of baseline)
SIMILARITY_GAP_SCALE = 0.05                   # 5pp gap difference → unit distance
SIMILARITY_VOL_SCALE = 1.0                    # 1.0× vol-ratio difference → unit distance
# Magnitude/similarity tilt (GENTLE — big-gap days must stay informative samples):
#   0.0 = ignore magnitude entirely (pure recency)
#   1.0 = full floored-similarity tilt
# Default LOW so magnitude only gently tilts weights; never discards analogs.
SIMILARITY_STRENGTH = 0.3
# Floor on the similarity factor BEFORE strength blend: even a "far" analog
# keeps at least this fraction of the max similarity score (1.0). Prevents
# exp(-dist)-style near-zeroing of explosive gap days.
SIMILARITY_FLOOR = 0.25
MIN_ANALOGS_FOR_PROFILE = 3                   # below this → INSUFFICIENT_HISTORY

# ── Commit 2: MAE/MFE / tier derivation ─────────────────────────────────────
ENTRY_PROXY_MIN = 3                           # minutes after 9:30 ET → entry proxy
RTH_OPEN_HOUR, RTH_OPEN_MIN = 9, 30
RTH_CLOSE_HOUR, RTH_CLOSE_MIN = 16, 0
# "Just beyond" multiplier for safe-max / Tier4 (applied to the MAE percentile)
STOP_BEYOND_MULT = 1.05
MFE_HIT_BUCKETS = (0.03, 0.05, 0.08, 0.10)    # absolute % move hit-rate thresholds
POLYGON_SLEEP_SEC = 0.12                      # rate limit between minute-bar pulls


def similarity_factor(
    gap_pct: float,
    vol_ratio: float,
    ref_gap: float,
    ref_vol: float,
    *,
    strength: float = SIMILARITY_STRENGTH,
    floor: float = SIMILARITY_FLOOR,
) -> float:
    """
    Bounded magnitude-similarity multiplier in [floor_blend, 1].

    Soft distance → 1/(1+dist) in (0, 1], then floored at `floor`, then
    blended with strength:

      sim_soft  = 1 / (1 + dist)                    # mild, never near-zero alone
      sim_floor = floor + (1 - floor) * sim_soft    # >= floor (e.g. 0.25)
      factor    = (1 - strength) * 1.0 + strength * sim_floor

    strength=0 → always 1.0 (pure recency). strength=1 → floored soft tilt.
    """
    dist = (
        abs(gap_pct - ref_gap) / SIMILARITY_GAP_SCALE
        + abs(vol_ratio - ref_vol) / SIMILARITY_VOL_SCALE
    )
    sim_soft = 1.0 / (1.0 + dist)
    sim_floored = floor + (1.0 - floor) * sim_soft
    s = max(0.0, min(1.0, float(strength)))
    return (1.0 - s) * 1.0 + s * sim_floored


def _load_polygon_key() -> str:
    """Load POLYGON_API_KEY from env or repo .env. Never print the key."""
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key.strip()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found in environment or .env")


def _http_get_json(url: str, timeout: int = 60) -> dict:
    """GET JSON from Polygon; raises on HTTP errors."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_daily_bars(
    ticker: str,
    start: date,
    end: date,
    api_key: str | None = None,
) -> list[dict]:
    """
    Pull adjusted daily OHLCV bars from Polygon for [start, end].

    Endpoint: /v2/aggs/ticker/{T}/range/1/day/{from}/{to}
    Returns list of {date, open, high, low, close, volume} sorted ascending.
    """
    key = api_key or _load_polygon_key()
    sym = ticker.upper().strip()
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{urllib.parse.quote(sym)}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
        f"?adjusted=true&sort=asc&limit=50000"
        f"&apiKey={urllib.parse.quote(key)}"
    )
    data = _http_get_json(url)
    results = data.get("results") or []
    bars: list[dict] = []
    for r in results:
        ts_ms = int(r["t"])
        d = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date()
        bars.append({
            "date": d,
            "open": float(r["o"]),
            "high": float(r["h"]),
            "low": float(r["l"]),
            "close": float(r["c"]),
            "volume": float(r.get("v") or 0),
        })
    return bars


def _parse_as_of(as_of_date: date | str | None) -> date:
    """Normalize as_of_date to a date (default: today calendar date)."""
    if as_of_date is None:
        return date.today()
    if isinstance(as_of_date, date):
        return as_of_date
    return date.fromisoformat(str(as_of_date)[:10])


def find_analog_days(
    ticker: str,
    as_of_date: date | str | None = None,
    *,
    today_gap: float | None = None,
    today_vol_ratio: float | None = None,
    history_calendar_days: int = HISTORY_CALENDAR_DAYS,
    volume_baseline_days: int = VOLUME_BASELINE_DAYS,
    min_gap_pct: float = MIN_GAP_PCT,
    vol_mult: float = VOL_MULT,
    recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    similarity_strength: float = SIMILARITY_STRENGTH,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Find weighted analog gap+volume days for `ticker` as of `as_of_date`.

    WEIGHTING FORMULA
    -----------------
    For each analog i with age_days = (as_of_date - analog_date).days:

      recency_weight_i =
          0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
          # exponential half-life: weight halves every RECENCY_HALF_LIFE_DAYS

      ref_gap, ref_vol = (today_gap, today_vol_ratio) if both provided,
                         else (median gap_pct, median vol_ratio) of the analog set

      dist_i =
          |gap_pct_i - ref_gap| / SIMILARITY_GAP_SCALE
        + |vol_ratio_i - ref_vol| / SIMILARITY_VOL_SCALE

      # Soft + floored similarity (NOT harsh exp(-dist), which zeroed big gaps):
      sim_soft_i  = 1 / (1 + dist_i)
      sim_floor_i = SIMILARITY_FLOOR + (1 - SIMILARITY_FLOOR) * sim_soft_i
                    # every analog keeps >= SIMILARITY_FLOOR of max similarity
      similarity_weight_i =
          (1 - SIMILARITY_STRENGTH) * 1.0
        + SIMILARITY_STRENGTH * sim_floor_i
          # SIMILARITY_STRENGTH in [0,1]: 0 = pure recency; 1 = full floored tilt
          # Default LOW (0.3) — magnitude only gently tilts weights

      raw_i = recency_weight_i * similarity_weight_i
      combined_weight_i = raw_i / sum(raw)     # normalize to sum 1

    Every qualifying analog (gap>3% + unusual vol) remains a valid sample —
    none are effectively discarded by magnitude distance.

    Returns
    -------
    {
      "ticker": str,
      "as_of_date": "YYYY-MM-DD",
      "flag": None | "INSUFFICIENT_HISTORY",
      "stats": {...},
      "analogs": [
          {date, gap_pct, vol_ratio, recency_weight, similarity_weight,
           combined_weight}, ...
      ],  # sorted by date ascending; combined_weight sums to ~1 when non-empty
    }
    """
    as_of = _parse_as_of(as_of_date)
    start = as_of - timedelta(days=history_calendar_days)
    bars = fetch_daily_bars(ticker, start, as_of, api_key=api_key)

    # Keep bars through as_of; analogs are STRICTLY before as_of.
    bars = [b for b in bars if b["date"] <= as_of]

    trading_days = len(bars)
    gap_gt_min = 0
    candidates: list[dict] = []

    for i in range(1, len(bars)):
        prev = bars[i - 1]
        cur = bars[i]
        if cur["date"] >= as_of:
            continue
        prev_close = prev["close"]
        if prev_close <= 0:
            continue
        gap_pct = (cur["open"] - prev_close) / prev_close

        # Trailing avg volume: prior volume_baseline_days sessions ONLY
        # (exclude current day — no look-ahead into the analog day's volume).
        if i < volume_baseline_days:
            continue
        baseline_vols = [bars[j]["volume"] for j in range(i - volume_baseline_days, i)]
        avg_vol = sum(baseline_vols) / volume_baseline_days
        if avg_vol <= 0:
            continue

        vol_ratio = cur["volume"] / avg_vol

        if gap_pct > min_gap_pct:
            gap_gt_min += 1
            if cur["volume"] >= vol_mult * avg_vol:
                age_days = (as_of - cur["date"]).days
                candidates.append({
                    "date": cur["date"].isoformat(),
                    "gap_pct": round(gap_pct, 6),
                    "vol_ratio": round(vol_ratio, 4),
                    "age_days": age_days,
                    "volume": int(cur["volume"]),
                    "avg_vol_baseline": int(avg_vol),
                    "open": cur["open"],
                    "prev_close": prev_close,
                })

    n_analogs = len(candidates)
    flag = "INSUFFICIENT_HISTORY" if n_analogs < MIN_ANALOGS_FOR_PROFILE else None

    if n_analogs == 0:
        return {
            "ticker": ticker.upper(),
            "as_of_date": as_of.isoformat(),
            "flag": flag or "INSUFFICIENT_HISTORY",
            "stats": {
                "trading_days_scanned": trading_days,
                "gap_gt_min_days": gap_gt_min,
                "analog_days": 0,
                "min_gap_pct": min_gap_pct,
                "vol_mult": vol_mult,
                "volume_baseline_days": volume_baseline_days,
                "recency_half_life_days": recency_half_life_days,
                "history_calendar_days": history_calendar_days,
            },
            "analogs": [],
            "weight_sum": 0.0,
        }

    gaps = [c["gap_pct"] for c in candidates]
    vols = [c["vol_ratio"] for c in candidates]
    gaps_sorted = sorted(gaps)
    vols_sorted = sorted(vols)
    mid = n_analogs // 2
    if n_analogs % 2:
        med_gap = gaps_sorted[mid]
        med_vol = vols_sorted[mid]
    else:
        med_gap = (gaps_sorted[mid - 1] + gaps_sorted[mid]) / 2.0
        med_vol = (vols_sorted[mid - 1] + vols_sorted[mid]) / 2.0

    if today_gap is not None and today_vol_ratio is not None:
        ref_gap = float(today_gap)
        ref_vol = float(today_vol_ratio)
        ref_source = "today_fingerprint"
    else:
        ref_gap = med_gap
        ref_vol = med_vol
        ref_source = "median_analog"

    raw_sum = 0.0
    for c in candidates:
        rec = 0.5 ** (c["age_days"] / recency_half_life_days)
        sim = similarity_factor(
            c["gap_pct"],
            c["vol_ratio"],
            ref_gap,
            ref_vol,
            strength=similarity_strength,
        )
        c["recency_weight"] = rec
        c["similarity_weight"] = sim
        c["_raw"] = rec * sim
        raw_sum += c["_raw"]

    analogs: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["date"]):
        combined = (c["_raw"] / raw_sum) if raw_sum > 0 else 0.0
        analogs.append({
            "date": c["date"],
            "gap_pct": c["gap_pct"],
            "vol_ratio": c["vol_ratio"],
            "recency_weight": round(c["recency_weight"], 6),
            "similarity_weight": round(c["similarity_weight"], 6),
            "combined_weight": round(combined, 6),
        })

    weight_sum = sum(a["combined_weight"] for a in analogs)

    return {
        "ticker": ticker.upper(),
        "as_of_date": as_of.isoformat(),
        "flag": flag,
        "ref_source": ref_source,
        "ref_gap": round(ref_gap, 6),
        "ref_vol_ratio": round(ref_vol, 4),
        "stats": {
            "trading_days_scanned": trading_days,
            "gap_gt_min_days": gap_gt_min,
            "analog_days": n_analogs,
            "min_gap_pct": min_gap_pct,
            "vol_mult": vol_mult,
            "volume_baseline_days": volume_baseline_days,
            "recency_half_life_days": recency_half_life_days,
            "history_calendar_days": history_calendar_days,
            "similarity_strength": similarity_strength,
            "similarity_floor": SIMILARITY_FLOOR,
        },
        "analogs": analogs,
        "weight_sum": round(weight_sum, 6),
    }


def save_analogs_json(result: dict, path: Path | None = None) -> Path:
    """Write finder result to profiles/{TICKER}_analogs.json."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out = path or (PROFILES_DIR / f"{result['ticker']}_analogs.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return out


# =============================================================================
# Commit 2 — MAE/MFE extractor + informational tier derivation
# =============================================================================

def fetch_minute_bars(
    ticker: str,
    day: date,
    api_key: str | None = None,
) -> list[dict]:
    """
    Pull 1-minute OHLCV bars for one calendar day from Polygon.

    Endpoint: /v2/aggs/ticker/{T}/range/1/minute/{day}/{day}
    Returns bars with timezone-aware ET timestamps, sorted ascending.
    Caller should rate-limit (POLYGON_SLEEP_SEC) between days.
    """
    key = api_key or _load_polygon_key()
    sym = ticker.upper().strip()
    day_s = day.isoformat()
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{urllib.parse.quote(sym)}/range/1/minute/"
        f"{day_s}/{day_s}"
        f"?adjusted=true&sort=asc&limit=50000"
        f"&apiKey={urllib.parse.quote(key)}"
    )
    data = _http_get_json(url)
    bars: list[dict] = []
    for r in data.get("results") or []:
        ts_ms = int(r["t"])
        dt_et = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(ET)
        bars.append({
            "time": dt_et,
            "open": float(r["o"]),
            "high": float(r["h"]),
            "low": float(r["l"]),
            "close": float(r["c"]),
            "volume": float(r.get("v") or 0),
        })
    return bars


def _rth_window(day: date) -> tuple[datetime, datetime]:
    """Regular-session [open, close) bounds in ET for `day`."""
    open_dt = datetime(
        day.year, day.month, day.day, RTH_OPEN_HOUR, RTH_OPEN_MIN, tzinfo=ET,
    )
    close_dt = datetime(
        day.year, day.month, day.day, RTH_CLOSE_HOUR, RTH_CLOSE_MIN, tzinfo=ET,
    )
    return open_dt, close_dt


def filter_rth_bars(bars: list[dict], day: date) -> list[dict]:
    """Keep only 9:30–16:00 ET bars for the session."""
    open_dt, close_dt = _rth_window(day)
    return [b for b in bars if open_dt <= b["time"] < close_dt]


def entry_proxy_bar(
    rth_bars: list[dict],
    day: date,
    entry_proxy_min: int = ENTRY_PROXY_MIN,
) -> dict | None:
    """
    First RTH bar at or after 9:30 + entry_proxy_min (default 9:33 ET).

    Matches the live agent's early-session entry window. Returns None if the
    minute feed has a hole past that time with no later bar (caller skips day).
    """
    open_dt, _close = _rth_window(day)
    proxy_dt = open_dt + timedelta(minutes=entry_proxy_min)
    for b in rth_bars:
        if b["time"] >= proxy_dt:
            return b
    return None


def measure_day_excursions(
    rth_bars: list[dict],
    day: date,
    entry_proxy_min: int = ENTRY_PROXY_MIN,
) -> dict | None:
    """
    MAE/MFE from the entry proxy through the RTH close.

    Definitions (fractions of entry_proxy_price):
      MAE = max(0, (entry - min_low_after) / entry)   # deepest pullback depth
      MFE = max(0, (max_high_after - entry) / entry)  # highest run height

    Also: time_of_mfe_peak (ET ISO), held (session close > entry), close_vs_entry_pct.
    Returns None if entry proxy or post-entry path is missing.
    """
    proxy = entry_proxy_bar(rth_bars, day, entry_proxy_min=entry_proxy_min)
    if proxy is None:
        return None
    entry = float(proxy["open"])  # open of the proxy minute ≈ fill at that clock
    if entry <= 0:
        return None

    path = [b for b in rth_bars if b["time"] >= proxy["time"]]
    if not path:
        return None

    min_low = min(b["low"] for b in path)
    max_high = max(b["high"] for b in path)
    mae = max(0.0, (entry - min_low) / entry)
    mfe = max(0.0, (max_high - entry) / entry)

    mfe_bar = max(path, key=lambda b: b["high"])
    session_close = float(path[-1]["close"])
    held = session_close > entry
    close_vs_entry = (session_close - entry) / entry

    return {
        "entry_proxy_price": round(entry, 4),
        "entry_proxy_time": proxy["time"].isoformat(),
        "mae_pct": round(mae, 6),          # fraction, e.g. 0.042 = 4.2%
        "mfe_pct": round(mfe, 6),
        "mae_pct_display": round(mae * 100, 3),
        "mfe_pct_display": round(mfe * 100, 3),
        "time_of_mfe_peak": mfe_bar["time"].isoformat(),
        "held": held,
        "close_vs_entry_pct": round(close_vs_entry, 6),
        "session_close": round(session_close, 4),
        "min_low": round(min_low, 4),
        "max_high": round(max_high, 4),
    }


def weighted_percentile(
    values: list[float],
    weights: list[float],
    pct: float,
) -> float | None:
    """
    Weighted percentile (pct in 0..100).

    Sort by value ascending; walk cumulative weight until cum >= pct/100 * total.
    Equal weights → ordinary empirical percentile.
    """
    if not values or not weights or len(values) != len(weights):
        return None
    total = sum(weights)
    if total <= 0:
        return None
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    target = (pct / 100.0) * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return float(v)
    return float(pairs[-1][0])


def unweighted_percentile(values: list[float], pct: float) -> float | None:
    """Equal-weight percentile via the same weighted engine."""
    if not values:
        return None
    n = len(values)
    return weighted_percentile(values, [1.0] * n, pct)


def confidence_label(n_analogs: int) -> str:
    """Map analog count to profile confidence band."""
    if n_analogs >= 10:
        return "HIGH"
    if n_analogs >= 5:
        return "MEDIUM"
    if n_analogs >= 3:
        return "LOW"
    return "INSUFFICIENT"


def derive_bracket(mae_w: dict, mfe_w: dict) -> dict:
    """
    Informational tier stops + target from weighted MAE/MFE percentiles.

    Tier1 ~ MAE p50 (tight), Tier2 ~ p75, Tier3 ~ p90,
    Tier4 ~ just beyond p90 (STOP_BEYOND_MULT).
    SAFE MAX STOP = just beyond MAE p75.
    TARGET = median (p50) MFE.
    All reported as positive % below (stops) or above (target) entry.
    """
    p50 = mae_w["p50"]
    p75 = mae_w["p75"]
    p90 = mae_w["p90"]
    safe = p75 * STOP_BEYOND_MULT
    tier4 = p90 * STOP_BEYOND_MULT
    target = mfe_w["p50"]
    return {
        "safe_max_stop_pct": round(safe, 6),
        "safe_max_stop_pct_display": round(safe * 100, 3),
        "tiers": {
            "tier1_pct": round(p50, 6),
            "tier1_pct_display": round(p50 * 100, 3),
            "tier2_pct": round(p75, 6),
            "tier2_pct_display": round(p75 * 100, 3),
            "tier3_pct": round(p90, 6),
            "tier3_pct_display": round(p90 * 100, 3),
            "tier4_pct": round(tier4, 6),
            "tier4_pct_display": round(tier4 * 100, 3),
        },
        "target_pct": round(target, 6),
        "target_pct_display": round(target * 100, 3),
        "stop_beyond_mult": STOP_BEYOND_MULT,
        "note": (
            "INFORMATIONAL ONLY — not wired into order logic. "
            "Stops are % below entry; target is % above entry."
        ),
    }


def build_ticker_profile(
    ticker: str,
    as_of_date: date | str | None = None,
    *,
    entry_proxy_min: int = ENTRY_PROXY_MIN,
    analog_result: dict | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Full Commit-2 profile: analogs → per-day MAE/MFE → percentiles → tiers.

    Read-only. Does not place orders or mutate pool state.
    """
    key = api_key or _load_polygon_key()
    analogs_pack = analog_result or find_analog_days(
        ticker, as_of_date, api_key=key,
    )
    ticker_u = analogs_pack["ticker"]
    as_of = analogs_pack["as_of_date"]
    analogs = analogs_pack.get("analogs") or []

    per_day: list[dict] = []
    skipped: list[dict] = []

    for i, a in enumerate(analogs):
        day = date.fromisoformat(a["date"])
        try:
            raw = fetch_minute_bars(ticker_u, day, api_key=key)
            rth = filter_rth_bars(raw, day)
            exc = measure_day_excursions(
                rth, day, entry_proxy_min=entry_proxy_min,
            )
        except Exception as exc_err:
            skipped.append({"date": a["date"], "reason": str(exc_err)})
            time.sleep(POLYGON_SLEEP_SEC)
            continue
        time.sleep(POLYGON_SLEEP_SEC)

        if exc is None:
            skipped.append({
                "date": a["date"],
                "reason": "missing entry proxy or empty RTH path",
            })
            continue

        row = {
            "date": a["date"],
            "gap_pct": a["gap_pct"],
            "vol_ratio": a["vol_ratio"],
            "combined_weight": a["combined_weight"],
            "n_rth_bars": len(rth),
            **exc,
        }
        per_day.append(row)
        print(
            f"  [{i + 1}/{len(analogs)}] {a['date']} "
            f"entry=${exc['entry_proxy_price']:.2f} "
            f"MAE={exc['mae_pct_display']:.2f}% "
            f"MFE={exc['mfe_pct_display']:.2f}% "
            f"held={exc['held']}"
        )

    n = len(per_day)
    conf = confidence_label(n)

    if n == 0:
        profile = {
            "ticker": ticker_u,
            "as_of_date": as_of,
            "informational_only": True,
            "confidence": conf,
            "flag": "INSUFFICIENT_HISTORY",
            "analog_finder": {
                "flag": analogs_pack.get("flag"),
                "stats": analogs_pack.get("stats"),
                "weight_sum": analogs_pack.get("weight_sum"),
            },
            "entry_proxy_min": entry_proxy_min,
            "per_analog": [],
            "skipped": skipped,
            "percentiles": {},
            "hit_rates": {},
            "bracket": {},
            "sanity": {"ok": False, "checks": ["no measurable analog days"]},
        }
        return profile

    maes = [r["mae_pct"] for r in per_day]
    mfes = [r["mfe_pct"] for r in per_day]
    # Re-normalize weights over successfully measured days only
    w_raw = [r["combined_weight"] for r in per_day]
    w_sum = sum(w_raw) or 1.0
    weights = [w / w_sum for w in w_raw]
    for r, w in zip(per_day, weights):
        r["weight_renorm"] = round(w, 6)

    def _pct_block(vals: list[float], wts: list[float]) -> dict:
        return {
            "p50": weighted_percentile(vals, wts, 50),
            "p75": weighted_percentile(vals, wts, 75),
            "p90": weighted_percentile(vals, wts, 90),
        }

    mae_w = _pct_block(maes, weights)
    mae_u = {
        "p50": unweighted_percentile(maes, 50),
        "p75": unweighted_percentile(maes, 75),
        "p90": unweighted_percentile(maes, 90),
    }
    mfe_w = _pct_block(mfes, weights)
    mfe_u = {
        "p50": unweighted_percentile(mfes, 50),
        "p75": unweighted_percentile(mfes, 75),
        "p90": unweighted_percentile(mfes, 90),
    }

    # Round percentile dicts for JSON cleanliness
    def _round_pct(d: dict) -> dict:
        return {
            k: (None if v is None else round(float(v), 6))
            for k, v in d.items()
        }

    mae_w, mae_u = _round_pct(mae_w), _round_pct(mae_u)
    mfe_w, mfe_u = _round_pct(mfe_w), _round_pct(mfe_u)

    hit_rates: dict[str, dict] = {}
    for thr in MFE_HIT_BUCKETS:
        flags = [1.0 if m >= thr else 0.0 for m in mfes]
        hit_u = sum(flags) / n
        hit_w = sum(f * w for f, w in zip(flags, weights))
        key = f"mfe_ge_{int(thr * 100)}pct"
        hit_rates[key] = {
            "threshold_pct": thr,
            "unweighted": round(hit_u, 4),
            "weighted": round(hit_w, 4),
        }

    bracket = derive_bracket(mae_w, mfe_w)

    # Sanity checks
    checks: list[str] = []
    ok = True
    if not (mae_w["p50"] <= mae_w["p75"] <= mae_w["p90"]):
        ok = False
        checks.append("FAIL: weighted MAE percentiles not non-decreasing")
    else:
        checks.append("OK: MAE p50 <= p75 <= p90 (weighted)")
    t = bracket["tiers"]
    if not (t["tier1_pct"] <= t["tier2_pct"] <= t["tier3_pct"] <= t["tier4_pct"]):
        ok = False
        checks.append("FAIL: tier stops not ordered")
    else:
        checks.append("OK: tier1 <= tier2 <= tier3 <= tier4")
    if bracket["target_pct"] <= 0:
        ok = False
        checks.append("FAIL: target <= 0")
    else:
        checks.append("OK: target > 0")

    profile = {
        "ticker": ticker_u,
        "as_of_date": as_of,
        "informational_only": True,
        "confidence": conf,
        "flag": analogs_pack.get("flag") if conf == "INSUFFICIENT" else None,
        "n_analogs_finder": len(analogs),
        "n_analogs_measured": n,
        "entry_proxy_min": entry_proxy_min,
        "analog_finder": {
            "flag": analogs_pack.get("flag"),
            "stats": analogs_pack.get("stats"),
            "weight_sum": analogs_pack.get("weight_sum"),
            "ref_source": analogs_pack.get("ref_source"),
        },
        "per_analog": per_day,
        "skipped": skipped,
        "percentiles": {
            "mae": {"weighted": mae_w, "unweighted": mae_u},
            "mfe": {"weighted": mfe_w, "unweighted": mfe_u},
        },
        "hit_rates": hit_rates,
        "bracket": bracket,
        "sanity": {"ok": ok, "checks": checks},
    }
    return profile


def save_profile_json(profile: dict, path: Path | None = None) -> Path:
    """Write profile to profiles/{TICKER}_profile.json."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out = path or (PROFILES_DIR / f"{profile['ticker']}_profile.json")
    out.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return out


def _print_report(result: dict) -> None:
    """Human-readable proof output for a find_analog_days result."""
    stats = result["stats"]
    print(f"\n{'=' * 60}")
    print(f"ANALOG FINDER — {result['ticker']}  as_of={result['as_of_date']}")
    print(f"{'=' * 60}")
    print(f"Trading days scanned:     {stats['trading_days_scanned']}")
    print(f"Gap > {stats['min_gap_pct']:.0%} days:         {stats['gap_gt_min_days']}")
    print(
        f"Analog days (gap+vol≥{stats['vol_mult']}x): "
        f"{stats['analog_days']}"
    )
    if result.get("flag"):
        print(f"FLAG: {result['flag']}")
    else:
        print("FLAG: (none — enough analogs for a profile)")
    print(
        f"Ref fingerprint: {result.get('ref_source')} "
        f"gap={result.get('ref_gap')} vol_ratio={result.get('ref_vol_ratio')}"
    )
    print(f"\n{'date':<12} {'gap%':>8} {'vol_r':>8} {'w_comb':>10}")
    print("-" * 42)
    for a in result["analogs"]:
        print(
            f"{a['date']:<12} {a['gap_pct'] * 100:7.2f}% "
            f"{a['vol_ratio']:8.2f} {a['combined_weight']:10.6f}"
        )
    print("-" * 42)
    print(f"{'weight sum':<12} {'':>8} {'':>8} {result['weight_sum']:10.6f}")
    print(f"Analogs found: {stats['analog_days']}  (need ≥{MIN_ANALOGS_FOR_PROFILE})")


def _print_profile(profile: dict) -> None:
    """Human-readable Commit-2 proof output."""
    print(f"\n{'=' * 72}")
    print(
        f"MAE/MFE PROFILE — {profile['ticker']}  as_of={profile['as_of_date']}  "
        f"confidence={profile['confidence']}"
    )
    print(f"{'=' * 72}")
    print(
        f"Analogs: finder={profile.get('n_analogs_finder')}  "
        f"measured={profile.get('n_analogs_measured')}  "
        f"entry_proxy=9:{30 + profile.get('entry_proxy_min', ENTRY_PROXY_MIN):02d} ET"
    )
    print("INFORMATIONAL ONLY — not wired into orders.\n")

    print(
        f"{'date':<12} {'entry':>8} {'MAE%':>8} {'MFE%':>8} "
        f"{'held':>5} {'weight':>8}"
    )
    print("-" * 56)
    for r in profile.get("per_analog") or []:
        print(
            f"{r['date']:<12} {r['entry_proxy_price']:8.2f} "
            f"{r['mae_pct'] * 100:7.2f}% {r['mfe_pct'] * 100:7.2f}% "
            f"{'Y' if r['held'] else 'N':>5} "
            f"{r.get('weight_renorm', r['combined_weight']):8.4f}"
        )
    print("-" * 56)

    pct = profile.get("percentiles") or {}
    mae = pct.get("mae") or {}
    mfe = pct.get("mfe") or {}
    print("\nPercentiles (fraction of entry):")
    print(f"  {'':12} {'MAE w':>10} {'MAE u':>10} {'MFE w':>10} {'MFE u':>10}")
    for key in ("p50", "p75", "p90"):
        mw = (mae.get("weighted") or {}).get(key)
        mu = (mae.get("unweighted") or {}).get(key)
        fw = (mfe.get("weighted") or {}).get(key)
        fu = (mfe.get("unweighted") or {}).get(key)
        print(
            f"  {key:<12} "
            f"{(mw or 0) * 100:9.2f}% {(mu or 0) * 100:9.2f}% "
            f"{(fw or 0) * 100:9.2f}% {(fu or 0) * 100:9.2f}%"
        )

    print("\nMFE hit-rates:")
    for _k, h in (profile.get("hit_rates") or {}).items():
        thr = h["threshold_pct"] * 100
        print(
            f"  MFE ≥ +{thr:.0f}%:  "
            f"weighted={h['weighted'] * 100:.1f}%  "
            f"unweighted={h['unweighted'] * 100:.1f}%"
        )

    b = profile.get("bracket") or {}
    t = b.get("tiers") or {}
    print("\nDerived bracket (informational):")
    print(
        f"  SAFE MAX STOP:  {b.get('safe_max_stop_pct_display')}% below entry "
        f"(just beyond MAE p75)"
    )
    print(f"  Tier1 stop:     {t.get('tier1_pct_display')}%  (≈ MAE p50)")
    print(f"  Tier2 stop:     {t.get('tier2_pct_display')}%  (≈ MAE p75)")
    print(f"  Tier3 stop:     {t.get('tier3_pct_display')}%  (≈ MAE p90)")
    print(f"  Tier4 stop:     {t.get('tier4_pct_display')}%  (just beyond p90)")
    print(f"  TARGET:         +{b.get('target_pct_display')}%  (≈ MFE p50)")
    print(f"  Confidence:     {profile.get('confidence')}")

    san = profile.get("sanity") or {}
    print("\nSanity:")
    for c in san.get("checks") or []:
        print(f"  {c}")
    print(f"  overall={'PASS' if san.get('ok') else 'FAIL'}")


if __name__ == "__main__":
    import sys

    sym = (sys.argv[1] if len(sys.argv) > 1 else "JOBY").upper()
    mode = (sys.argv[2] if len(sys.argv) > 2 else "profile").lower()

    if mode in ("analogs", "finder", "commit1"):
        print(f"Running find_analog_days({sym!r}) ...")
        result = find_analog_days(sym)
        _print_report(result)
        out = save_analogs_json(result)
        print(f"\nWrote {out.relative_to(ROOT)}")
    else:
        print(f"Running build_ticker_profile({sym!r}) ...")
        print("(Polygon 1-min pulls per analog — may take ~30s)\n")
        result = find_analog_days(sym)
        _print_report(result)
        save_analogs_json(result)
        profile = build_ticker_profile(sym, analog_result=result)
        _print_profile(profile)
        out = save_profile_json(profile)
        print(f"\nWrote {out.relative_to(ROOT)}")
