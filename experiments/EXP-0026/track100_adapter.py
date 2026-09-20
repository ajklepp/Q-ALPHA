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
# Fallback only when paper_filter.py has no IS constant. The study book split
# is Aaron-locked in php_t100_wf_engine.IS_CUT_DEFAULT (2026-09-03 mini WF).
IS_CUT_DEFAULT = "2026-09-03"

# paper_filter.py does `from features import ...`. These must sit next to it
# on the Modal image (VENDOR.glob('*.py')). Missing features.py is the
# confirmed ModuleNotFoundError after ~8232 Peak Hour signals.
IMPORT_CLOSURE = (
    "paper_filter.py",
    "paper_exit.py",
    "trail_exits.py",
    "features.py",
    "playbook.py",
    "wave.py",
    "backtest.py",
    "leverage.py",
)


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
    feat = resolve_track100_file("features.py")
    closure = {n: (str(p) if (p := resolve_track100_file(n)) else None) for n in IMPORT_CLOSURE}
    missing = [n for n, p in closure.items() if p is None]
    return {
        "paper_filter": str(filt) if filt else None,
        "paper_exit": str(exit_py) if exit_py else None,
        "trail_exits": str(trail) if trail else None,
        "features": str(feat) if feat else None,
        "filter_ok": filt is not None,
        "exit_ok": exit_py is not None or trail is not None,
        "features_ok": feat is not None,
        "closure": closure,
        "closure_missing": missing,
        # Fail closed if features.py is absent — otherwise Modal scans ~8k
        # signals then dies in load_filter_module with ModuleNotFoundError.
        "ready": (
            filt is not None
            and (exit_py is not None or trail is not None)
            and feat is not None
        ),
        "search_roots": [str(p) for p in track100_search_roots()],
    }


def _ensure_track100_sys_path(module_path: Path) -> None:
    """
    Insert vendor_track100 (then the loaded file's directory) at sys.path[0]
    BEFORE exec'ing paper_filter.py / exit modules.

    WHY: vendored `paper_filter.py` does
        from features import daily_feature_dict, enrich_features_1h,
            enrich_features_daily, row_to_feature_dict
    On Modal the image only has `/pkg/exp0026/vendor_track100/*.py`. Without
    this insert, `import features` raises ModuleNotFoundError (confirmed after
    ~8232 Peak Hour signals). Q-ALPHA's root `features.py` must not win either.
    """
    dirs: list[str] = []
    for candidate in (VENDOR, module_path.parent):
        try:
            root = str(Path(candidate).resolve())
        except Exception:
            continue
        if root not in dirs:
            dirs.append(root)
    # Insert last-first so VENDOR is sys.path[0].
    for root in reversed(dirs):
        while root in sys.path:
            sys.path.remove(root)
        sys.path.insert(0, root)


