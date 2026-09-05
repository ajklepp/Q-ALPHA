"""
EXP-0021 social + news — re-exports live canonical module.

Keep research and live scoring on one implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CAND = _ROOT / "candidates"
if str(_CAND) not in sys.path:
    sys.path.insert(0, str(_CAND))

from tsd_scan_pipeline.tsd_social import (  # noqa: E402,F401
    attach_social_to_rows,
    fetch_polygon_news_velocity,
    fetch_social_bundle,
    fetch_stocktwits,
    fetch_x_recent,
)
