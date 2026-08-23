"""
strategy_lab/collect_setups.py

Assemble the historical setup master list for Strategy Lab backtests
from LOCAL files only — no Polygon, no IBKR, no live agent calls.

Sources
-------
1. profiles/*_profile.json / *_analogs.json
   - each analog day (ticker + date)
   - the profile ticker itself (as_of / profile date)

2. Scanner live-flagged candidates
   - Preferred: data/watchlist_sync.json
   - Optional:  data/telegram_alerts.log (best-effort parse)
   - Fallback archives (when data/ sync files are absent):
       candidates/full_scan/scan_YYYY-MM-DD.json
       candidates/daily_scan_*.json

Output
------
strategy_lab/results/setups.json
  { setups: [{ticker, flag_date, source, gap_pct, vol_ratio, score}, ...],
    tickers: { TICKER: {earliest_flag_date, n_setups}, ... },
    summary: {...} }

Usage (from repo root):
  py -3 strategy_lab/collect_setups.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"
DATA_DIR = ROOT / "data"
FULL_SCAN_DIR = ROOT / "candidates" / "full_scan"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
OUT_PATH = RESULTS_DIR / "setups.json"


def _norm_gap(raw: Any) -> float | None:
    """Store gap as a fraction; values with |x|>1 are treated as percent."""
    if raw is None:
        return None
    try:
        g = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(g) > 1.0:
        g = g / 100.0
    return round(g, 6)


def _norm_score(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return round(float(raw), 6)
    except (TypeError, ValueError):
        return None


def _norm_vol(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return round(float(raw), 6)
    except (TypeError, ValueError):
        return None


def _iso_date(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None


def _add(
    bucket: list[dict],
    *,
    ticker: str,
    flag_date: str | None,
    source: str,
    gap_pct: Any = None,
    vol_ratio: Any = None,
    score: Any = None,
) -> None:
    t = str(ticker or "").upper().strip()
    d = _iso_date(flag_date)
    if not t or not d:
        return
    bucket.append({
        "ticker": t,
        "flag_date": d,
        "source": source,
        "gap_pct": _norm_gap(gap_pct),
        "vol_ratio": _norm_vol(vol_ratio),
        "score": _norm_score(score),
    })


# ── Source 1: profiles ──────────────────────────────────────────────────────

def collect_from_profiles() -> list[dict]:
    """Extract analog days + profile ticker seeds from profiles/*.json."""
    out: list[dict] = []
    if not PROFILES_DIR.is_dir():
        print(f"  [profiles] missing dir: {PROFILES_DIR}")
        return out

    profile_files = sorted(PROFILES_DIR.glob("*_profile.json"))
    analog_files = sorted(PROFILES_DIR.glob("*_analogs.json"))
    print(
        f"  [profiles] {len(profile_files)} profile JSON(s), "
        f"{len(analog_files)} analogs JSON(s)"
    )

    tickers_from_profiles: set[str] = set()

    for path in profile_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [profiles] skip {path.name}: {exc}")
            continue

        ticker = (
            data.get("ticker") or path.name.replace("_profile.json", "")
        ).upper()
        tickers_from_profiles.add(ticker)
        as_of = (
            data.get("as_of_date")
            or data.get("as_of")
            or data.get("generated_at")
        )
        _add(
            out,
            ticker=ticker,
            flag_date=as_of,
            source="profile_ticker",
            gap_pct=None,
            score=None,
        )
        for row in data.get("per_analog") or data.get("analogs") or []:
            _add(
                out,
                ticker=ticker,
                flag_date=row.get("date") or row.get("flag_date"),
                source="profile_analog",
                gap_pct=row.get("gap_pct"),
                vol_ratio=row.get("vol_ratio"),
                score=row.get("combined_weight"),
            )

    # Analogs-only files when no matching profile was loaded
    for path in analog_files:
        stem_ticker = path.name.replace("_analogs.json", "").upper()
        if stem_ticker in tickers_from_profiles:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [profiles] skip {path.name}: {exc}")
            continue
        ticker = (data.get("ticker") or stem_ticker).upper()
        as_of = data.get("as_of_date") or data.get("as_of")
        _add(
            out,
            ticker=ticker,
            flag_date=as_of,
            source="profile_ticker",
            gap_pct=None,
            score=None,
        )
        for row in data.get("analogs") or []:
            _add(
                out,
                ticker=ticker,
                flag_date=row.get("date"),
                source="profile_analog",
                gap_pct=row.get("gap_pct"),
                vol_ratio=row.get("vol_ratio"),
                score=row.get("combined_weight"),
            )

    print(f"  [profiles] extracted {len(out)} raw row(s)")
    return out


# ── Source 2: live scanner flags ────────────────────────────────────────────

def _collect_watchlist_sync() -> list[dict]:
    """Parse data/watchlist_sync.json if present (flexible shapes)."""
    path = DATA_DIR / "watchlist_sync.json"
    out: list[dict] = []
    if not path.exists():
        print(
            f"  [live-scan] missing {path.relative_to(ROOT)} "
            f"(ok — using scan archives if present)"
        )
        return out

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [live-scan] failed to parse watchlist_sync.json: {exc}")
        return out

    rows: list[dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("candidates", "watchlist", "rows", "items", "data"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        if not rows:
            for k, v in data.items():
                if isinstance(v, list) and _iso_date(k):
                    for r in v:
                        if isinstance(r, dict):
                            r = dict(r)
                            r.setdefault("date", k)
                            rows.append(r)
                elif isinstance(v, dict) and _iso_date(k):
                    for r in (
                        v.get("candidates")
                        or v.get("watchlist")
                        or v.get("rows")
                        or []
                    ):
                        if isinstance(r, dict):
                            r = dict(r)
                            r.setdefault("date", k)
                            rows.append(r)

    for r in rows:
        if not isinstance(r, dict):
            continue
        _add(
            out,
            ticker=r.get("ticker") or r.get("symbol"),
            flag_date=(
                r.get("date")
                or r.get("scan_date")
                or r.get("flag_date")
                or r.get("as_of")
            ),
            source="watchlist_sync",
            gap_pct=r.get("gap_pct") or r.get("gap_estimate"),
            vol_ratio=(
                r.get("vol_ratio")
                or r.get("pm_vol_ratio")
                or r.get("rvol")
            ),
            score=r.get("score") or r.get("rank_score") or r.get("rank"),
        )
    print(f"  [live-scan] watchlist_sync.json → {len(out)} row(s)")
    return out


def _collect_telegram_log() -> list[dict]:
    """Best-effort parse of data/telegram_alerts.log for flagged tickers."""
    path = DATA_DIR / "telegram_alerts.log"
    out: list[dict] = []
    if not path.exists():
        print(f"  [live-scan] missing {path.relative_to(ROOT)}")
        return out

    date_re = re.compile(r"(20\d{2}-\d{2}-\d{2})")
    ticker_re = re.compile(r"\b([A-Z]{1,5})\b")
    gap_re = re.compile(
        r"gap[_ ]*(?:pct)?[=:\s]+(-?\d+(?:\.\d+)?)\s*%?", re.I
    )
    score_re = re.compile(r"score[=:\s]+(-?\d+(?:\.\d+)?)", re.I)
    vol_re = re.compile(
        r"(?:vol|rvol|pm_vol)[_ ]*(?:ratio)?[=:\s]+(-?\d+(?:\.\d+)?)", re.I
    )
    noise = {
        "Q", "ALPHA", "WATCHLIST", "GAP", "SCORE", "VOL", "RVOL",
        "ET", "AM", "PM", "YES", "NO", "USD", "HTTP", "HTTPS", "THE",
    }

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"  [live-scan] failed to read telegram_alerts.log: {exc}")
        return out

    for line in text.splitlines():
        if not line.strip():
            continue
        if not re.search(r"watchlist|candidate|gap|flag", line, re.I):
            continue
        dm = date_re.search(line)
        if not dm:
            continue
        flag_date = dm.group(1)
        tm = re.search(
            r"(?:ticker|symbol|WATCHLIST)?\s*[#:]?\s*([A-Z]{1,5})\b",
            line,
        )
        ticker = tm.group(1) if tm else None
        if not ticker or ticker in noise:
            for m in ticker_re.finditer(line):
                cand = m.group(1)
                if cand not in noise and len(cand) >= 2:
                    ticker = cand
                    break
        if not ticker:
            continue
        gm = gap_re.search(line)
        sm = score_re.search(line)
        vm = vol_re.search(line)
        _add(
            out,
            ticker=ticker,
            flag_date=flag_date,
            source="telegram_alerts",
            gap_pct=gm.group(1) if gm else None,
            vol_ratio=vm.group(1) if vm else None,
            score=sm.group(1) if sm else None,
        )

    print(f"  [live-scan] telegram_alerts.log → {len(out)} row(s)")
    return out


def _collect_full_scan_archives() -> list[dict]:
    """Fallback: candidates/full_scan/scan_*.json (morning Polygon scan dumps)."""
    out: list[dict] = []
    if not FULL_SCAN_DIR.is_dir():
        print(f"  [live-scan] missing {FULL_SCAN_DIR.relative_to(ROOT)}")
        return out

    files = sorted(FULL_SCAN_DIR.glob("scan_*.json"))
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [live-scan] skip {path.name}: {exc}")
            continue
        scan_date = data.get("scan_date") or path.stem.replace("scan_", "")
        rows: list = list(
            data.get("candidates_all")
            or data.get("candidates")
            or []
        )
        for extra_key in ("top_n", "candidates_watchlist", "watchlist"):
            extra = data.get(extra_key) or []
            if isinstance(extra, list):
                rows.extend(extra)
        n_before = len(out)
        for r in rows:
            if not isinstance(r, dict):
                continue
            _add(
                out,
                ticker=r.get("ticker"),
                flag_date=scan_date,
                source="full_scan",
                gap_pct=r.get("gap_pct") or r.get("todays_change_pct"),
                vol_ratio=(
                    r.get("pm_vol_ratio")
                    or r.get("rvol")
                    or r.get("vol_ratio")
                ),
                score=(
                    r.get("rank_score")
                    or r.get("score")
                    or r.get("rank")
                ),
            )
        print(
            f"  [live-scan] {path.name} ({scan_date}) → "
            f"{len(out) - n_before} row(s)"
        )
    return out


def _collect_daily_scan_archives() -> list[dict]:
    """Fallback: candidates/daily_scan_*.json."""
    out: list[dict] = []
    files = sorted((ROOT / "candidates").glob("daily_scan_*.json"))
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [live-scan] skip {path.name}: {exc}")
            continue
        scan_date = (
            data.get("scan_date") or path.stem.replace("daily_scan_", "")
        )
        n_before = len(out)
        for r in data.get("candidates") or []:
            if not isinstance(r, dict):
                continue
            _add(
                out,
                ticker=r.get("ticker"),
                flag_date=scan_date,
                source="daily_scan",
                gap_pct=r.get("gap_estimate") or r.get("gap_pct"),
                vol_ratio=r.get("pm_vol_ratio") or r.get("vol_ratio"),
                score=r.get("rank") or r.get("score"),
            )
        print(
            f"  [live-scan] {path.name} ({scan_date}) → "
            f"{len(out) - n_before} row(s)"
        )
    return out


def collect_from_live_scans() -> list[dict]:
    """Aggregate all live-scan / archive sources."""
    rows: list[dict] = []
    rows.extend(_collect_watchlist_sync())
    rows.extend(_collect_telegram_log())
    rows.extend(_collect_full_scan_archives())
    rows.extend(_collect_daily_scan_archives())
    return rows


# ── Dedup + ticker earliest ─────────────────────────────────────────────────

_SOURCE_PRIORITY = {
    "watchlist_sync": 0,
    "telegram_alerts": 1,
    "full_scan": 2,
    "daily_scan": 3,
    "profile_analog": 4,
    "profile_ticker": 5,
}


def dedupe_setups(rows: list[dict]) -> list[dict]:
    """
    Unique on (ticker, flag_date). Prefer live-scan sources over profile seeds;
    keep richer gap/score when merging.
    """
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["ticker"], row["flag_date"])
        prev = best.get(key)
        if prev is None:
            best[key] = dict(row)
            continue
        if _SOURCE_PRIORITY.get(row["source"], 99) < _SOURCE_PRIORITY.get(
            prev["source"], 99
        ):
            merged = dict(row)
            for f in ("gap_pct", "vol_ratio", "score"):
                if merged.get(f) is None:
                    merged[f] = prev.get(f)
            best[key] = merged
        else:
            for f in ("gap_pct", "vol_ratio", "score"):
                if prev.get(f) is None and row.get(f) is not None:
                    prev[f] = row[f]
    return sorted(best.values(), key=lambda r: (r["flag_date"], r["ticker"]))


def ticker_earliest_map(setups: list[dict]) -> dict[str, dict]:
    """Per ticker: earliest flag_date + setup count."""
    dates: dict[str, list[str]] = defaultdict(list)
    for s in setups:
        dates[s["ticker"]].append(s["flag_date"])
    out: dict[str, dict] = {}
    for t, ds in sorted(dates.items()):
        ds_sorted = sorted(ds)
        out[t] = {
            "earliest_flag_date": ds_sorted[0],
            "latest_flag_date": ds_sorted[-1],
            "n_setups": len(ds_sorted),
        }
    return out


def _is_profile_source(src: str) -> bool:
    return src.startswith("profile_")


def _is_live_source(src: str) -> bool:
    return not _is_profile_source(src)


def print_summary(setups: list[dict], tickers: dict[str, dict]) -> None:
    n_profile = sum(1 for s in setups if _is_profile_source(s["source"]))
    n_live = sum(1 for s in setups if _is_live_source(s["source"]))
    dates = [s["flag_date"] for s in setups]
    d_min = min(dates) if dates else "—"
    d_max = max(dates) if dates else "—"

    by_src: dict[str, int] = defaultdict(int)
    for s in setups:
        by_src[s["source"]] += 1

    print("\n" + "=" * 64)
    print("STRATEGY LAB — SETUP COLLECTOR SUMMARY")
    print("=" * 64)
    print(f"  Setups from profiles     : {n_profile}")
    print(f"  Setups from live-scans   : {n_live}")
    print(f"  Total unique setups      : {len(setups)}")
    print(f"  Unique tickers           : {len(tickers)}")
    print(f"  Date range               : {d_min} → {d_max}")
    print("  By source:")
    for src, n in sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {src:<20} {n:>5}")
    print("-" * 64)
    print(f"  {'Ticker':<8} {'Earliest':<12} {'Latest':<12} {'N':>4}")
    print("-" * 64)
    for t, meta in list(tickers.items())[:30]:
        print(
            f"  {t:<8} {meta['earliest_flag_date']:<12} "
            f"{meta['latest_flag_date']:<12} {meta['n_setups']:>4}"
        )
    if len(tickers) > 30:
        print(f"  ... +{len(tickers) - 30} more tickers")
    print("=" * 64)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


def main() -> int:
    print("Collecting setups from local files (no Polygon / no IBKR)…")
    print(f"Repo root: {ROOT}")

    profile_rows = collect_from_profiles()
    live_rows = collect_from_live_scans()
    raw = profile_rows + live_rows
    print(f"\n  Raw rows before dedupe: {len(raw)}")

    setups = dedupe_setups(raw)
    tickers = ticker_earliest_map(setups)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "note": (
            "Setup list only — no price/performance fetched. "
            "gap_pct stored as fraction when known. "
            "data/watchlist_sync.json and telegram_alerts.log are preferred "
            "live sources; candidates/full_scan and daily_scan_* are archives."
        ),
        "summary": {
            "n_setups": len(setups),
            "n_from_profiles": sum(
                1 for s in setups if _is_profile_source(s["source"])
            ),
            "n_from_live_scans": sum(
                1 for s in setups if _is_live_source(s["source"])
            ),
            "n_tickers": len(tickers),
            "date_min": min((s["flag_date"] for s in setups), default=None),
            "date_max": max((s["flag_date"] for s in setups), default=None),
        },
        "tickers": tickers,
        "setups": setups,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print_summary(setups, tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
