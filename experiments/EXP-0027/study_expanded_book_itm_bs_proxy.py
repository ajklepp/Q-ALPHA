"""
EXP-0027 — ~8% ITM call model proxy on the Track 100 expanded-universe book.

Label: model_proxy_pnl
MODEL PROXY ONLY — NOT FILLS. Not gospel. Not broker fills. Not live paper.

Same Black–Scholes math as study_t100_fills_bs_proxy.py. The entry list is
the expanded half-equity tape (book expanded_half_equity_2), not the
ops-stack tape that printed about +33%. Stock dollars stay the tape's
dollars. One model call is overlaid on each closed fill.

Polygon option OHLC is not used. Stock bars price the underlying. IV is the
bridge when it is up, else 10-day realized vol, frozen backward.

If the trade file is missing, or it only has a summary (win rate / P&L and
no fills), this script writes NOT_RUN and does not invent dollars.

Laptop (one command, from the Q-ALPHA repo):

    cd C:\\Users\\ajkle\\Documents\\Q-ALPHA
    .\\experiments\\EXP-0027\\run_expanded_book_itm_bs_proxy.ps1

If C:\\Users\\ajkle\\Documents\\Track 100\\results has no
universe_expand_movers_trades.json or .csv, re-run the study first, in
Track 100, on the branch that contains the universe-expand code
(cursor/universe-expand-movers-3067):

    cd C:\\Users\\ajkle\\Documents\\Track 100
    py -3 -m modal run cloud/universe_expand_modal.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

import study_itm_bs_proxy as bs
import study_t100_fills_bs_proxy as tape

# Half of the $5k account is the published denominator's sibling. The
# Track 100 write-up quotes P&L as a percent of the full $5,000.
BOOK_USD = 5000.0
HALF_EQUITY_FRACTION = 0.5
MAX_SEATS = 2
# Used only when a fill records no notional. Two seats share the half book.
DEFAULT_SEAT_USD = BOOK_USD * HALF_EQUITY_FRACTION / MAX_SEATS

EXPANDED_BOOK = "expanded_half_equity_2"
BASELINE_BOOK = "baseline_half_equity_2"
FILTER_ON = "paper_filter_winloss_v1"
EXIT_MODEL = "C_ratchet_struct"
WINDOW_START = "2026-01-16"
WINDOW_END = "2026-09-16"

PREFERRED_NAMES = (
    "universe_expand_movers_trades.json",
    "universe_expand_movers_trades.csv",
)

_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results_expanded_book_itm_bs_proxy.md"
OUT_JSON = EXP_DIR / "results_expanded_book_itm_bs_proxy.json"

BANNER = tape.BANNER
LABEL = tape.LABEL

LAPTOP_PS = (
    "cd C:\\Users\\ajkle\\Documents\\Q-ALPHA\n"
    ".\\experiments\\EXP-0027\\run_expanded_book_itm_bs_proxy.ps1"
)
MODAL_RERUN = (
    "cd C:\\Users\\ajkle\\Documents\\Track 100\n"
    "py -3 -m modal run cloud/universe_expand_modal.py"
)
TRACK100_RESULTS = Path(r"C:\Users\ajkle\Documents\Track 100\results")

_TRADE_KEYS = {
    "symbol",
    "ticker",
    "traded",
    "entry",
    "entry_px",
    "entry_price",
    "opened_et",
    "entry_date",
    "entry_ts",
    "entry_time",
}
_LIST_KEYS = {"trades", "fills", "closed", "rows", "closed_trades"}
_SUMMARY_KEYS = {"pnl_usd", "win_rate", "n_closed", "pnl_pct", "return_pct", "n"}


def canonical_book(raw: str | None) -> str | None:
    """Map CLI names and tape labels onto the two half-equity books."""
    text = "".join(ch if ch.isalnum() else "_" for ch in str(raw or "").strip().lower())
    while "__" in text:
        text = text.replace("__", "_")
    text = text.strip("_")
    if not text:
        return None
    expanded_aliases = {
        "expanded",
        "expand",
        "expanded_half_equity",
        "expanded_half_equity_2",
        "expanded_half_equity_2_seats",
        "expanded_universe",
        "expanded_movers",
        "universe_expanded",
    }
    baseline_aliases = {
        "baseline",
        "baseline_half_equity",
        "baseline_half_equity_2",
        "baseline_half_equity_2_seats",
        "ops_stack",
    }
    if text in expanded_aliases or (text.startswith("expanded") and "baseline" not in text):
        return EXPANDED_BOOK
    if text in baseline_aliases or (text.startswith("baseline") and "expand" not in text):
        return BASELINE_BOOK
    return text


def _looks_like_trade(row: dict) -> bool:
    keys = {str(k).lower() for k in row}
    return bool(keys & _TRADE_KEYS)


def _looks_like_summary(row: dict) -> bool:
    if _looks_like_trade(row):
        return False
    keys = {str(k).lower() for k in row}
    return bool(keys & _SUMMARY_KEYS)


def _half_equity_name(raw: str | None) -> str | None:
    """Half-equity seat book, or None when the cell is an exit model.

    canonical_book returns the normalized text for unknown labels such as
    C_ratchet_struct. Those are not seat books.
    """
    canon = canonical_book(raw)
    if canon in {EXPANDED_BOOK, BASELINE_BOOK}:
        return canon
    return None


def _explicit_seat_book(row: dict) -> str | None:
    """Seat book on this row.

    Modal rows put the seat book in comparison (expanded_half_equity_2) and
    the exit book in book (C_ratchet_struct). comparison, book_name, variant,
    and seat_book win. book is used only when it is itself a half-equity name.
    """
    for key in ("comparison", "book_name", "variant", "seat_book", "book"):
        raw = tape._first(row, key)
        if raw in (None, ""):
            continue
        named = _half_equity_name(str(raw))
        if named:
            return named
    return None


def _row_book(row: dict, inherited: str | None) -> str | None:
    """Seat book for filtering. An exit-only book column does not count."""
    explicit = _explicit_seat_book(row)
    if explicit:
        return explicit
    return _half_equity_name(inherited) if inherited else None


def _stamp_inherited_book(row: dict, inherited_book: str | None) -> None:
    """Copy a parent seat book onto a row that does not already name one.

    Does not overwrite book when that column is the exit model.
    """
    if _explicit_seat_book(row):
        return
    inherited = _half_equity_name(inherited_book)
    if not inherited:
        return
    existing = tape._first(row, "book")
    if existing in (None, "") or _half_equity_name(str(existing)):
        row["book"] = inherited
    elif tape._first(row, "comparison") in (None, ""):
        row["comparison"] = inherited


def collect_trade_rows(obj: Any, inherited_book: str | None = None) -> list[dict]:
    """Pull fill dicts out of the JSON shapes the expand study may write.

    A book key such as expanded_half_equity_2 stamps rows that omit `book`.
    Summary objects (P&L, win rate, no symbol) are not fills.
    """
    found: list[dict] = []
    if isinstance(obj, list):
        trade_like = [item for item in obj if isinstance(item, dict) and _looks_like_trade(item)]
        if trade_like:
            for item in obj:
                if not isinstance(item, dict):
                    continue
                if not _looks_like_trade(item):
                    found.extend(collect_trade_rows(item, inherited_book))
                    continue
                row = dict(item)
                _stamp_inherited_book(row, inherited_book)
                found.append(row)
            return found
        for item in obj:
            found.extend(collect_trade_rows(item, inherited_book))
        return found
    if not isinstance(obj, dict):
        return found
    if _looks_like_trade(obj):
        row = dict(obj)
        _stamp_inherited_book(row, inherited_book)
        return [row]
    list_keys = [key for key in obj if str(key).lower() in _LIST_KEYS]
    if list_keys:
        parent = _row_book(obj, inherited_book) or inherited_book
        # One list only. A file that stores the same fills under two keys
        # would otherwise be counted twice.
        order = ("trades", "fills", "closed_trades", "closed", "rows")
        for name in order:
            for key in list_keys:
                if str(key).lower() != name:
                    continue
                got = collect_trade_rows(obj.get(key), parent)
                if got:
                    return got
    stamped = False
    for key, val in obj.items():
        canon = canonical_book(str(key))
        if canon in {EXPANDED_BOOK, BASELINE_BOOK}:
            found.extend(collect_trade_rows(val, canon))
            stamped = True
    if stamped:
        return found
    for val in obj.values():
        if isinstance(val, (dict, list)):
            found.extend(collect_trade_rows(val, inherited_book))
    return found


def collect_summaries(obj: Any, inherited_book: str | None = None) -> list[dict[str, Any]]:
    """Headline blobs that are not fills. Kept so a summary-only file is obvious."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(collect_summaries(item, inherited_book))
        return found
    if not isinstance(obj, dict):
        return found
    if _looks_like_summary(obj):
        book = _row_book(obj, inherited_book)
        found.append({"book": book, "summary": obj})
        return found
    parent = _row_book(obj, inherited_book) or inherited_book
    for key, val in obj.items():
        canon = canonical_book(str(key))
        next_book = canon if canon in {EXPANDED_BOOK, BASELINE_BOOK} else parent
        if isinstance(val, (dict, list)):
            found.extend(collect_summaries(val, next_book))
    return found


