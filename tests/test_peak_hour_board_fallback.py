"""Peak Hour launches board prefers cloud opens/queue; falls back to local."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_live_status import _build_peak_hour_launch_board  # noqa: E402


def test_cloud_open_shows_without_fallback() -> None:
    rows, cap = _build_peak_hour_launch_board(
        [],
        [{"symbol": "HPE", "status": "OPEN", "scan_score": 50, "launch_score": 50, "shares": 4}],
        [],  # empty watchlist
    )
    assert cap is None
    assert any(r["Symbol"] == "HPE" and r["Status"] == "ENTERED" for r in rows)


def test_local_fallback_when_cloud_empty() -> None:
    local_q = [{
        "symbol": "HPE",
        "status": "WATCHING",
        "launch_score": 50,
        "htf_1h_bar_hour": 7,
        "buy_signal": True,
        "phase": "LAUNCH",
    }]
    local_o = [{"symbol": "HPE", "status": "ENTERED", "status_label": "ENTERED", "launch_score": 50}]
    with patch("dashboard_live_status._load_local_queue_rows", return_value=local_q), patch(
        "dashboard_live_status._load_local_open_board_rows", return_value=local_o
    ):
        rows, cap = _build_peak_hour_launch_board([], [], [])
    assert cap == "local fallback — Supabase lag"
    assert any(r["Symbol"] == "HPE" and r["Status"] == "ENTERED" for r in rows)


def test_ignores_legacy_watchlist() -> None:
    legacy = [{
        "symbol": "MFIC",
        "status_label": "Profiler OK",
        "rank": 1,
        "scan_score": 80,
    }]
    with patch("dashboard_live_status._load_local_queue_rows", return_value=[]), patch(
        "dashboard_live_status._load_local_open_board_rows", return_value=[]
    ):
        rows, cap = _build_peak_hour_launch_board([], [], legacy)
    assert rows == []
    assert cap is None


if __name__ == "__main__":
    test_cloud_open_shows_without_fallback()
    test_local_fallback_when_cloud_empty()
    test_ignores_legacy_watchlist()
    print("OK peak hour board fallback")