def _load_module(path: Path, name: str) -> Any:
    """Load a Track 100 .py with vendor_track100 on sys.path (import siblings)."""
    _ensure_track100_sys_path(path)
    # Drop Q-ALPHA `features` shadow and the target module so vendor siblings
    # resolve. Do NOT pop already-preloaded exit siblings (trail_exits) —
    # paper_exit does `from trail_exits import ...`.
    sys.modules.pop("features", None)
    sys.modules.pop(name, None)
    sys.modules.pop(path.stem, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Track100Missing(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    # Also register under the short name Track 100 code expects.
    short = path.stem
    sys.modules[short] = mod
    spec.loader.exec_module(mod)
    return mod


def load_filter_module() -> Any:
    """Import vendored / sibling paper_filter.py with vendor dir on sys.path."""
    path = resolve_track100_file("paper_filter.py")
    if path is None:
        raise Track100Missing(
            "paper_filter.py not found. Run vendor_from_track100.py "
            "or set TRACK100_ROOT."
        )
    if resolve_track100_file("features.py") is None:
        raise Track100Missing(
            "features.py not found next to paper_filter.py. "
            "Re-run vendor_from_track100.py (COPY_NAMES includes features.py)."
        )
    # Explicit vendor insert before exec — paper_filter imports `features`.
    _ensure_track100_sys_path(path)
    return _load_module(path, "exp0026_paper_filter")


def load_exit_module() -> Any:
    """Import paper_exit.py (preferred) and ensure trail_exits is loadable."""
    trail_path = resolve_track100_file("trail_exits.py")
    if trail_path is not None:
        # Vendor dir on sys.path so exit siblings (`features`, `trail_exits`) resolve.
        _ensure_track100_sys_path(trail_path)
        # Preload so paper_exit's `from trail_exits import ...` resolves.
        _load_module(trail_path, "exp0026_trail_exits")
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
    """IS cut date string from paper_filter, else study default 2026-09-03."""
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


def apply_paper_filter_winloss_v1(
    row: dict[str, Any],
    *,
    mod: Any | None = None,
    und_1h: Any | None = None,
    daily_en: Any | None = None,
    signal_i: int | None = None,
) -> FilterDecision:
    """
    Apply frozen paper_filter_winloss_v1 to a signal-time row.

    Prefers Track 100's real entry points:
      - extract_paper_filter_features + evaluate_paper_filter (when 1H/daily given)
      - evaluate_paper_filter(features_dict) when row already has frozen feature keys
    Does not invent thresholds.
    """
    filt = mod or load_filter_module()
    version = str(
        getattr(filt, "FILTER_VERSION", None)
        or getattr(filt, "FILTER_NAME", None)
        or getattr(filt, "PAPER_FILTER_NAME", None)
        or FILTER_VERSION
    )

    # Path A: build Track 100 features from causal 1H + daily frames.
    extract = getattr(filt, "extract_paper_filter_features", None)
    evaluate = getattr(filt, "evaluate_paper_filter", None)
    if callable(extract) and callable(evaluate) and und_1h is not None:
        h1 = und_1h
        daily = daily_en
        ensure_h1 = getattr(filt, "ensure_h1_filter_cols", None)
        ensure_d = getattr(filt, "ensure_daily_filter_cols", None)
        if callable(ensure_h1):
            h1 = ensure_h1(h1)
        if callable(ensure_d) and daily is not None:
            daily = ensure_d(daily)
        idx = signal_i
        if idx is None:
            idx = _signal_index(h1, row.get("signal_ts"))
        if idx is None:
            return FilterDecision(
                passed=False,
                reason="missing_signal_bar",
                version=version,
                details={"signal_ts": row.get("signal_ts")},
            )
        feats = extract(und_1h=h1, signal_i=int(idx), daily_en=daily)
        return _normalize_filter(evaluate(feats), version=version)

    # Path B: evaluate_paper_filter on a feature dict (missing keys fail closed).
    if callable(evaluate):
        feat_keys = getattr(filt, "PAPER_FILTER_FEATURES", None) or (
            "h1_ema50_chg_5",
            "d_px_ema200",
            "d_sma50_200_spread",
            "d_sma200_chg_5",
            "d_ema200_chg_5",
        )
        # Prefer explicit frozen-feature payload; otherwise pass row through
        # (missing / non-finite → fail that rule — Track 100 contract).
        feats = {k: row.get(k) for k in feat_keys} if any(k in row for k in feat_keys) else dict(row)
        return _normalize_filter(evaluate(feats), version=version)

    # Path C: legacy / alternate names (tests, older modules).
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

    evaluate_generic = getattr(filt, "evaluate", None) or getattr(filt, "apply_filter", None)
    if callable(evaluate_generic):
        try:
            raw = evaluate_generic(row, version=FILTER_VERSION)
        except TypeError:
            try:
                raw = evaluate_generic(row, FILTER_VERSION)
            except TypeError:
                raw = evaluate_generic(row)
        return _normalize_filter(raw, version=version)

    passes = getattr(filt, "passes") if callable(getattr(filt, "passes", None)) else None
    if passes is None:
        passes = getattr(filt, "pass_filter", None)
    if callable(passes):
        return _normalize_filter(passes(row), version=version)

    raise Track100Missing(
        "paper_filter.py loaded but no evaluate_paper_filter / "
        "paper_filter_winloss_v1 entry point. Do not invent a filter."
    )


def _signal_index(df: Any, signal_ts: Any) -> int | None:
    """Locate signal bar index on a 1H frame (ET timestamps)."""
    if df is None or signal_ts is None or len(df) == 0:
        return None
    try:
        import pandas as pd

        target = pd.Timestamp(signal_ts)
        idx = df.index
        if getattr(idx, "tz", None) is not None and target.tzinfo is None:
            target = target.tz_localize(idx.tz)
        elif getattr(idx, "tz", None) is not None and target.tzinfo is not None:
            target = target.tz_convert(idx.tz)
        if target in idx:
            return int(idx.get_loc(target))
        # Nearest prior bar (causal).
        prior = idx[idx <= target]
        if len(prior) == 0:
            return None
        return int(idx.get_loc(prior[-1]))
    except Exception:
        return None


def _normalize_filter(raw: Any, *, version: str) -> FilterDecision:
    if isinstance(raw, FilterDecision):
        return raw
    # Track 100 PaperFilterDecision dataclass
    if hasattr(raw, "passed") and hasattr(raw, "failed"):
        failed = getattr(raw, "failed", None) or []
        if failed:
            first = failed[0]
            reason = str(
                getattr(first, "feature", None)
                or getattr(first, "label", None)
                or getattr(first, "why", None)
                or "reject"
            )
        else:
            reason = "pass"
        details: dict[str, Any] = {}
        if hasattr(raw, "features"):
            details["features"] = dict(getattr(raw, "features") or {})
        if hasattr(raw, "as_dict") and callable(raw.as_dict):
            details.update(raw.as_dict())
        elif failed:
            details["failed_features"] = [
                (f.as_dict() if hasattr(f, "as_dict") else str(f)) for f in failed
            ]
        return FilterDecision(
            passed=bool(raw.passed), reason=reason, version=version, details=details,
        )
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
        "simulate_paper_exit",
        "measure_exit_path",
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


def _bars_to_frame(bars: list[dict[str, Any]]) -> Any:
    """OHLC path list → DataFrame for Track 100 measure_exit_path."""
    import pandas as pd

    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    df = pd.DataFrame(bars)
    if "ts" in df.columns:
        df = df.set_index(pd.to_datetime(df["ts"]))
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise Track100Missing(f"exit path missing column {col}")
    return df


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

    Binds to trail_exits.measure_exit_path + book_c_ratchet (or
    paper_exit.simulate_paper_exit). Does not invent ratchet math.
    `structure_level` is accepted for Peak Hour compatibility; Track 100 C
    uses its own structure_lookback on the path.
    """
    del structure_level, signal_row  # Peak Hour args; C book ignores explicit level
    exit_mod = mod or load_exit_module()
    df = _bars_to_frame(bars)
    if df is None or len(df) == 0:
        return None

    # Preferred: paper_exit.simulate_paper_exit(df, entry_i=0, entry=..., end_i=len-1)
    sim_paper = getattr(exit_mod, "simulate_paper_exit", None)
    if callable(sim_paper):
        raw = sim_paper(
            df,
            entry_i=0,
            entry=float(entry),
            end_i=len(df) - 1,
            paper_exit=EXIT_BOOK,
        )
        return _normalize_exit(raw)

    # Next: measure_exit_path + book_c_ratchet on trail_exits / paper_exit.
    measure = getattr(exit_mod, "measure_exit_path", None)
    book_fn = getattr(exit_mod, "book_c_ratchet", None) or getattr(
        exit_mod, "paper_exit_book", None
    )
    if measure is None or book_fn is None:
        trail_path = resolve_track100_file("trail_exits.py")
        if trail_path is not None:
            trail = _load_module(trail_path, "exp0026_trail_exits")
            measure = measure or getattr(trail, "measure_exit_path", None)
            book_fn = book_fn or getattr(trail, "book_c_ratchet", None)
    if callable(measure) and callable(book_fn):
        book = book_fn()
        # paper_exit_book / book_c_ratchet return ExitBook; tolerate nested callables.
        if not hasattr(book, "horizon_bars") and callable(book):
            book = book()
        measured = measure(df, 0, book=book, entry=float(entry), cost=0.0)
        if not isinstance(measured, dict) or not measured.get("valid"):
            return None
        exit_i = int(measured.get("exit_i", len(df) - 1))
        ts = ""
        try:
            ts = str(df.index[exit_i])
        except Exception:
            ts = ""
        return ExitFill(
            exit_px=float(measured["exit"]),
            reason=str(measured.get("exit_reason") or measured.get("reason") or "c_ratchet"),
            bar_ts=ts,
            extra=measured,
        )

    # Legacy generic binding (tests / alternate modules).
    fn = _find_c_ratchet_callable(exit_mod)
    kwargs_list = [
        dict(
            bars=bars,
            entry=entry,
            structure_level=None,
            signal_row={},
        ),
        dict(bars=bars, entry=entry, structure_level=None),
        dict(ohlc=bars, entry=entry, structure=None),
        dict(path=bars, entry_px=entry, area_low=None),
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
            raw = fn(bars, entry, None)
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
            bar_ts=str(
                raw.get("ts")
                or raw.get("bar_ts")
                or raw.get("exit_ts")
                or raw.get("closed_et")
                or ""
            ),
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
