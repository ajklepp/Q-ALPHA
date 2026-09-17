"""
Peak Hour momentum-rank overlay (LIVE when PHP_MOMENTUM_RANK is ON, default).

After equal-signal admission, scarce slots should prefer same-day rippers
(momentum + room + tape) over slow multi-day popular names.

Hard-extension (scan >= 75) and case ENTER remain surgical vetoes.
This overlay does not change trails / keep-profit / take cap.

Set PHP_MOMENTUM_RANK=0 to restore pre-change ranking (equal-signal or v1.6
score as-is; take sort = case confidence then continuation; popularity-only
take filter). See candidates/tsd_scan_pipeline/REVERT.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PHP_MOMENTUM_RANK_ENV = "PHP_MOMENTUM_RANK"
MOMENTUM_RANK_VERSION = "live_v1"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Same-day ripper boosts (capped so we do not crown every runner).
RS_1H_STRONG = 0.03
RS_1H_MILD = 0.015
RS_1H_STRONG_PTS = 16.0
RS_1H_MILD_PTS = 8.0

SESSION_RET_STRONG = 0.04
SESSION_RET_MILD = 0.02
SESSION_RET_STRONG_PTS = 14.0
SESSION_RET_MILD_PTS = 7.0

VOL_HOT = 2.0
VOL_WARM = 1.25
VOL_HOT_PTS = 12.0
VOL_WARM_PTS = 6.0

DV1H_STRONG = 0.15
DV1H_EXTRA_PTS = 6.0

LIVE_GAINER_HOT_PTS = 10.0
LIVE_GAINER_ALONE_PTS = 4.0

ROOM_CONSTRUCTIVE = 0.10
ROOM_TIGHT = 0.05
ROOM_CONSTRUCTIVE_PTS = 8.0
ROOM_TIGHT_PENALTY = 6.0

MAX_RIPPER_BOOST = 36.0

# Slow popular: on the multi-day board, but tape is not actually ripping.
SLOW_POPULAR_PENALTY = 18.0
HIST_WITHOUT_TAPE_PENALTY = 8.0
HIST_HIT_HIGH = 0.35
DEAD_TAPE_EXTRA_PENALTY = 10.0


def _php_momentum_rank_raw() -> str:
    """Env first, then repo .env, then default ON ('1')."""
    import os

    raw = os.environ.get(PHP_MOMENTUM_RANK_ENV)
    if raw is not None:
        return raw
    env_path = _REPO_ROOT / ".env"
    try:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                key, _, val = s.partition("=")
                if key.strip() == PHP_MOMENTUM_RANK_ENV:
                    return val.strip().strip('"').strip("'")
    except Exception:
        pass
    return "1"


def momentum_rank_enabled() -> bool:
    """
    Live Peak Hour momentum-rank overlay. Default ON.

    PHP_MOMENTUM_RANK=0 / false / off / no restores pre-change ranking
    (score as computed by equal-signal / v1.6; popularity-first take sort).
    """
    return _php_momentum_rank_raw().strip().lower() not in ("0", "false", "off", "no")


def momentum_rank_mode_label() -> str:
    """ON or OFF — for scan logs and Telegram."""
    return "ON" if momentum_rank_enabled() else "OFF"


def _finite(val: Any) -> float | None:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _vol_ratio(row: dict[str, Any]) -> float:
    return _finite(row.get("vol_ratio_20") or row.get("vol_ratio")) or 0.0


def _room(row: dict[str, Any]) -> float:
    return _finite(row.get("dist_20d_high_pct")) or 0.0


def _rs_1h(row: dict[str, Any]) -> float | None:
    if int(row.get("rs_spy_1h_ok") or 0) != 1 and row.get("rs_spy_1h") is None:
        return None
    return _finite(row.get("rs_spy_1h"))


def _session_ret(row: dict[str, Any]) -> float | None:
    """Same-session return through the signal bar when present (no look-ahead)."""
    for key in ("session_ret", "session_ret_1h", "intraday_ret"):
        val = _finite(row.get(key))
        if val is not None:
            return val
    return _rs_1h(row)


def is_tape_hot(row: dict[str, Any]) -> bool:
    """
    True when the signal-time tape is actually moving.

    Used to tell a ripper from a slow popular name. Missing fields fail closed
    (not hot) so we do not invent heat.
    """
    vol = _vol_ratio(row)
    rs = _rs_1h(row)
    sess = _session_ret(row)
    dv = _finite(row.get("dollar_vol_1h_vs_20d"))
    if vol >= VOL_WARM:
        return True
    if rs is not None and rs >= RS_1H_MILD:
        return True
    if sess is not None and sess >= SESSION_RET_MILD:
        return True
    if dv is not None and dv >= DV1H_STRONG:
        return True
    if bool(row.get("on_gainers")) and vol >= 1.0:
        return True
    return False


def is_slow_popular(row: dict[str, Any]) -> bool:
    """Multi-day / tradable popular without same-day tape heat."""
    popular = bool(
        row.get("tradable_popular")
        or row.get("recent_leaderboard")
        or row.get("recent_gainer")
        or row.get("recent_most_active")
    )
    if not popular:
        return False
    if bool(row.get("on_gainers")) and is_tape_hot(row):
        return False
    return not is_tape_hot(row)


def momentum_rank_delta(row: dict[str, Any]) -> float:
    """
    Points added to continuation_score when the overlay is ON.

    Boosts same-day RS / session return / volume / live-gainer+tape / room.
    Penalizes slow popular and high hist-prior without tape.
    Ripper boosts are capped (do not grab every runner).
    """
    boost = 0.0
    rs = _rs_1h(row)
    if rs is not None:
        if rs >= RS_1H_STRONG:
            boost += RS_1H_STRONG_PTS
        elif rs >= RS_1H_MILD:
            boost += RS_1H_MILD_PTS

    sess = _session_ret(row)
    if sess is not None:
        if sess >= SESSION_RET_STRONG:
            boost += SESSION_RET_STRONG_PTS
        elif sess >= SESSION_RET_MILD:
            boost += SESSION_RET_MILD_PTS

    vol = _vol_ratio(row)
    if vol >= VOL_HOT:
        boost += VOL_HOT_PTS
    elif vol >= VOL_WARM:
        boost += VOL_WARM_PTS

    dv = _finite(row.get("dollar_vol_1h_vs_20d"))
    if dv is not None and dv >= DV1H_STRONG:
        boost += DV1H_EXTRA_PTS

    if bool(row.get("on_gainers")):
        boost += LIVE_GAINER_HOT_PTS if is_tape_hot(row) else LIVE_GAINER_ALONE_PTS

    room = _room(row)
    if room >= ROOM_CONSTRUCTIVE:
        boost += ROOM_CONSTRUCTIVE_PTS
    elif room < ROOM_TIGHT:
        boost -= ROOM_TIGHT_PENALTY

    boost = min(boost, MAX_RIPPER_BOOST)

    if is_slow_popular(row):
        boost -= SLOW_POPULAR_PENALTY

    prior = _finite(row.get("ticker_prior_hit1r_rate")) or 0.0
    if prior >= HIST_HIT_HIGH and not is_tape_hot(row):
        boost -= HIST_WITHOUT_TAPE_PENALTY

    if int(row.get("micro_dead_tape") or 0) == 1:
        boost -= DEAD_TAPE_EXTRA_PENALTY

    return round(boost, 2)


def apply_momentum_rank(score: float, row: dict[str, Any]) -> float:
    """Return score + overlay delta (caller checks the flag)."""
    return round(float(score) + momentum_rank_delta(row), 2)


def annotate_momentum_rank(row: dict[str, Any]) -> dict[str, Any]:
    """Attach tape_hot / slow_popular / delta telemetry (always; cheap)."""
    out = dict(row)
    out["tape_hot"] = is_tape_hot(out)
    out["slow_popular"] = is_slow_popular(out)
    out["momentum_rank_delta"] = momentum_rank_delta(out)
    out["momentum_rank"] = momentum_rank_enabled()
    return out


def take_eligible(row: dict[str, Any]) -> bool:
    """
    Slot eligibility after case ENTER (overlay ON).

    Popularity remains a lane, not the only lane: a first-day ripper with
    momentum_context + hot tape can take without multi-day board membership.
    """
    popular = bool(
        row.get("tradable_popular")
        or row.get("recent_leaderboard")
        or row.get("on_gainers")
    )
    if popular:
        return True
    return bool(row.get("momentum_context")) and is_tape_hot(row)


def take_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    """
    Rank takes by momentum-adjusted continuation; case confidence is tiebreak.

    Overlay ON replaces confidence-first sort so a high-confidence slow
    popular name cannot beat a ripper.
    """
    score = float(
        row.get("combined_rank_score")
        or row.get("continuation_score")
        or 0.0
    )
    conf = float(row.get("case_confidence") or 0.0)
    return (-score, -conf)


def ranker_suffix() -> str:
    """Version-label fragment when overlay is ON."""
    return "+momentum_rank" if momentum_rank_enabled() else ""
