"""
Q-ALPHA ticker_profiler — analog finder + MAE/MFE profile (read-only).

Commit 1: find historical "analog" gap days (gap>3% + unusual vol) inside a
2-year lookback. The 2-year window IS the recency filter — analogs are
EQUAL-WEIGHTED by default (optional gentle similarity tilt is off).

Commit 2: for each analog, pull Polygon 1-min RTH bars, measure MAE/MFE from
a ~9:33 ET entry proxy, aggregate equal-weight percentiles, derive
informational tier stops + target, plus win-rate / conditional winner-MFE /
failure-MAE / R:R warning. NO order wiring — research/measurement only.
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
# Lookback: default ~2yr (current regime). If too few analogs, step-extend
# up to a hard 3yr cap — never 5yr (avoids deep COVID/meme-era regime mix).
HISTORY_DEFAULT_CALENDAR_DAYS = 365 * 2 + 30  # start here (~2.1yr)
HISTORY_MAX_CALENDAR_DAYS = 365 * 3 + 30      # hard cap (~3.1yr)
HISTORY_CALENDAR_DAYS = HISTORY_DEFAULT_CALENDAR_DAYS  # alias for callers
LOOKBACK_EXTEND_STEP_DAYS = 90                # grow window in ~quarter-year steps
MIN_ANALOGS_TARGET = 8                        # extend beyond 2yr if below this
# Reliable window ≈ 1.75 years of calendar history in the sample
RELIABLE_LOOKBACK_DAYS = int(1.75 * 365)      # ~639 calendar days
VOLUME_BASELINE_DAYS = 5                      # trailing avg vol window (excl. day)
MIN_GAP_PCT = 0.03                            # >3% open gap vs prior close
VOL_MULT = 1.75                               # unusual-vol: day vol >= this × baseline
# Similarity distance scales (gap in fraction, vol_ratio in multiples of baseline)
SIMILARITY_GAP_SCALE = 0.05                   # 5pp gap difference → unit distance
SIMILARITY_VOL_SCALE = 1.0                    # 1.0× vol-ratio difference → unit distance
# Optional magnitude-similarity tilt (OFF by default → equal weights).
SIMILARITY_STRENGTH = 0.0
SIMILARITY_FLOOR = 0.25
# Analog-count thresholds for history_flag / confidence
MIN_ANALOGS_RELIABLE = 10                     # + long lookback → "" / HIGH
MIN_ANALOGS_USABLE = 3                        # below → "**" / INSUFFICIENT
MIN_ANALOGS_FOR_PROFILE = MIN_ANALOGS_USABLE  # back-compat alias
# Analogs must be strictly before as_of AND at least this many calendar days
# older. age=1 (yesterday) is excluded so today's setup never treats
# yesterday's same-name gap day as "history" (USDE 2026-08-20 bug).
MIN_ANALOG_AGE_DAYS = 2

# ── Commit 2: MAE/MFE / tier derivation ─────────────────────────────────────
ENTRY_PROXY_MIN = 3                           # minutes after 9:30 ET → entry proxy
RTH_OPEN_HOUR, RTH_OPEN_MIN = 9, 30
RTH_CLOSE_HOUR, RTH_CLOSE_MIN = 16, 0
# "Just beyond" multiplier for safe-max / Tier4 (applied to the MAE percentile)
STOP_BEYOND_MULT = 1.05
MFE_HIT_BUCKETS = (0.03, 0.05, 0.08, 0.10)    # absolute % move hit-rate thresholds
POLYGON_SLEEP_SEC = 0.12                      # rate limit between minute-bar pulls
# Warn when informational target / safe-max-stop reward:risk is thin
RR_WARN_THRESHOLD = 1.5


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
    Optional magnitude-similarity multiplier. Default strength=0 → always 1.0
    (equal weight). When enabled, soft+floored tilt — never near-zeros big gaps.

      sim_soft  = 1 / (1 + dist)
      sim_floor = floor + (1 - floor) * sim_soft
      factor    = (1 - strength) * 1.0 + strength * sim_floor
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
    """Normalize as_of_date to a date (default: today in America/New_York)."""
    if as_of_date is None:
        return datetime.now(ET).date()
    if isinstance(as_of_date, date):
        return as_of_date
    return date.fromisoformat(str(as_of_date)[:10])


def classify_history(
    actual_lookback_days: int,
    analog_count: int,
    *,
    lookback_extended: bool = False,
) -> tuple[str, str, str | None]:
    """
    Return (history_flag, confidence, lookback_note).

      ""   reliable: within default ~2yr, lookback >= ~1.75yr, analogs >= 10 → HIGH
      "*"  limited:  young ticker, small sample (3-9), OR lookback extended
                     past 2yr to reach MIN_ANALOGS_TARGET (older-regime note)
      "**" insufficient: analog_count < 3 → INSUFFICIENT (informational only)

    Profiles are ALWAYS built when possible; flags communicate certainty.
    """
    if analog_count < MIN_ANALOGS_USABLE:
        return "**", "INSUFFICIENT", None

    if lookback_extended:
        yrs = round(actual_lookback_days / 365.25, 2)
        note = (
            f"used {yrs}yr to reach {analog_count} analogs - "
            f"includes older-regime data"
        )
        # Never HIGH when leaning on data beyond the 2yr default window
        if analog_count >= 5:
            return "*", "MEDIUM", note
        return "*", "LOW", note

    young = actual_lookback_days < RELIABLE_LOOKBACK_DAYS
    small = analog_count < MIN_ANALOGS_RELIABLE
    if young or small:
        if analog_count >= 5:
            return "*", "MEDIUM", None
        return "*", "LOW", None
    return "", "HIGH", None


def _scan_gap_vol_candidates(
    bars: list[dict],
    as_of: date,
    *,
    volume_baseline_days: int,
    min_gap_pct: float,
    vol_mult: float,
) -> tuple[list[dict], int]:
    """
    Scan daily bars for gap+unusual-vol analog candidates.

    Returns (candidates, gap_gt_min_count). Candidates never include
    as_of / future / age < MIN_ANALOG_AGE_DAYS.
    """
    gap_gt_min = 0
    candidates: list[dict] = []
    for i in range(1, len(bars)):
        prev = bars[i - 1]
        cur = bars[i]
        if cur["date"] >= as_of:
            continue
        age_days = (as_of - cur["date"]).days
        if age_days < MIN_ANALOG_AGE_DAYS:
            continue
        prev_close = prev["close"]
        if prev_close <= 0:
            continue
        gap_pct = (cur["open"] - prev_close) / prev_close

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
    return candidates, gap_gt_min


def _choose_lookback_window(
    all_candidates: list[dict],
    as_of: date,
    bars: list[dict],
) -> dict[str, Any]:
    """
    Default 2yr window; step-extend to 3yr cap only if analogs < MIN_ANALOGS_TARGET.

    Returns window metadata + filtered candidate list for the chosen window.
    """
    default_days = HISTORY_DEFAULT_CALENDAR_DAYS
    max_days = HISTORY_MAX_CALENDAR_DAYS

    def _in_window(cands: list[dict], window: int) -> list[dict]:
        return [c for c in cands if int(c["age_days"]) <= window]

    n_at_2yr = len(_in_window(all_candidates, default_days))
    n_at_3yr = len(_in_window(all_candidates, max_days))

    window_days = default_days
    if n_at_2yr < MIN_ANALOGS_TARGET:
        while (
            len(_in_window(all_candidates, window_days)) < MIN_ANALOGS_TARGET
            and window_days < max_days
        ):
            window_days = min(window_days + LOOKBACK_EXTEND_STEP_DAYS, max_days)

    chosen = _in_window(all_candidates, window_days)
    # Only call it "extended" if we actually kept analogs older than the
    # 2yr default. Young names with <2yr of data must not get a false flag.
    has_older = any(int(c["age_days"]) > default_days for c in chosen)
    if not has_older:
        window_days = default_days
        chosen = _in_window(all_candidates, window_days)
        lookback_extended = False
    else:
        lookback_extended = True

    # actual lookback = span of bars available inside the chosen window
    bars_in_window = [
        b for b in bars
        if b["date"] <= as_of and (as_of - b["date"]).days <= window_days
    ]
    if bars_in_window:
        lookback_start = bars_in_window[0]["date"]
        actual_lookback_days = (as_of - lookback_start).days
    elif bars:
        lookback_start = bars[0]["date"]
        actual_lookback_days = (as_of - lookback_start).days
    else:
        lookback_start = None
        actual_lookback_days = 0

    return {
        "window_days": window_days,
        "lookback_extended": lookback_extended,
        "candidates": chosen,
        "lookback_start": lookback_start,
        "actual_lookback_days": actual_lookback_days,
        "analogs_at_2yr": n_at_2yr,
        "analogs_at_3yr": n_at_3yr,
        "trading_days_in_window": len(bars_in_window),
    }


def find_analog_days(
    ticker: str,
    as_of_date: date | str | None = None,
    *,
    today_gap: float | None = None,
    today_vol_ratio: float | None = None,
    history_calendar_days: int | None = None,
    volume_baseline_days: int = VOLUME_BASELINE_DAYS,
    min_gap_pct: float = MIN_GAP_PCT,
    vol_mult: float = VOL_MULT,
    similarity_strength: float = SIMILARITY_STRENGTH,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Find analog gap+volume days for `ticker` as of `as_of_date`.

    LOOKBACK (2yr default → step-extend to 3yr)
    ------------------------------------------
    Fetch up to HISTORY_MAX (~3yr). Prefer analogs inside the default ~2yr
    window. If count < MIN_ANALOGS_TARGET, grow the window in
    LOOKBACK_EXTEND_STEP_DAYS steps up to the 3yr hard cap. Never beyond 3yr.
    Young tickers use whatever history exists. Records actual_lookback_days
    and flags when the window was extended (older-regime data).

    ANALOG DATES: strictly past (date < as_of, age >= MIN_ANALOG_AGE_DAYS).
    """
    as_of = _parse_as_of(as_of_date)
    # Always fetch the hard cap once; window choice filters candidates.
    fetch_days = HISTORY_MAX_CALENDAR_DAYS
    if history_calendar_days is not None:
        fetch_days = min(int(history_calendar_days), HISTORY_MAX_CALENDAR_DAYS)
    start = as_of - timedelta(days=fetch_days)
    bars = fetch_daily_bars(ticker, start, as_of, api_key=api_key)
    bars = [b for b in bars if b["date"] <= as_of]

    all_candidates, gap_gt_min = _scan_gap_vol_candidates(
        bars,
        as_of,
        volume_baseline_days=volume_baseline_days,
        min_gap_pct=min_gap_pct,
        vol_mult=vol_mult,
    )
    # Safety net
    all_candidates = [
        c for c in all_candidates
        if date.fromisoformat(c["date"]) < as_of
        and int(c.get("age_days") or 0) >= MIN_ANALOG_AGE_DAYS
    ]

    win = _choose_lookback_window(all_candidates, as_of, bars)
    candidates = win["candidates"]
    n_analogs = len(candidates)
    actual_lookback_days = win["actual_lookback_days"]
    lookback_start = win["lookback_start"]
    lookback_extended = win["lookback_extended"]

    history_flag, confidence, lookback_note = classify_history(
        actual_lookback_days,
        n_analogs,
        lookback_extended=lookback_extended,
    )
    weighting = "equal" if similarity_strength <= 0 else "similarity_tilt"

    lookback_meta = {
        "actual_lookback_days": actual_lookback_days,
        "actual_lookback_years": round(actual_lookback_days / 365.25, 2),
        "actual_lookback_start_date": (
            lookback_start.isoformat() if lookback_start else None
        ),
        "lookback_default_days": HISTORY_DEFAULT_CALENDAR_DAYS,
        "lookback_cap_days": HISTORY_MAX_CALENDAR_DAYS,
        "lookback_window_days": win["window_days"],
        "lookback_extended": lookback_extended,
        "lookback_note": lookback_note,
        "analogs_at_2yr": win["analogs_at_2yr"],
        "analogs_at_3yr": win["analogs_at_3yr"],
        "min_analogs_target": MIN_ANALOGS_TARGET,
        "trading_days_scanned": win["trading_days_in_window"],
        "trading_days_fetched": len(bars),
        "analog_count": n_analogs,
        "history_flag": history_flag,
        "confidence": confidence,
    }

    empty_base = {
        "ticker": ticker.upper(),
        "as_of_date": as_of.isoformat(),
        "flag": "INSUFFICIENT_HISTORY" if history_flag == "**" else None,
        "history_flag": history_flag,
        "confidence": confidence,
        "lookback_note": lookback_note,
        "lookback_extended": lookback_extended,
        "analog_count": 0,
        "actual_lookback_days": actual_lookback_days,
        "actual_lookback_years": lookback_meta["actual_lookback_years"],
        "actual_lookback_start_date": lookback_meta["actual_lookback_start_date"],
        "analogs_at_2yr": win["analogs_at_2yr"],
        "analogs_at_3yr": win["analogs_at_3yr"],
        "weighting": weighting,
    }

    if n_analogs == 0:
        return {
            **empty_base,
            "stats": {
                **lookback_meta,
                "gap_gt_min_days": gap_gt_min,
                "analog_days": 0,
                "min_gap_pct": min_gap_pct,
                "vol_mult": vol_mult,
                "volume_baseline_days": volume_baseline_days,
                "history_calendar_days": win["window_days"],
                "similarity_strength": similarity_strength,
                "similarity_floor": SIMILARITY_FLOOR,
                "recency_weighting": False,
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
        sim = similarity_factor(
            c["gap_pct"],
            c["vol_ratio"],
            ref_gap,
            ref_vol,
            strength=similarity_strength,
        )
        c["similarity_weight"] = sim
        c["_raw"] = sim
        raw_sum += c["_raw"]

    analogs: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["date"]):
        combined = (c["_raw"] / raw_sum) if raw_sum > 0 else 0.0
        analogs.append({
            "date": c["date"],
            "gap_pct": c["gap_pct"],
            "vol_ratio": c["vol_ratio"],
            "age_days": c["age_days"],
            "similarity_weight": round(c["similarity_weight"], 6),
            "combined_weight": round(combined, 6),
        })

    weight_sum = sum(a["combined_weight"] for a in analogs)

    return {
        **empty_base,
        "analog_count": n_analogs,
        "ref_source": ref_source,
        "ref_gap": round(ref_gap, 6),
        "ref_vol_ratio": round(ref_vol, 4),
        "stats": {
            **lookback_meta,
            "gap_gt_min_days": gap_gt_min,
            "analog_days": n_analogs,
            "min_gap_pct": min_gap_pct,
            "vol_mult": vol_mult,
            "volume_baseline_days": volume_baseline_days,
            "history_calendar_days": win["window_days"],
            "similarity_strength": similarity_strength,
            "similarity_floor": SIMILARITY_FLOOR,
            "recency_weighting": False,
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


def confidence_label(
    n_analogs: int,
    actual_lookback_days: int | None = None,
    *,
    lookback_extended: bool = False,
) -> str:
    """
    Map analog count (+ optional lookback) to confidence band.

    Prefer classify_history() when lookback is known; fallback for callers
    that only pass analog count.
    """
    if actual_lookback_days is None:
        if n_analogs >= MIN_ANALOGS_RELIABLE:
            return "HIGH"
        if n_analogs >= 5:
            return "MEDIUM"
        if n_analogs >= MIN_ANALOGS_USABLE:
            return "LOW"
        return "INSUFFICIENT"
    _, conf, _ = classify_history(
        actual_lookback_days,
        n_analogs,
        lookback_extended=lookback_extended,
    )
    return conf


def derive_bracket(mae_w: dict, mfe_w: dict) -> dict:
    """
    Informational tier stops + target from MAE/MFE percentiles (equal-weight).

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


def outcome_analytics(
    per_day: list[dict],
    bracket: dict,
) -> dict[str, Any]:
    """
    Win-rate, conditional winner-MFE, failure-MAE, and R:R warning.

    Win = session close above entry proxy (held=True).
    Winner-MFE = MFE distribution among winners only.
    Failure-MAE = MAE distribution among losers only.
    R:R = target_pct / safe_max_stop_pct; warn if < RR_WARN_THRESHOLD.
    """
    n = len(per_day)
    winners = [r for r in per_day if r.get("held")]
    losers = [r for r in per_day if not r.get("held")]
    n_w, n_l = len(winners), len(losers)
    win_rate = (n_w / n) if n else 0.0

    def _pcts(vals: list[float]) -> dict:
        if not vals:
            return {"p50": None, "p75": None, "p90": None, "n": 0}
        return {
            "p50": round(unweighted_percentile(vals, 50) or 0.0, 6),
            "p75": round(unweighted_percentile(vals, 75) or 0.0, 6),
            "p90": round(unweighted_percentile(vals, 90) or 0.0, 6),
            "n": len(vals),
        }

    winner_mfe = _pcts([r["mfe_pct"] for r in winners])
    failure_mae = _pcts([r["mae_pct"] for r in losers])

    target = float(bracket.get("target_pct") or 0.0)
    safe = float(bracket.get("safe_max_stop_pct") or 0.0)
    rr = (target / safe) if safe > 0 else None
    rr_warning = None
    if rr is not None and rr < RR_WARN_THRESHOLD:
        rr_warning = (
            f"R:R {rr:.2f} below {RR_WARN_THRESHOLD} — "
            f"median MFE target may not justify safe-max stop width "
            f"(informational; not wired to orders)"
        )

    return {
        "win_definition": "session_close > entry_proxy (held)",
        "n_total": n,
        "n_winners": n_w,
        "n_losers": n_l,
        "win_rate": round(win_rate, 4),
        "win_rate_pct_display": round(win_rate * 100, 1),
        "winner_mfe": winner_mfe,
        "winner_mfe_p50_display": (
            None if winner_mfe["p50"] is None
            else round(winner_mfe["p50"] * 100, 3)
        ),
        "failure_mae": failure_mae,
        "failure_mae_p50_display": (
            None if failure_mae["p50"] is None
            else round(failure_mae["p50"] * 100, 3)
        ),
        "reward_risk": None if rr is None else round(rr, 3),
        "rr_warn_threshold": RR_WARN_THRESHOLD,
        "rr_warning": rr_warning,
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
    as_of_d = date.fromisoformat(str(as_of)[:10])
    # Never measure MAE/MFE on as_of / future / too-recent sessions.
    analogs = [
        a for a in (analogs_pack.get("analogs") or [])
        if date.fromisoformat(a["date"]) < as_of_d
        and (as_of_d - date.fromisoformat(a["date"])).days >= MIN_ANALOG_AGE_DAYS
    ]

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
    # Flag / confidence from lookback + measured analogs (fallback: finder count).
    lookback_days = int(analogs_pack.get("actual_lookback_days") or 0)
    lookback_start = analogs_pack.get("actual_lookback_start_date")
    lookback_extended = bool(analogs_pack.get("lookback_extended"))
    lookback_note = analogs_pack.get("lookback_note")
    # Flag on finder count (intended sample), not only measured — a single
    # measurable day must not look like a full profile.
    finder_n = len(analogs)
    analog_count_for_flag = finder_n
    history_flag, conf, note_from_classify = classify_history(
        lookback_days,
        analog_count_for_flag,
        lookback_extended=lookback_extended,
    )
    if note_from_classify:
        lookback_note = note_from_classify
    stats_meaningful = history_flag != "**" and conf != "INSUFFICIENT"

    lookback_fields = {
        "actual_lookback_days": lookback_days,
        "actual_lookback_years": round(lookback_days / 365.25, 2),
        "actual_lookback_start_date": lookback_start,
        "lookback_extended": lookback_extended,
        "lookback_note": lookback_note,
        "analogs_at_2yr": analogs_pack.get("analogs_at_2yr"),
        "analogs_at_3yr": analogs_pack.get("analogs_at_3yr"),
    }

    if n == 0 or not stats_meaningful:
        # Still persist per_analog for audit when n>0, but do NOT publish
        # percentiles / win-rate / R:R that look confident on n<3.
        note = (
            f"n={finder_n} analog(s), not meaningful — insufficient sample "
            f"(need ≥{MIN_ANALOGS_USABLE}). Informational flag only."
        )
        profile = {
            "ticker": ticker_u,
            "as_of_date": as_of,
            "informational_only": True,
            "stats_meaningful": False,
            "confidence": conf,
            "history_flag": history_flag,
            "analog_count": finder_n,
            **lookback_fields,
            "flag": "INSUFFICIENT_HISTORY" if history_flag == "**" else None,
            "analog_finder": {
                "flag": analogs_pack.get("flag"),
                "history_flag": analogs_pack.get("history_flag"),
                "confidence": analogs_pack.get("confidence"),
                "lookback_note": lookback_note,
                "stats": analogs_pack.get("stats"),
                "weight_sum": analogs_pack.get("weight_sum"),
            },
            "entry_proxy_min": entry_proxy_min,
            "n_analogs_finder": finder_n,
            "n_analogs_measured": n,
            "per_analog": per_day,
            "skipped": skipped,
            "percentiles": {},
            "hit_rates": {},
            "bracket": {},
            "outcomes": {
                "n_total": n,
                "n_finder": finder_n,
                "win_rate": None,
                "win_rate_pct_display": None,
                "reward_risk": None,
                "rr_warning": None,
                "note": note,
            },
            "sanity": {
                "ok": False,
                "checks": [note],
            },
        }
        return profile

    maes = [r["mae_pct"] for r in per_day]
    mfes = [r["mfe_pct"] for r in per_day]
    # Primary distribution = equal weight (1/n). Finder combined_weight is also
    # equal under default SIMILARITY_STRENGTH=0; re-normalize over measured days.
    equal_w = [1.0 / n] * n
    w_raw = [r["combined_weight"] for r in per_day]
    w_sum = sum(w_raw) or 1.0
    finder_w = [w / w_sum for w in w_raw]
    for r, w in zip(per_day, equal_w):
        r["weight_renorm"] = round(w, 6)

    def _pct_block(vals: list[float], wts: list[float]) -> dict:
        return {
            "p50": weighted_percentile(vals, wts, 50),
            "p75": weighted_percentile(vals, wts, 75),
            "p90": weighted_percentile(vals, wts, 90),
        }

    def _round_pct(d: dict) -> dict:
        return {
            k: (None if v is None else round(float(v), 6))
            for k, v in d.items()
        }

    # Bracket + primary percentiles use EQUAL weights (no recency skew).
    mae_eq = _round_pct(_pct_block(maes, equal_w))
    mfe_eq = _round_pct(_pct_block(mfes, equal_w))
    # Optional: finder weights (equal unless similarity tilt enabled)
    mae_fw = _round_pct(_pct_block(maes, finder_w))
    mfe_fw = _round_pct(_pct_block(mfes, finder_w))

    hit_rates: dict[str, dict] = {}
    for thr in MFE_HIT_BUCKETS:
        flags = [1.0 if m >= thr else 0.0 for m in mfes]
        hit_eq = sum(flags) / n
        key = f"mfe_ge_{int(thr * 100)}pct"
        hit_rates[key] = {
            "threshold_pct": thr,
            "equal_weight": round(hit_eq, 4),
        }

    bracket = derive_bracket(mae_eq, mfe_eq)
    outcomes = outcome_analytics(per_day, bracket)

    # Sanity checks
    checks: list[str] = []
    ok = True
    if not (mae_eq["p50"] <= mae_eq["p75"] <= mae_eq["p90"]):
        ok = False
        checks.append("FAIL: MAE percentiles not non-decreasing")
    else:
        checks.append("OK: MAE p50 <= p75 <= p90 (equal-weight)")
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
    if outcomes.get("rr_warning"):
        checks.append(f"WARN: {outcomes['rr_warning']}")

    profile = {
        "ticker": ticker_u,
        "as_of_date": as_of,
        "informational_only": True,
        "stats_meaningful": True,
        "confidence": conf,
        "history_flag": history_flag,
        "analog_count": n,
        **lookback_fields,
        "weighting": analogs_pack.get("weighting", "equal"),
        "flag": "INSUFFICIENT_HISTORY" if history_flag == "**" else None,
        "n_analogs_finder": len(analogs),
        "n_analogs_measured": n,
        "entry_proxy_min": entry_proxy_min,
        "analog_finder": {
            "flag": analogs_pack.get("flag"),
            "history_flag": analogs_pack.get("history_flag"),
            "confidence": analogs_pack.get("confidence"),
            "lookback_note": lookback_note,
            "weighting": analogs_pack.get("weighting"),
            "stats": analogs_pack.get("stats"),
            "weight_sum": analogs_pack.get("weight_sum"),
            "ref_source": analogs_pack.get("ref_source"),
        },
        "per_analog": per_day,
        "skipped": skipped,
        "percentiles": {
            "scheme": "equal_weight",
            "mae": mae_eq,
            "mfe": mfe_eq,
            "mae_finder_weights": mae_fw,
            "mfe_finder_weights": mfe_fw,
        },
        "hit_rates": hit_rates,
        "bracket": bracket,
        "outcomes": outcomes,
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
    hf = result.get("history_flag") or ""
    label = f"{result['ticker']}{(' ' + hf) if hf else ''}"
    print(f"\n{'=' * 60}")
    print(f"ANALOG FINDER — {label}  as_of={result['as_of_date']}")
    print(f"{'=' * 60}")
    print(f"Weighting: {result.get('weighting')}  "
          f"(recency_weighting={stats.get('recency_weighting')})")
    print(
        f"Lookback: {result.get('actual_lookback_days')}d "
        f"({result.get('actual_lookback_years')}yr, "
        f"start={result.get('actual_lookback_start_date')}, "
        f"extended={result.get('lookback_extended')})"
    )
    print(
        f"Analogs @2yr={result.get('analogs_at_2yr')}  "
        f"@3yr={result.get('analogs_at_3yr')}  "
        f"used={result.get('analog_count')}"
    )
    if result.get("lookback_note"):
        print(f"NOTE: {result['lookback_note']}")
    print(f"Trading days scanned:     {stats['trading_days_scanned']}")
    print(f"Gap > {stats['min_gap_pct']:.0%} days:         {stats['gap_gt_min_days']}")
    print(
        f"Analog days (gap+vol≥{stats['vol_mult']}x): "
        f"{stats['analog_days']}"
    )
    print(
        f"history_flag={hf!r}  confidence={result.get('confidence')}  "
        f"legacy_flag={result.get('flag')}"
    )
    print(f"\n{'date':<12} {'gap%':>8} {'vol_r':>8} {'weight':>10}")
    print("-" * 42)
    for a in result["analogs"]:
        print(
            f"{a['date']:<12} {a['gap_pct'] * 100:7.2f}% "
            f"{a['vol_ratio']:8.2f} {a['combined_weight']:10.6f}"
        )
    print("-" * 42)
    print(f"{'weight sum':<12} {'':>8} {'':>8} {result['weight_sum']:10.6f}")
    print(
        f"Analogs found: {stats['analog_days']}  "
        f"(** if <{MIN_ANALOGS_USABLE}; reliable if ≥{MIN_ANALOGS_RELIABLE} "
        f"+ lookback ≥{RELIABLE_LOOKBACK_DAYS}d)"
    )


def _print_profile(profile: dict) -> None:
    """Human-readable Commit-2 proof output."""
    hf = profile.get("history_flag") or ""
    label = f"{profile['ticker']}{(' ' + hf) if hf else ''}"
    print(f"\n{'=' * 72}")
    print(
        f"MAE/MFE PROFILE — {label}  as_of={profile['as_of_date']}  "
        f"confidence={profile['confidence']}"
    )
    print(f"{'=' * 72}")
    print(
        f"Lookback: {profile.get('actual_lookback_days')}d "
        f"({profile.get('actual_lookback_years')}yr, "
        f"start={profile.get('actual_lookback_start_date')})  "
        f"extended={profile.get('lookback_extended')}  "
        f"history_flag={hf!r}  analog_count={profile.get('analog_count')}"
    )
    if profile.get("lookback_note"):
        print(f"NOTE: {profile['lookback_note']}")
    print(
        f"Analogs @2yr={profile.get('analogs_at_2yr')}  "
        f"@3yr={profile.get('analogs_at_3yr')}"
    )
    print(
        f"Analogs: finder={profile.get('n_analogs_finder')}  "
        f"measured={profile.get('n_analogs_measured')}  "
        f"weighting={profile.get('weighting')}  "
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
    print("\nPercentiles (equal-weight, fraction of entry):")
    print(f"  {'':8} {'MAE':>10} {'MFE':>10}")
    for key in ("p50", "p75", "p90"):
        print(
            f"  {key:<8} "
            f"{(mae.get(key) or 0) * 100:9.2f}% "
            f"{(mfe.get(key) or 0) * 100:9.2f}%"
        )

    print("\nMFE hit-rates (equal-weight):")
    for _k, h in (profile.get("hit_rates") or {}).items():
        thr = h["threshold_pct"] * 100
        print(f"  MFE ≥ +{thr:.0f}%:  {h['equal_weight'] * 100:.1f}%")

    out = profile.get("outcomes") or {}
    print("\nOutcomes:")
    print(
        f"  Win rate: {out.get('win_rate_pct_display')}%  "
        f"({out.get('n_winners')}/{out.get('n_total')} held close>entry)"
    )
    wm = out.get("winner_mfe_p50_display")
    fm = out.get("failure_mae_p50_display")
    print(f"  Winner MFE p50:  {wm}%  (conditional on held)")
    print(f"  Failure MAE p50: {fm}%  (conditional on NOT held)")
    print(f"  Reward:Risk:     {out.get('reward_risk')}  "
          f"(target / safe_max_stop)")
    if out.get("rr_warning"):
        print(f"  R:R WARNING:     {out['rr_warning']}")

    b = profile.get("bracket") or {}
    t = b.get("tiers") or {}
    print("\nDerived bracket (informational, equal-weight):")
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