def _filter_ok(raw: dict) -> bool:
    """Keep the row when the tape does not say the win/loss filter was off.

    Missing columns mean the file was already sliced to filter ON.
    """
    flag = tape._first(raw, "filter", "paper_filter", "filter_name", "filter_id")
    state = tape._first(raw, "filter_on", "filter_state", "paper_filter_on")
    if flag is None and state is None:
        return True
    if state is not None and str(state).strip().lower() in {"0", "off", "false", "no"}:
        return False
    if flag is None:
        return True
    text = str(flag).strip().lower()
    if text in {"1", "on", "true", "yes", FILTER_ON.lower()}:
        return True
    if FILTER_ON.lower() in text and "off" not in text:
        return True
    return False


def _exit_ok(raw: dict) -> bool:
    """Keep C_ratchet_struct when an exit-model column exists. Reason is not the model."""
    flag = tape._first(raw, "exit_model", "exit_rule", "exit_profile", "struct_exit")
    if flag is None:
        return True
    text = str(flag).strip().lower()
    if not text:
        return True
    return "c_ratchet" in text


def normalize_expanded_row(raw: dict) -> dict[str, Any] | None:
    """Tape row plus the book label. Stock P&L is whatever the tape stored."""
    base = tape.normalize_trade(raw)
    if base is None:
        return None
    base["book"] = _row_book(raw, None)
    base["filter_name"] = str(tape._first(raw, "filter", "paper_filter", "filter_name") or "")
    base["exit_model"] = str(tape._first(raw, "exit_model", "exit_rule", "exit_profile", "struct_exit") or "")
    status = str(tape._first(raw, "status", "state") or "").strip().lower()
    base["tape_status"] = status
    if not base.get("seat_usd") or float(base["seat_usd"]) <= 0:
        base["seat_usd"] = DEFAULT_SEAT_USD
    return base


