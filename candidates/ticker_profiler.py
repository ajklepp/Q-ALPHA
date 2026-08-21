"""
Q-ALPHA ticker_profiler — Commit 1: ANALOG FINDER (read-only).

Finds historical "analog" gap days for a ticker from Polygon daily bars.
No trading logic, no orders, no pool/IBKR. Foundation for a later
statistically-calibrated tiered-stop profile (MAE/MFE = Commit 2).

Analog day = gap > MIN_GAP_PCT AND volume >= VOL_MULT × trailing avg volume.
Each analog is weighted by (1) recency and (2) volatility-similarity, then
normalized so combined weights sum to 1.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────
CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
PROFILES_DIR = ROOT / "profiles"

POLYGON_BASE = "https://api.polygon.io"

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


if __name__ == "__main__":
    import sys

    sym = (sys.argv[1] if len(sys.argv) > 1 else "JOBY").upper()
    print(f"Running find_analog_days({sym!r}) ...")
    result = find_analog_days(sym)
    _print_report(result)
    out = save_analogs_json(result)
    print(f"\nWrote {out.relative_to(ROOT)}")
