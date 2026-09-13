"""
Read-only Peak Hour watchlist resolution for the logger universe.

WHY: Subscribe the names Peak Hour is actually watching, without importing
or mutating TSD / Peak Hour strategy modules. JSON artifacts only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_TOP_N,
    FALLBACK_REASON,
    FALLBACK_SYMBOLS,
    LAUNCH_ARTIFACT,
    QUEUE_ARTIFACT,
    WATCHLIST_ARTIFACT,
)


@dataclass
class UniversePick:
    """One symbol chosen for logging, with score provenance."""

    symbol: str
    score: float
    dollar_vol: float
    source: str
    reason: str


@dataclass
class UniverseResult:
    """Resolved logger universe (CLI override, artifacts, or fallback)."""

    symbols: list[str]
    picks: list[UniversePick] = field(default_factory=list)
    used_fallback: bool = False
    fallback_reason: str | None = None
    artifacts_seen: list[str] = field(default_factory=list)


def repo_root() -> Path:
    """Repo root (Documents/Q-ALPHA layout): parents of this package."""
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> Any | None:
    """Load JSON if the file exists; ignore corrupt files (read-only best effort)."""
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _as_rows(doc: Any) -> list[dict[str, Any]]:
    """Normalize launch / queue / watchlist payloads to a list of dict rows."""
    if doc is None:
        return []
    if isinstance(doc, list):
        rows: list[dict[str, Any]] = []
        for item in doc:
            if isinstance(item, str) and item.strip():
                rows.append({"symbol": item.strip().upper()})
            elif isinstance(item, dict):
                rows.append(item)
        return rows
    if not isinstance(doc, dict):
        return []
    for key in ("rows", "queue", "watch", "symbols", "tickers"):
        raw = doc.get(key)
        if isinstance(raw, list):
            return _as_rows(raw)
    return []


def _num(*values: Any) -> float:
    for val in values:
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _row_score(row: dict[str, Any]) -> tuple[float, float]:
    """
    Liquidity-then-score heuristic.

    Prefer dollar volume (1h / 20d / generic), then continuation / launch /
    scan score. Rank (1=best) is a small tie-break boost.
    """
    dollar_vol = _num(
        row.get("dollar_vol_1h"),
        row.get("dollar_vol_20d_avg"),
        row.get("dollar_vol"),
        row.get("avg_dollar_vol"),
        row.get("dollarVolume"),
    )
    score = _num(
        row.get("continuation_score"),
        row.get("combined_rank_score"),
        row.get("launch_score_display"),
        row.get("launch_score"),
        row.get("entry_score"),
        row.get("scan_score"),
        row.get("htf_score"),
    )
    rank = _num(row.get("rank"))
    rank_boost = (100.0 - rank) if rank > 0 else 0.0
    # Dollar vol dominates so liquid names win when scores are sparse.
    blended = dollar_vol + score * 1_000.0 + rank_boost
    return blended, dollar_vol


def _collect_artifact(path: Path, source: str) -> list[UniversePick]:
    doc = _read_json(path)
    if doc is None:
        return []
    picks: list[UniversePick] = []
    for row in _as_rows(doc):
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not sym:
            continue
        blended, dollar_vol = _row_score(row)
        picks.append(
            UniversePick(
                symbol=sym,
                score=blended,
                dollar_vol=dollar_vol,
                source=source,
                reason=f"{source} score={blended:.1f} dvol={dollar_vol:.0f}",
            )
        )
    return picks


def resolve_universe(
    *,
    root: Path | None = None,
    symbols: list[str] | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> UniverseResult:
    """
    Pick top-N liquid Peak Hour names, or CLI override, or fallback ETFs.

    Never writes artifacts. Never imports tsd_* / Peak Hour strategy code.
    """
    n = max(1, int(top_n))
    if symbols:
        clean = []
        seen: set[str] = set()
        for raw in symbols:
            sym = str(raw).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            clean.append(sym)
            if len(clean) >= n:
                break
        picks = [
            UniversePick(symbol=s, score=0.0, dollar_vol=0.0, source="cli", reason="--symbols")
            for s in clean
        ]
        return UniverseResult(symbols=clean, picks=picks, used_fallback=False)

    base = root or repo_root()
    artifact_specs = (
        (base / LAUNCH_ARTIFACT, "last_1h_launch"),
        (base / QUEUE_ARTIFACT, "tsd_watch_queue"),
        (base / WATCHLIST_ARTIFACT, "last_watchlist"),
    )
    merged: dict[str, UniversePick] = {}
    seen_paths: list[str] = []
    for path, source in artifact_specs:
        rows = _collect_artifact(path, source)
        if rows:
            seen_paths.append(str(path.relative_to(base)) if path.is_relative_to(base) else str(path))
        for pick in rows:
            prev = merged.get(pick.symbol)
            if prev is None or pick.score > prev.score:
                merged[pick.symbol] = pick

    ranked = sorted(merged.values(), key=lambda p: (p.dollar_vol, p.score), reverse=True)
    top = ranked[:n]
    if top:
        return UniverseResult(
            symbols=[p.symbol for p in top],
            picks=top,
            used_fallback=False,
            artifacts_seen=seen_paths,
        )

    fb = list(FALLBACK_SYMBOLS[:n])
    picks = [
        UniversePick(
            symbol=s,
            score=0.0,
            dollar_vol=0.0,
            source="fallback",
            reason=FALLBACK_REASON,
        )
        for s in fb
    ]
    return UniverseResult(
        symbols=fb,
        picks=picks,
        used_fallback=True,
        fallback_reason=FALLBACK_REASON,
        artifacts_seen=seen_paths,
    )