def _is_closed(row: dict[str, Any]) -> bool:
    if row.get("tape_status") in {"open", "working", "pending"}:
        return False
    return bool(row.get("exit_date")) and row.get("pnl_usd") is not None


def select_book_rows(raw_rows: list[dict], book: str) -> dict[str, Any]:
    """Keep one book. Unlabeled rows are not assigned, so the +33% tape cannot sneak in."""
    want = canonical_book(book) or EXPANDED_BOOK
    kept: list[dict[str, Any]] = []
    dropped = {"wrong_book": 0, "filter_off": 0, "other_exit": 0, "unlabeled": 0, "incomplete": 0}
    seen_books: set[str] = set()
    for raw in raw_rows:
        label = _row_book(raw, None)
        if label:
            seen_books.add(label)
        if label != want:
            if label is None:
                dropped["unlabeled"] += 1
            else:
                dropped["wrong_book"] += 1
            continue
        if not _filter_ok(raw):
            dropped["filter_off"] += 1
            continue
        if not _exit_ok(raw):
            dropped["other_exit"] += 1
            continue
        norm = normalize_expanded_row(raw)
        if norm is None:
            dropped["incomplete"] += 1
            continue
        norm["book"] = want
        norm["closed"] = _is_closed(norm)
        kept.append(norm)
    return {
        "book": want,
        "trades": kept,
        "dropped": dropped,
        "books_seen": sorted(seen_books),
    }


def parse_tape_payload(payload: Any, book: str, path: str | None = None) -> dict[str, Any]:
    """Normalize one JSON object or a list of CSV row dicts."""
    rows = collect_trade_rows(payload)
    summaries = collect_summaries(payload)
    selected = select_book_rows(rows, book)
    want = selected["book"]
    book_summaries = [row for row in summaries if row.get("book") in {None, want}]
    reason = None
    if not selected["trades"]:
        if book_summaries and not rows:
            reason = "summary_only_no_trade_list"
        elif rows and selected["dropped"]["unlabeled"] == len(rows):
            reason = "book_field_missing"
        elif rows:
            reason = "book_not_in_tape"
        else:
            reason = "tape_empty"
    return {
        "ok": bool(selected["trades"]),
        "reason": reason,
        "path": path,
        "book": want,
        "trades": selected["trades"],
        "dropped": selected["dropped"],
        "books_seen": selected["books_seen"],
        "summaries_ignored": book_summaries,
        "n_raw_rows": len(rows),
    }


