"""
Pure A-book and B-tape feature math.

WHY: TEST-01 and the L2/tape plan need deterministic features with no
look-ahead. All functions are IB-free so unit tests can use fakes.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .constants import (
    BOOK_DELTA_1S,
    BOOK_DELTA_5S,
    LARGE_PRINT_K,
    LARGE_PRINT_MEDIAN_MIN_N,
    PRINT_IMB_WINDOW_SEC,
    PRINT_MEDIAN_LOOKBACK_SEC,
    PRINT_MEDIAN_MAX_N,
    PRINT_VWAP_WINDOW_SEC,
    QUOTE_FLICKER_WINDOW_SEC,
    TAPE_BURST_BASELINE_SEC,
    TAPE_BURST_BUCKET_SEC,
    TAPE_BURST_MIN_BUCKETS,
    UPTICK_WINDOW_SEC,
)


def _finite(value: Any) -> float | None:
    """Parse a number; reject NaN/inf/non-numeric."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _ts_epoch(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def mid_price(bid1: float | None, ask1: float | None) -> float | None:
    """Simple mid. None if either side is missing or crossed/invalid."""
    if bid1 is None or ask1 is None:
        return None
    if bid1 <= 0 or ask1 <= 0 or ask1 < bid1:
        return None
    return (bid1 + ask1) / 2.0


def microprice(
    bid1: float | None,
    ask1: float | None,
    bid_sz1: float | None,
    ask_sz1: float | None,
) -> float | None:
    """
    Size-weighted microprice: (bid*ask_sz + ask*bid_sz) / (bid_sz + ask_sz).

    WHY: weights the quote toward the thinner side — standard L1 microprice.
    """
    if bid1 is None or ask1 is None or bid_sz1 is None or ask_sz1 is None:
        return None
    if bid1 <= 0 or ask1 <= 0 or bid_sz1 < 0 or ask_sz1 < 0:
        return None
    denom = bid_sz1 + ask_sz1
    if denom <= 0:
        return None
    return (bid1 * ask_sz1 + ask1 * bid_sz1) / denom


def spread_bps(bid1: float | None, ask1: float | None, mid: float | None) -> float | None:
    """Quoted spread in basis points of mid."""
    if bid1 is None or ask1 is None or mid is None or mid <= 0:
        return None
    if ask1 < bid1:
        return None
    return (ask1 - bid1) / mid * 10_000.0


def imbalance(bid_sz: float | None, ask_sz: float | None) -> float | None:
    """Signed size imbalance in [-1, 1]: (bid - ask) / (bid + ask)."""
    if bid_sz is None or ask_sz is None:
        return None
    if bid_sz < 0 or ask_sz < 0:
        return None
    denom = bid_sz + ask_sz
    if denom <= 0:
        return None
    return (bid_sz - ask_sz) / denom


def imbalance_l3(
    bid_sizes: list[float],
    ask_sizes: list[float],
) -> float | None:
    """L3 imbalance from the first three levels; None if depth < 3 on either side."""
    if len(bid_sizes) < 3 or len(ask_sizes) < 3:
        return None
    return imbalance(sum(bid_sizes[:3]), sum(ask_sizes[:3]))


def book_pressure(bid_sizes: list[float], ask_sizes: list[float]) -> float | None:
    """
    Signed book pressure using available levels (same formula as imbalance).

    WHY: delta of this series is book_pressure_delta_{1,5}s.
    """
    if not bid_sizes or not ask_sizes:
        return None
    return imbalance(sum(bid_sizes), sum(ask_sizes))


def classify_print_side(
    price: float,
    *,
    mid: float | None,
    prev_price: float | None,
    last_tick_sign: int,
) -> int:
    """
    Lee-Ready if mid is known; otherwise tick rule.

    Returns +1 (buy / uptick), -1 (sell / downtick), or last_tick_sign (0 if none).
    """
    if mid is not None:
        if price > mid:
            return 1
        if price < mid:
            return -1
    if prev_price is not None:
        if price > prev_price:
            return 1
        if price < prev_price:
            return -1
    return last_tick_sign


@dataclass(frozen=True)
class BookLevel:
    """One depth level (price + size)."""

    price: float
    size: float


