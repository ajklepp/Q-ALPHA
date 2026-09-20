"""
Load frozen Track 100 paper_filter_winloss_v1 + C_ratchet_struct.

Does not invent IS medians or ratchet constants. Discovers the functions
already frozen in track-100 `paper_filter.py` and `paper_exit.py` /
`trail_exits.py` (vendored or TRACK100_ROOT).
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

EXP_DIR = Path(__file__).resolve().parent
VENDOR = EXP_DIR / "vendor_track100"
REPO = EXP_DIR.parents[1]

FILTER_VERSION = "paper_filter_winloss_v1"
EXIT_BOOK = "C_ratchet_struct"
IS_CUT_DEFAULT = "2026-07-20"


@dataclass
class FilterDecision:
    """One paper_filter_winloss_v1 decision at signal time."""

    passed: bool
    reason: str
    version: str = FILTER_VERSION
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitFill:
    """Terminal exit from C_ratchet_struct."""

    exit_px: float
    reason: str
    bar_ts: str
    extra: dict[str, Any] = field(default_factory=dict)


class Track100Missing(RuntimeError):
    """Raised when frozen Track 100 modules are not on disk."""


def track100_search_roots() -> list[Path]:
    """Ordered roots that may contain paper_filter.py / exit modules."""
    out: list[Path] = [VENDOR]
    env = (os.environ.get("TRACK100_ROOT") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    for name in ("track-100", "Track 100", "Track100"):
        out.append(REPO.parent / name)
        out.append(REPO / name)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def resolve_track100_file(filename: str) -> Path | None:
    """First existing named file under search roots (and common subfolders)."""
    for root in track100_search_roots():
        for sub in ("", "src", "paper", "ops", "lib"):
            path = (root / sub / filename) if sub else (root / filename)
            if path.is_file():
                return path
    return None


def track100_available() -> dict[str, Any]:
    """Telemetry: which frozen files are visible (no import yet)."""
    filt = resolve_track100_file("paper_filter.py")
    exit_py = resolve_track100_file("paper_exit.py")
    trail = resolve_track100_file("trail_exits.py")
    return {
        "paper_filter": str(filt) if filt else None,
        "paper_exit": str(exit_py) if exit_py else None,
        "trail_exits": str(trail) if trail else None,
        "filter_ok": filt is not None,
        "exit_ok": exit_py is not None or trail is not None,
        "ready": filt is not None and (exit_py is not None or trail is not None),
        "search_roots": [str(p) for p in track100_search_roots()],
    }


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Track100Missing(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_filter_module() -> Any:
    """Import vendored / sibling paper_filter.py."""
    path = resolve_track100_file("paper_filter.py")
    if path is None:
        raise Track100Missing(
            "paper_filter.py not found. Run vendor_from_track100.py "
            "or set TRACK100_ROOT."
        )
    return _load_module(path, "exp0026_paper_filter")


def load_exit_module() -> Any:
    """Import paper_exit.py, else trail_exits.py."""
    for fname, modname in (
        ("paper_exit.py", "exp0026_paper_exit"),
        ("trail_exits.py", "exp0026_trail_exits"),
    ):
        path = resolve_track100_file(fname)
        if path is not None:
            return _load_module(path, modname)
    raise Track100Missing(
        "paper_exit.py / trail_exits.py not found. Run vendor_from_track100.py "
        "or set TRACK100_ROOT."
    )


def _call_first(obj: Any, names: list[str], *args, **kwargs) -> Any:
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    raise AttributeError(f"none of {names} found on {obj}")


def frozen_is_cut(mod: Any | None = None) -> str:
    """IS cut date string from paper_filter, else Track 100 default 2026-07-20."""
    if mod is None:
        try:
            mod = load_filter_module()
        except Track100Missing:
            return IS_CUT_DEFAULT
    for name in ("IS_CUT", "IS_END", "IS_SIGNAL_END", "FROZEN_IS_CUT", "OOS_START"):
        val = getattr(mod, name, None)
        if val is None:
            continue
        text = str(val)
        if len(text) >= 10 and text[4] == "-":
            # OOS_START is the day after IS — caller wants IS inclusive end.
            if name == "OOS_START":
                return text[:10]
            return text[:10]
    return IS_CUT_DEFAULT


def apply_paper_filter_winloss_v1(row: dict[str, Any], *, mod: Any | None = None) -> FilterDecision:
    """
    Apply frozen paper_filter_winloss_v1 to a signal-time row.

    Tries the common Track 100 entry points without guessing thresholds.
    """
    filt = mod or load_filter_module()
    version = str(
        getattr(filt, "FILTER_VERSION", None)
        or getattr(filt, "FILTER_NAME", None)
        or FILTER_VERSION
    )

    # Preferred: explicit versioned function / evaluate(version=...).
    for name in (
        "paper_filter_winloss_v1",
        "apply_paper_filter_winloss_v1",
        "filter_winloss_v1",
        "evaluate_winloss_v1",
        "passes_winloss_v1",
    ):
        fn = getattr(filt, name, None)
        if callable(fn):
            return _normalize_filter(fn(row), version=version)

    evaluate = getattr(filt, "evaluate", None) or getattr(filt, "apply_filter", None)
    if callable(evaluate):
        try:
            raw = evaluate(row, version=FILTER_VERSION)
        except TypeError:
            try:
                raw = evaluate(row, FILTER_VERSION)
            except TypeError:
                raw = evaluate(row)
        return _normalize_filter(raw, version=version)

    passes = getattr(filt, "passes") if callable(getattr(filt, "passes", None)) else None
    if passes is None:
        passes = getattr(filt, "pass_filter", None)
    if callable(passes):
        return _normalize_filter(passes(row), version=version)

    raise Track100Missing(
        "paper_filter.py loaded but no paper_filter_winloss_v1 / evaluate / "
        "passes entry point. Do not invent a filter."
    )


def _normalize_filter(raw: Any, *, version: str) -> FilterDecision:
    if isinstance(raw, FilterDecision):
        return raw
    if isinstance(raw, tuple) and raw:
        ok = bool(raw[0])
        reason = str(raw[1]) if len(raw) > 1 else ("pass" if ok else "reject")
        extra = raw[2] if len(raw) > 2 and isinstance(raw[2], dict) else {}
        return FilterDecision(passed=ok, reason=reason, version=version, details=extra)
    if isinstance(raw, dict):
        ok = bool(raw.get("passed", raw.get("ok", raw.get("pass", False))))
        reason = str(raw.get("reason") or raw.get("why") or ("pass" if ok else "reject"))
        return FilterDecision(passed=ok, reason=reason, version=version, details=raw)
    return FilterDecision(passed=bool(raw), reason="pass" if raw else "reject", version=version)


def _find_c_ratchet_callable(mod: Any) -> Callable[..., Any]:
    """Locate the exact C_ratchet_struct simulator — no homemade fallback."""
    for name in (
        "C_ratchet_struct",
        "c_ratchet_struct",
        "simulate_c_ratchet_struct",
        "run_c_ratchet_struct",
        "apply_c_ratchet_struct",
        "book_c_ratchet_struct",
        "exit_c_ratchet_struct",
    ):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn

    books = getattr(mod, "BOOKS", None) or getattr(mod, "EXIT_BOOKS", None)
    if isinstance(books, dict):
        book = books.get(EXIT_BOOK) or books.get("C") or books.get("c_ratchet_struct")
        if callable(book):
            return book
        simulate = getattr(mod, "simulate", None) or getattr(mod, "simulate_book", None)
        if callable(simulate) and book is not None:
            def _wrapped(*args, **kwargs):
                kwargs.setdefault("book", book)
                try:
                    return simulate(*args, **kwargs)
                except TypeError:
                    return simulate(book, *args, **{k: v for k, v in kwargs.items() if k != "book"})
            return _wrapped

    simulate = getattr(mod, "simulate", None) or getattr(mod, "simulate_exit", None)
    if callable(simulate):
        def _named(*args, **kwargs):
            kwargs.setdefault("book_name", EXIT_BOOK)
            kwargs.setdefault("book", EXIT_BOOK)
            return simulate(*args, **kwargs)
        return _named

    raise Track100Missing(
        "exit module loaded but C_ratchet_struct callable not found. "
        "Do not invent ratchet math."
    )


def simulate_c_ratchet_struct(
    bars: list[dict[str, Any]],
    *,
    entry: float,
    structure_level: float | None,
    signal_row: dict[str, Any] | None = None,
    mod: Any | None = None,
) -> ExitFill | None:
    """
    Run the exact Track 100 C_ratchet_struct book on post-entry 1H bars.

    `bars` are causal subsequent 1H bars (including the fill bar) with
    keys: ts, open, high, low, close.
    Returns None if the book is still open at the last bar (caller marks).
    """
    exit_mod = mod or load_exit_module()
    fn = _find_c_ratchet_callable(exit_mod)

    kwargs_list = [
        dict(
            bars=bars,
            entry=entry,
            structure_level=structure_level,
            signal_row=signal_row or {},
        ),
        dict(bars=bars, entry=entry, structure_level=structure_level),
        dict(ohlc=bars, entry=entry, structure=structure_level),
        dict(path=bars, entry_px=entry, area_low=structure_level),
    ]
    raw = None
    last_err: Exception | None = None
    for kw in kwargs_list:
        try:
            raw = fn(**kw)
            break
        except TypeError as exc:
            last_err = exc
            continue
    if raw is None:
        try:
            raw = fn(bars, entry, structure_level)
        except TypeError as exc:
            raise Track100Missing(
                f"C_ratchet_struct callable signature not recognized: {exc}"
            ) from last_err or exc

    return _normalize_exit(raw)


def _normalize_exit(raw: Any) -> ExitFill | None:
    if raw is None:
        return None
    if isinstance(raw, ExitFill):
        return raw
    if isinstance(raw, tuple) and raw:
        if raw[0] is None:
            return None
        if len(raw) >= 2 and isinstance(raw[0], (int, float)):
            return ExitFill(
                exit_px=float(raw[0]),
                reason=str(raw[1]),
                bar_ts=str(raw[2]) if len(raw) > 2 else "",
            )
    if isinstance(raw, dict):
        if raw.get("still_open") or raw.get("open"):
            return None
        px = raw.get("exit_px", raw.get("exit", raw.get("exit_price")))
        if px is None:
            return None
        return ExitFill(
            exit_px=float(px),
            reason=str(raw.get("reason") or raw.get("exit_reason") or "c_ratchet"),
            bar_ts=str(raw.get("ts") or raw.get("bar_ts") or raw.get("exit_ts") or ""),
            extra=raw,
        )
    return None


def filter_skip_reason_counts(decisions: list[FilterDecision]) -> dict[str, int]:
    """Histogram of skip reasons (rejects only)."""
    out: dict[str, int] = {}
    for d in decisions:
        if d.passed:
            continue
        key = d.reason or "reject"
        out[key] = out.get(key, 0) + 1
    return out


def adapter_selftest() -> dict[str, Any]:
    """Import-time inventory for results.md (no P&L)."""
    info = track100_available()
    if not info["ready"]:
        return {**info, "imported": False}
    try:
        filt = load_filter_module()
        exit_mod = load_exit_module()
        fn = _find_c_ratchet_callable(exit_mod)
        info.update({
            "imported": True,
            "filter_module": getattr(filt, "__name__", ""),
            "filter_version": str(
                getattr(filt, "FILTER_VERSION", None)
                or getattr(filt, "FILTER_NAME", None)
                or FILTER_VERSION
            ),
            "is_cut": frozen_is_cut(filt),
            "exit_callable": getattr(fn, "__name__", str(fn)),
            "filter_public": [
                n for n, v in inspect.getmembers(filt) if not n.startswith("_")
            ][:40],
            "exit_public": [
                n for n, v in inspect.getmembers(exit_mod) if not n.startswith("_")
            ][:40],
        })
    except Exception as exc:
        info.update({"imported": False, "import_error": str(exc)[:300]})
    return info