def load_tape_file(path: Path, book: str) -> dict[str, Any]:
    """Read a CSV or JSON trade file. Does not score the ops-stack tape."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
        return parse_tape_payload(rows, book, str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    return parse_tape_payload(payload, book, str(path))


def _preferred_rank(path: Path) -> tuple[int, int, str]:
    """Trades filename first, JSON before CSV, then anything else."""
    name = path.name.lower()
    if name in PREFERRED_NAMES:
        kind = 0 if name.endswith(".json") else 1
        return (0, kind, name)
    if "universe_expand" in name and "trade" in name:
        kind = 0 if name.endswith(".json") else 1
        return (1, kind, name)
    return (5, 0, name)


def search_roots(explicit: Path | None = None) -> list[Path]:
    """TRACK100_TRADES, then the Track 100 results folder, then sibling checkouts."""
    roots: list[Path] = []
    if explicit is not None:
        roots.append(explicit)
    for key in ("TRACK100_TRADES", "T100_TRADES", "TRACK100_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            roots.append(Path(raw).expanduser())
    roots.extend(tape.track100_roots())
    seen: set[str] = set()
    out: list[Path] = []
    for path in roots:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def find_tape_files(roots: list[Path] | None = None) -> list[Path]:
    """Candidate trade files. ops_stack_5k_trades is intentionally absent."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots if roots is not None else search_roots():
        bases: list[Path] = []
        if root.is_file():
            bases = [root]
        else:
            bases = [root]
            if root.name != "results":
                bases.append(root / "results")
        for base in bases:
            paths: list[Path] = []
            if base.is_file() and base.suffix.lower() in {".csv", ".json"}:
                paths = [base]
            elif base.is_dir():
                for name in PREFERRED_NAMES:
                    paths.append(base / name)
                paths += list(base.glob("*universe_expand*trade*.json"))
                paths += list(base.glob("*universe_expand*trade*.csv"))
            for path in paths:
                if not path.is_file():
                    continue
                if "ops_stack" in path.name.lower():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                found.append(path)
    found.sort(key=_preferred_rank)
    return found


def choose_tape(files: list[Path], book: str) -> dict[str, Any]:
    """Prefer a file that actually contains expanded fills over a summary-only twin."""
    if not files:
        return {
            "ok": False,
            "reason": "tape_not_found",
            "book": canonical_book(book) or EXPANDED_BOOK,
            "trades": [],
            "files": [],
        }
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in files:
        try:
            got = load_tape_file(path, book)
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}")
            continue
        got["rank"] = _preferred_rank(path)
        parsed.append(got)
    with_rows = [row for row in parsed if row.get("ok")]
    if with_rows:
        with_rows.sort(key=lambda row: row["rank"])
        best = with_rows[0]
        best["alternates"] = [row["path"] for row in with_rows[1:]]
        best["errors"] = errors
        return best
    if parsed:
        parsed.sort(key=lambda row: row["rank"])
        best = parsed[0]
        best["errors"] = errors
        best["files"] = [str(path) for path in files]
        return best
    return {
        "ok": False,
        "reason": "tape_unreadable_or_empty",
        "book": canonical_book(book) or EXPANDED_BOOK,
        "trades": [],
        "files": [str(path) for path in files],
        "errors": errors,
    }


def sequential_max_dd(pnls: list[float], start: float = BOOK_USD) -> float | None:
    """Peak-to-trough of realized P&L added in exit order, from the $5k book.

    This is not a daily mark. Overlapping seats are flattened into exit order.
    """
    if not pnls:
        return None
    equity = [start]
    cash = start
    for pnl in pnls:
        cash += float(pnl)
        equity.append(cash)
    return bs.max_drawdown(equity)


def _ordered(rows: list[dict], key_pnl: str) -> list[dict]:
    usable = [row for row in rows if row.get(key_pnl) is not None and row.get("closed", True)]
    return sorted(usable, key=lambda row: (str(row.get("exit_date") or ""), str(row.get("entry_date") or ""), str(row.get("traded") or "")))