@dataclass
class BookHistory:
    """Rolling mid/pressure history for 1s/5s deltas and quote flicker."""

    pressure: deque[tuple[float, float]] = field(default_factory=deque)
    mids: deque[tuple[float, float]] = field(default_factory=deque)

    def observe(self, ts: datetime, pressure: float | None, mid: float | None) -> None:
        """Record one snapshot; drop points older than the 5s feature window + slack."""
        epoch = _ts_epoch(ts)
        cutoff = epoch - (BOOK_DELTA_5S + 2.0)
        if pressure is not None:
            self.pressure.append((epoch, pressure))
        if mid is not None:
            self.mids.append((epoch, mid))
        while self.pressure and self.pressure[0][0] < cutoff:
            self.pressure.popleft()
        while self.mids and self.mids[0][0] < cutoff:
            self.mids.popleft()

    def _delta_at(self, series: deque[tuple[float, float]], now: datetime, lag_sec: float) -> float | None:
        if not series:
            return None
        epoch = _ts_epoch(now)
        current = series[-1][1]
        target = epoch - lag_sec
        prior = None
        for t, val in series:
            if t <= target + 1e-9:
                prior = val
            else:
                break
        if prior is None:
            return None
        return current - prior

    def pressure_delta(self, now: datetime, lag_sec: float) -> float | None:
        """Change in book pressure versus the last sample at or before now-lag."""
        return self._delta_at(self.pressure, now, lag_sec)

    def quote_flicker(self, now: datetime, window_sec: float = QUOTE_FLICKER_WINDOW_SEC) -> float | None:
        """
        Mid flip-rate proxy: direction reversals of mid changes / window seconds.

        WHY: flicker is a quote-stability proxy without claiming exchange message rates.
        """
        if len(self.mids) < 3:
            return None
        epoch = _ts_epoch(now)
        window = [(t, m) for t, m in self.mids if t >= epoch - window_sec]
        if len(window) < 3:
            return None
        flips = 0
        prev_sign = 0
        for (_, m0), (_, m1) in zip(window, window[1:]):
            delta = m1 - m0
            if delta > 0:
                sign = 1
            elif delta < 0:
                sign = -1
            else:
                continue
            if prev_sign != 0 and sign != prev_sign:
                flips += 1
            prev_sign = sign
        return flips / window_sec


@dataclass(frozen=True)
class TapePrint:
    """One trade print. side: +1 buy, -1 sell, 0 unknown."""

    ts: datetime
    price: float
    size: float
    side: int


@dataclass
class TapeEngine:
    """
    Event / bucket tape features (B).

    Warm-up: tape_burst_z is null until TAPE_BURST_MIN_BUCKETS of 5s volume
    exist. Baseline then expands up to ~20 minutes.
    """

    prints: deque[TapePrint] = field(default_factory=deque)
    buckets: deque[tuple[float, float]] = field(default_factory=deque)
    _bucket_start: float | None = None
    _bucket_vol: float = 0.0
    _last_price: float | None = None
    _last_tick_sign: int = 0

    def add_print(
        self,
        ts: datetime,
        price: float,
        size: float,
        *,
        mid: float | None = None,
        side: int | None = None,
    ) -> TapePrint | None:
        """Ingest one print; classify side if the feed did not provide it."""
        px = _finite(price)
        sz = _finite(size)
        if px is None or sz is None or px <= 0 or sz <= 0:
            return None
        if side in (1, -1):
            signed = side
        else:
            signed = classify_print_side(
                px,
                mid=mid,
                prev_price=self._last_price,
                last_tick_sign=self._last_tick_sign,
            )
        if signed in (1, -1):
            self._last_tick_sign = signed
        rec = TapePrint(ts=ts, price=px, size=sz, side=signed)
        self.prints.append(rec)
        self._last_price = px
        self._accrue_bucket(_ts_epoch(ts), sz)
        self._trim(ts)
        return rec

    def _accrue_bucket(self, epoch: float, size: float) -> None:
        """Accumulate print size into 5s buckets for tape_burst_z."""
        if self._bucket_start is None:
            self._bucket_start = epoch
            self._bucket_vol = 0.0
        while epoch - self._bucket_start >= TAPE_BURST_BUCKET_SEC:
            self.buckets.append((self._bucket_start, self._bucket_vol))
            self._bucket_start += TAPE_BURST_BUCKET_SEC
            self._bucket_vol = 0.0
            cutoff = epoch - TAPE_BURST_BASELINE_SEC
            while self.buckets and self.buckets[0][0] < cutoff:
                self.buckets.popleft()
        self._bucket_vol += size

    def flush_bucket(self, now: datetime) -> None:
        """Close completed 5s buckets even if no new print arrived."""
        if self._bucket_start is None:
            return
        epoch = _ts_epoch(now)
        while epoch - self._bucket_start >= TAPE_BURST_BUCKET_SEC:
            self.buckets.append((self._bucket_start, self._bucket_vol))
            self._bucket_start += TAPE_BURST_BUCKET_SEC
            self._bucket_vol = 0.0
            cutoff = epoch - TAPE_BURST_BASELINE_SEC
            while self.buckets and self.buckets[0][0] < cutoff:
                self.buckets.popleft()

    def _trim(self, now: datetime) -> None:
        cutoff = _ts_epoch(now) - max(PRINT_MEDIAN_LOOKBACK_SEC, UPTICK_WINDOW_SEC)
        while self.prints and _ts_epoch(self.prints[0].ts) < cutoff:
            self.prints.popleft()
        while len(self.prints) > PRINT_MEDIAN_MAX_N:
            self.prints.popleft()

    def _window(self, now: datetime, sec: float) -> list[TapePrint]:
        start = _ts_epoch(now) - sec
        return [p for p in self.prints if _ts_epoch(p.ts) >= start]

    def print_vwap(self, now: datetime, sec: float = PRINT_VWAP_WINDOW_SEC) -> float | None:
        """Volume-weighted print price over the window; None if no size."""
        rows = self._window(now, sec)
        notional = sum(p.price * p.size for p in rows)
        volume = sum(p.size for p in rows)
        if volume <= 0:
            return None
        return notional / volume

    def print_imbalance(self, now: datetime, sec: float = PRINT_IMB_WINDOW_SEC) -> float | None:
        """
        Signed print-volume imbalance over the window.

        Unclassified (side=0) size is excluded from both sides.
        None if no classified volume.
        """
        rows = self._window(now, sec)
        buy = sum(p.size for p in rows if p.side > 0)
        sell = sum(p.size for p in rows if p.side < 0)
        denom = buy + sell
        if denom <= 0:
            return None
        return (buy - sell) / denom

    def uptick_ratio(self, now: datetime, sec: float = UPTICK_WINDOW_SEC) -> float | None:
        """Share of classified prints that are upticks / buys over 30s."""
        rows = self._window(now, sec)
        up = sum(1 for p in rows if p.side > 0)
        down = sum(1 for p in rows if p.side < 0)
        denom = up + down
        if denom <= 0:
            return None
        return up / denom

    def large_print_flag(self, now: datetime) -> int | None:
        """
        1 if the latest print size >= k * median size in the lookback.

        None until LARGE_PRINT_MEDIAN_MIN_N prints exist (warm-up).
        """
        self._trim(now)
        if len(self.prints) < LARGE_PRINT_MEDIAN_MIN_N:
            return None
        sizes = [p.size for p in self.prints]
        median = statistics.median(sizes)
        if median <= 0:
            return None
        return 1 if self.prints[-1].size >= LARGE_PRINT_K * median else 0

    def tape_burst_z(self, now: datetime) -> float | None:
        """
        Z-score of the current 5s volume vs a ~20m rolling baseline.

        Warm-up: None until TAPE_BURST_MIN_BUCKETS completed buckets.
        """
        self.flush_bucket(now)
        if len(self.buckets) < TAPE_BURST_MIN_BUCKETS:
            return None
        vols = [v for _, v in self.buckets]
        if len(vols) < 2:
            return None
        mean = statistics.fmean(vols)
        stdev = statistics.stdev(vols)
        # Zero-variance baseline (flat tape): keep a tiny denom so a real
        # burst is still a large positive z instead of a silent 0.0.
        if stdev <= 1e-12:
            stdev = 1e-9
        current = self._bucket_vol
        # Mid-bucket: compare the in-progress 5s volume to the closed baseline.
        return (current - mean) / stdev

    def features(self, now: datetime) -> dict[str, Any]:
        """B-tape fields for a JSONL row (nulls during warm-up)."""
        return {
            "print_vwap_5s": self.print_vwap(now),
            "print_imb_5s": self.print_imbalance(now),
            "large_print_flag": self.large_print_flag(now),
            "uptick_ratio_30s": self.uptick_ratio(now),
            "tape_burst_z": self.tape_burst_z(now),
        }


def book_features(
    *,
    ts: datetime,
    bids: list[BookLevel],
    asks: list[BookLevel],
    history: BookHistory,
) -> dict[str, Any]:
    """
    A-book snapshot features plus history-dependent deltas / flicker.

    imbalance_l3 is null unless both sides have at least 3 levels.
    """
    bid1 = bids[0].price if bids else None
    ask1 = asks[0].price if asks else None
    bid_sz1 = bids[0].size if bids else None
    ask_sz1 = asks[0].size if asks else None
    mid = mid_price(bid1, ask1)
    bid_sizes = [lvl.size for lvl in bids]
    ask_sizes = [lvl.size for lvl in asks]
    pressure = book_pressure(bid_sizes, ask_sizes)
    history.observe(ts, pressure, mid)
    levels_n = max(len(bids), len(asks)) if (bids or asks) else 0
    return {
        "bid1": bid1,
        "ask1": ask1,
        "bid_sz1": bid_sz1,
        "ask_sz1": ask_sz1,
        "mid": mid,
        "microprice": microprice(bid1, ask1, bid_sz1, ask_sz1),
        "spread_bps": spread_bps(bid1, ask1, mid),
        "imbalance_l1": imbalance(bid_sz1, ask_sz1),
        "imbalance_l3": imbalance_l3(bid_sizes, ask_sizes),
        "book_pressure_delta_1s": history.pressure_delta(ts, BOOK_DELTA_1S),
        "book_pressure_delta_5s": history.pressure_delta(ts, BOOK_DELTA_5S),
        "quote_flicker": history.quote_flicker(ts),
        "levels_n": levels_n,
    }