def summarize_expanded(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Stock dollars from the tape. Option dollars only from priced calls."""
    closed = [row for row in trades if row.get("closed")]
    stock_pnl = sum(float(row["pnl_usd"]) for row in closed) if closed else None
    stock_wins = sum(1 for row in closed if float(row["pnl_usd"]) > 0)
    calls = [row for row in closed if row.get("status") == "closed" and row.get("option_pnl_usd") is not None]
    opt_pnl = sum(float(row["option_pnl_usd"]) for row in calls) if calls else None
    opt_wins = sum(1 for row in calls if float(row["option_pnl_usd"]) > 0)
    same = calls
    same_stock = sum(float(row["pnl_usd"]) for row in same) if same else None
    same_wins = sum(1 for row in same if float(row["pnl_usd"]) > 0)
    skips: dict[str, int] = {}
    for row in trades:
        if row.get("status") == "closed":
            continue
        reason = str(row.get("reason_skip") or ("still_open" if not row.get("closed") else "skipped"))
        skips[reason] = skips.get(reason, 0) + 1
    stock_order = _ordered(closed, "pnl_usd")
    opt_order = _ordered(calls, "option_pnl_usd")
    outside = [
        row for row in closed
        if row.get("entry_date") and (row["entry_date"] < WINDOW_START or row["entry_date"] > WINDOW_END)
    ]
    return {
        "n_tape": len(trades),
        "n_closed": len(closed),
        "stock_pnl_usd": stock_pnl,
        "stock_pnl_pct_on_5k": (stock_pnl / BOOK_USD) if stock_pnl is not None else None,
        "stock_pnl_pct_on_half_equity": (stock_pnl / (BOOK_USD * HALF_EQUITY_FRACTION)) if stock_pnl is not None else None,
        "stock_win_rate": (stock_wins / len(closed)) if closed else None,
        "stock_max_dd": sequential_max_dd([float(row["pnl_usd"]) for row in stock_order]) if stock_order else None,
        "option_pnl_usd": opt_pnl,
        "option_pnl_pct_on_5k": (opt_pnl / BOOK_USD) if opt_pnl is not None else None,
        "option_win_rate": (opt_wins / len(calls)) if calls else None,
        "option_max_dd": sequential_max_dd([float(row["option_pnl_usd"]) for row in opt_order]) if opt_order else None,
        "n_calls": len(calls),
        "same_trades_stock_pnl_usd": same_stock,
        "same_trades_stock_win_rate": (same_wins / len(same)) if same else None,
        "n_same": len(same),
        "n_outside_window": len(outside),
        "skips": skips,
    }


def price_expanded_trades(
    trades: list[dict[str, Any]],
    bars_by: dict[str, list[dict]],
    iv_by: dict[str, Any],
) -> list[dict[str, Any]]:
    """One ~8% ITM call per fill. Missing bars leave option P&L empty."""
    priced: list[dict[str, Any]] = []
    for trade in trades:
        if not trade.get("closed"):
            row = dict(trade)
            row["status"] = "skipped"
            row["reason_skip"] = "still_open"
            row["option_pnl_usd"] = None
            priced.append(row)
            continue
        und = trade["underlying"]
        iv = (iv_by.get(und) or {}).get("iv")
        row = tape.price_one_fill(trade, bars_by.get(und) or [], float(iv or 0))
        row["closed"] = True
        row["book"] = trade.get("book")
        priced.append(row)
    return priced


def comparison_line(result: dict[str, Any]) -> str:
    """The one sentence Aaron should read first."""
    n = result.get("n_closed") or 0
    book = result.get("book") or EXPANDED_BOOK
    status = result.get("status") or "NOT_RUN"
    stock = (
        f"{tape._fmt_usd(result.get('stock_pnl_usd'))} "
        f"({tape._fmt_pct(result.get('stock_pnl_pct_on_5k'))} on $5,000, "
        f"win {tape._fmt_pct(result.get('stock_win_rate'))})"
    )
    if result.get("stock_pnl_usd") is None:
        stock = "n/a"
    option = "n/a"
    if result.get("option_pnl_usd") is not None:
        option = (
            f"{tape._fmt_usd(result.get('option_pnl_usd'))} "
            f"({tape._fmt_pct(result.get('option_pnl_pct_on_5k'))} on $5,000, "
            f"win {tape._fmt_pct(result.get('option_win_rate'))})"
        )
    return (
        f"Expanded stock P&L {stock} vs ITM call proxy {option} "
        f"on the same {n} closed {book} fills. Status: {status}."
    )


def render(result: dict[str, Any]) -> str:
    """Markdown. Banner, then the stock-vs-call comparison, then how to rerun."""
    line = result.get("comparison_line") or comparison_line(result)
    lines = [
        f"# {BANNER}",
        "",
        line,
        "",
        f"**{LABEL}**. NOT gospel. NOT broker fills. NOT live paper. NOT a new entry rule.",
        "",
        "Stock dollars and entry/exit dates are the expanded tape's. "
        "The call is one Black–Scholes ~8% ITM contract marked on those same dates. "
        "Premiums are not fills.",
        "",
        f"- Book: `{result.get('book')}`",
        f"- Tape: `{result.get('tape_path')}`",
        f"- Closed fills: {result.get('n_closed')} of {result.get('n_tape')} rows. "
        f"Calls priced: {result.get('n_calls')}.",
        f"- Stock max DD (exit-order realized, from $5,000): {tape._fmt_pct(result.get('stock_max_dd'))}.",
        f"- Call max DD (same method, priced calls only): {tape._fmt_pct(result.get('option_max_dd'))}.",
        f"- IV: {result.get('iv_note')}",
        f"- Stock bars: `{result.get('stock_bar_source')}`.",
        f"- Window the study used: {WINDOW_START} → {WINDOW_END}. "
        f"Fills outside that window: {result.get('n_outside_window')}.",
        f"- Filter expected on the tape: {FILTER_ON} ON. Exit expected: {EXIT_MODEL}.",
        f"- Runtime: {result.get('runtime_sec')} seconds.",
        "",
        "## Skips",
        "",
    ]
    skips = result.get("skips") or {}
    if not skips:
        lines.append("- None.")
    for reason, n in sorted(skips.items()):
        lines.append(f"- {reason}: {n}")
    dropped = result.get("dropped") or {}
    if any(dropped.values()):
        lines += ["", "## Rows left out of this book", ""]
        for reason, n in sorted(dropped.items()):
            if n:
                lines.append(f"- {reason}: {n}")
    lines += [
        "",
        "## Assumptions",
        "",
        "- Same fills as the expanded half-equity book. The ops-stack +33% tape is not a fallback.",
        "- ~8% ITM (5–12% band), Friday expiry inside 21–45 DTE, one contract.",
        f"- Debit must fit that fill's notional. If the tape omits notional, the seat default is ${DEFAULT_SEAT_USD:,.0f} "
        f"(half of ${BOOK_USD:,.0f} split across {MAX_SEATS} seats).",
        "- Exit mark is the model value on the tape's exit session.",
        "- Percents use the $5,000 account, which is how the expanded study quoted −14.69%. "
        "Half-equity percent is also in the JSON.",
        "- Max DD is realized P&L in exit order. It is not a daily mark of open seats.",
        "- European BS, r = 0.04. IV frozen backward. No Polygon option OHLC.",
        "- A summary-only JSON (win rate and P&L, no trade rows) is NOT_RUN. Those headline dollars are not copied.",
        "",
        "## Laptop",
        "",
        "One command. It points at `C:\\Users\\ajkle\\Documents\\Track 100\\results`, "
        "starts the options bridge only when port 8787 is down, and writes this file again.",
        "",
        "```powershell",
        LAPTOP_PS,
        "```",
        "",
        "If `universe_expand_movers_trades.json` and `.csv` are missing, the proxy has no fills. "
        "Re-run the expand study in Track 100 on branch `cursor/universe-expand-movers-3067` "
        "(the branch that contains the universe-expand code), then run the command above again:",
        "",
        "```powershell",
        MODAL_RERUN,
        "```",
        "",
    ]
    if result.get("status") != "OK":
        lines += ["## Why this copy has no option dollars", "", str(result.get("detail") or ""), ""]
    lines += [
        "## Fills",
        "",
        "| Entry | Exit | Traded | Underlying | Book | Stock $ | Call $ | Debit |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for row in result.get("trades") or []:
        lines.append(
            f"| {row.get('entry_date')} | {row.get('exit_date')} | {row.get('traded')} | "
            f"{row.get('underlying')} | {row.get('book')} | "
            f"{tape._fmt_usd(row.get('pnl_usd'))} | {tape._fmt_usd(row.get('option_pnl_usd'))} | "
            f"{tape._fmt_usd(row.get('debit_usd'))} |"
        )
    if not result.get("trades"):
        lines.append("| — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def _base_payload(book: str, runtime: float) -> dict[str, Any]:
    return {
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "book": book,
        "option_pnl_usd": None,
        "option_pnl_pct_on_5k": None,
        "option_win_rate": None,
        "option_max_dd": None,
        "n_calls": 0,
        "stock_pnl_usd": None,
        "stock_pnl_pct_on_5k": None,
        "stock_pnl_pct_on_half_equity": None,
        "stock_win_rate": None,
        "stock_max_dd": None,
        "n_closed": 0,
        "n_tape": 0,
        "n_same": 0,
        "n_outside_window": 0,
        "same_trades_stock_pnl_usd": None,
        "same_trades_stock_win_rate": None,
        "skips": {},
        "dropped": {},
        "trades": [],
        "iv": {},
        "iv_note": "not measured",
        "stock_bar_source": None,
        "tape_path": None,
        "runtime_sec": round(runtime, 1),
        "laptop_powershell": LAPTOP_PS,
        "modal_rerun": MODAL_RERUN,
        "track100_results": str(TRACK100_RESULTS),
        "study_window": [WINDOW_START, WINDOW_END],
        "filter": FILTER_ON,
        "exit_model": EXIT_MODEL,
    }


def _empty(reason: str, detail: str, runtime: float, book: str, extra: dict | None = None) -> dict[str, Any]:
    """No invented stock or option dollars."""
    payload = _base_payload(book, runtime)
    payload.update({
        "status": "NOT_RUN",
        "reason": reason,
        "detail": detail,
        "iv_note": "not measured — expanded trade list missing",
    })
    if extra:
        payload.update(extra)
    payload["comparison_line"] = comparison_line(payload)
    return payload


def _attach_prices(loaded: dict[str, Any], runtime: float) -> dict[str, Any]:
    """Fetch underlying bars and price calls. Stock P&L still comes from the tape."""
    trades = loaded["trades"]
    closed = [row for row in trades if row.get("closed")]
    symbols = sorted({row["underlying"] for row in closed if row.get("underlying")})
    if not closed or not symbols:
        summary = summarize_expanded(trades)
        summary.update(_base_payload(loaded["book"], runtime))
        summary.update({
            "status": "NOT_RUN",
            "reason": "no_closed_fills",
            "detail": "The expanded book had rows, but none were closed fills with an exit and a stock P&L.",
            "tape_path": loaded.get("path"),
            "dropped": loaded.get("dropped") or {},
            "books_seen": loaded.get("books_seen") or [],
            "trades": trades,
        })
        summary["comparison_line"] = comparison_line(summary)
        return summary
    start = min(row["entry_date"] for row in closed if row.get("entry_date"))
    end = max(row["exit_date"] or row["entry_date"] for row in closed if row.get("entry_date"))
    from datetime import date, timedelta
    fetch_start = (date.fromisoformat(start) - timedelta(days=40)).isoformat()
    bars_pack = tape._load_bars(symbols, fetch_start, end)
    bars_by = bars_pack.get("bars") or {}
    iv_by = bs.measure_iv(bars_by, symbols)
    priced = price_expanded_trades(trades, bars_by, iv_by)
    summary = summarize_expanded(priced)
    iv_sources = sorted({(row or {}).get("used") for row in iv_by.values()})
    bar_counts = {sym: len(bars_by.get(sym) or []) for sym in symbols}
    any_bars = any(bar_counts.values())
    if summary["n_calls"] == 0:
        status = "NOT_RUN"
        reason = "bars_missing" if not any_bars else "no_call_priced"
        detail = (
            "Expanded fills were loaded and stock dollars are the tape's. "
            "No call was priced, so option dollars were left empty rather than set to zero."
        )
    else:
        status = "OK"
        reason = None
        detail = None
    summary.update({
        "status": status,
        "reason": reason,
        "detail": detail,
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "book": loaded.get("book"),
        "tape_path": loaded.get("path"),
        "dropped": loaded.get("dropped") or {},
        "books_seen": loaded.get("books_seen") or [],
        "stock_bar_source": bars_pack.get("source"),
        "stock_bar_notes": bars_pack.get("notes") or [],
        "stock_bar_counts": bar_counts,
        "iv": {
            sym: {"iv": (iv_by.get(sym) or {}).get("iv"), "used": (iv_by.get(sym) or {}).get("used")}
            for sym in symbols
        },
        "iv_note": ", ".join(str(item) for item in iv_sources) or "not measured",
        "trades": priced,
        "laptop_powershell": LAPTOP_PS,
        "modal_rerun": MODAL_RERUN,
        "track100_results": str(TRACK100_RESULTS),
        "runtime_sec": round(runtime, 1),
        "assumptions": {
            "r": bs.RISK_FREE_RATE,
            "seat_default_usd": DEFAULT_SEAT_USD,
            "book_usd": BOOK_USD,
            "max_seats": MAX_SEATS,
            "filter": FILTER_ON,
            "exit": EXIT_MODEL,
            "window": [WINDOW_START, WINDOW_END],
        },
    })
    summary["comparison_line"] = comparison_line(summary)
    return summary


def write_outputs(result: dict[str, Any]) -> None:
    """Write the markdown and JSON next to this script."""
    OUT_MD.write_text(render(result), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)


def _detail_for(loaded: dict[str, Any]) -> str:
    reason = loaded.get("reason")
    if reason == "summary_only_no_trade_list":
        return (
            "The expand artifact on disk has a headline and no trade rows. "
            "Stock and option dollars were not copied from that headline. "
            "Re-run `py -3 -m modal run cloud/universe_expand_modal.py` in Track 100 so "
            "results/universe_expand_movers_trades.json or .csv contains the closed fills, "
            "then rerun the laptop command."
        )
    if reason == "book_field_missing":
        return (
            "Trade rows had no book field. They were not treated as expanded_half_equity_2, "
            "because that would mix in the baseline book."
        )
    if reason == "book_not_in_tape":
        seen = ", ".join(loaded.get("books_seen") or []) or "none"
        return f"No rows matched {loaded.get('book')}. Books present: {seen}."
    if reason == "tape_not_found":
        return (
            "universe_expand_movers_trades.json / .csv was not in this checkout or under TRACK100_TRADES. "
            "The ops-stack +33% tape was not used. No entry list was invented. "
            "On the laptop, if those files are missing, run "
            "`py -3 -m modal run cloud/universe_expand_modal.py` from Track 100 "
            "(branch cursor/universe-expand-movers-3067) and then the EXP-0027 PowerShell command."
        )
    return str(loaded.get("reason") or "expanded tape unavailable")


def run(book: str = "expanded", path: Path | None = None, roots: list[Path] | None = None) -> dict[str, Any]:
    """Load the expanded book and write the proxy report."""
    started = time.time()
    want = canonical_book(book) or EXPANDED_BOOK
    print(f"EXP-0027 expanded-book BS proxy — {want} — model marks only, no orders", flush=True)
    looked = search_roots(path) if roots is None else roots
    files = find_tape_files(looked)
    if path is not None and path.is_file():
        files = [path] + [item for item in files if item.resolve() != path.resolve()]
    loaded = choose_tape(files, want)
    if not loaded.get("ok"):
        result = _empty(
            str(loaded.get("reason") or "tape_not_found"),
            _detail_for(loaded),
            time.time() - started,
            want,
            {
                "tape_path": loaded.get("path"),
                "files": loaded.get("files") or [str(item) for item in files],
                "tried_roots": [str(item) for item in looked],
                "errors": loaded.get("errors"),
                "books_seen": loaded.get("books_seen") or [],
                "dropped": loaded.get("dropped") or {},
                "summaries_ignored": loaded.get("summaries_ignored") or [],
            },
        )
        return result
    return _attach_prices(loaded, time.time() - started)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    """CLI: optional path, --book expanded (default) or baseline."""
    parser = argparse.ArgumentParser(description="ITM call model proxy on the expanded Track 100 book")
    parser.add_argument("path", nargs="?", help="Trade JSON or CSV. Else TRACK100_TRADES / Track 100 results.")
    parser.add_argument(
        "--book",
        default=os.environ.get("TRACK100_BOOK") or "expanded",
        help="expanded (default) or baseline. expanded selects expanded_half_equity_2.",
    )
    args = parser.parse_args(argv)
    path = Path(args.path).expanduser() if args.path else None
    result = run(book=args.book, path=path)
    result["runtime_sec"] = result.get("runtime_sec") if result.get("runtime_sec") is not None else 0
    write_outputs(result)
    print(f"{BANNER} {result.get('comparison_line')}", flush=True)
    return result


if __name__ == "__main__":
    main()
