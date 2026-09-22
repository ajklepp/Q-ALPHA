"""
EXP-0027 — Black–Scholes proxy on the Track 100 fills that were actually taken.

Label: model_proxy_pnl
MODEL PROXY ONLY — NOT FILLS. Not gospel. Not broker fills. Not live paper.

The entry list is the Track 100 tape (filter ON + C_ratchet), not the
SMA-cross signals in study_itm_bs_proxy.py. This file does not invent a
new entry filter. If the tape is not on disk, it writes an empty proxy.

Laptop (one command):

    cd C:\\Users\\ajkle\\Documents\\Q-ALPHA
    .\\experiments\\EXP-0027\\run_t100_fills_bs_proxy.ps1
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import study_itm_bs_proxy as bs

ET = ZoneInfo("America/New_York")
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
REPO = EXP_DIR.parents[1]
OUT_MD = EXP_DIR / "results_t100_fills_bs_proxy.md"
OUT_JSON = EXP_DIR / "results_t100_fills_bs_proxy.json"

BANNER = "MODEL PROXY ONLY — NOT FILLS"
LABEL = "model_proxy_pnl"
BOOK_USD = 5000.0
# $5k / 10 seats. Used only when a fill does not record its own notional.
DEFAULT_SEAT_USD = 500.0
TARGET_RETURN = 0.33  # half-equity book the tape is supposed to be
TARGET_WIN = 0.77

# Single-name 2x long ETFs seen in Track 100 notes and the request.
# A tape column named underlying always wins over this table.
LEVERAGE_2X_TO_UNDERLYING = {
    "NVDL": "NVDA",
    "NVDX": "NVDA",
    "TSLL": "TSLA",
    "MSFU": "MSFT",
    "AMZU": "AMZN",
    "MUU": "MU",
    "SMCX": "SMCI",
    "ASMU": "ASML",
    "CONL": "COIN",
    "AAPB": "AAPL",
    "AMDL": "AMD",
    "METU": "META",
    "TSMX": "TSM",
    "GGLL": "GOOGL",
    "ADBU": "ADBE",
}

TAPE_RELATIVE = (
    "results/ops_stack_5k_trades.json",
    "results/ops_stack_5k_trades.csv",
    "results/half_equity_trades.json",
    "results/half_equity_trades.csv",
    "results/capital_util_trades.json",
    "results/capital_util_trades.csv",
)

LAPTOP_PS = (
    "cd C:\\Users\\ajkle\\Documents\\Q-ALPHA\n"
    ".\\experiments\\EXP-0027\\run_t100_fills_bs_proxy.ps1"
)


def track100_roots() -> list[Path]:
    """Places the ops-stack tape is allowed to live. No network lookup."""
    roots: list[Path] = []
    for key in ("T100_TRADES", "TRACK100_TRADES", "TRACK100_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            roots.append(Path(raw).expanduser())
    roots.append(Path(r"C:\Users\ajkle\Documents\Track 100"))
    roots.append(Path.home() / "Documents" / "Track 100")
    for name in ("track-100", "Track 100", "Track100"):
        roots.append(REPO.parent / name)
        roots.append(REPO / name)
    roots.append(EXP_DIR)
    seen: set[str] = set()
    out: list[Path] = []
    for path in roots:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _candidate_files() -> list[Path]:
    """ops_stack tape first, then half-equity / capital-util names."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in track100_roots():
        bases = [root]
        if root.is_file():
            bases = [root]
        elif root.name != "results":
            bases.append(root / "results")
        for base in bases:
            if base.is_file() and base.suffix.lower() in {".csv", ".json"}:
                paths = [base]
            elif base.is_dir():
                paths = [base / rel for rel in TAPE_RELATIVE]
                paths += list(base.glob("*half*equity*.csv"))
                paths += list(base.glob("*half*equity*.json"))
                paths += list(base.glob("*capital_util*.csv"))
                paths += list(base.glob("*capital_util*.json"))
                paths += list(base.glob("*ops_stack_5k_trades.*"))
            else:
                continue
            for path in paths:
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                found.append(path)
    return found


def _session_date(raw: Any) -> str | None:
    """Calendar date in Eastern time when the stamp carries an offset."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and "T" not in text[:11] and " " not in text[:11]:
        return text[:10]
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ET)
    return stamp.astimezone(ET).date().isoformat()


def _num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val):
        return None
    return val


def _first(row: dict, *keys: str) -> Any:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def map_underlying(traded: str, explicit: str | None) -> dict[str, Any]:
    """Option underlying. Tape column wins. Else the documented 2x table."""
    symbol = str(traded or "").upper().strip()
    if explicit:
        und = str(explicit).upper().strip()
        return {
            "underlying": und,
            "mapped": und != symbol,
            "map_source": "tape_underlying",
        }
    if symbol in LEVERAGE_2X_TO_UNDERLYING:
        return {
            "underlying": LEVERAGE_2X_TO_UNDERLYING[symbol],
            "mapped": True,
            "map_source": "leverage_2x_table",
        }
    return {"underlying": symbol, "mapped": False, "map_source": "symbol_is_underlying"}


def _pnl_usd(row: dict, notional: float | None) -> float | None:
    """Dollar P&L already on the tape. Percent is scaled by that fill's notional."""
    for key in ("pnl_usd", "pnl", "dollar_pnl", "stock_pnl_usd", "half_equity_pnl_usd"):
        val = _num(_first(row, key))
        if val is not None and key != "pnl":
            return val
        if val is not None and key == "pnl" and abs(val) > 3:
            return val
    pct = _num(_first(row, "pnl_pct", "return_pct", "ret", "pnl_percent"))
    if pct is None or notional is None:
        direct = _num(_first(row, "pnl"))
        return direct
    scale = pct / 100.0 if abs(pct) > 1.5 else pct
    return scale * notional


def normalize_trade(row: dict) -> dict[str, Any] | None:
    """One tape row into the fields the proxy needs. Incomplete rows are dropped."""
    traded = str(_first(row, "traded", "symbol", "ticker") or "").upper().strip()
    if not traded:
        return None
    entry_date = _session_date(_first(row, "entry_date", "opened_et", "entry_ts", "open_ts", "entry_time"))
    exit_date = _session_date(_first(row, "exit_date", "closed_et", "exit_ts", "close_ts", "exit_time"))
    entry_px = _num(_first(row, "entry", "entry_px", "entry_price", "fill_px"))
    exit_px = _num(_first(row, "exit", "exit_px", "exit_price"))
    notional = _num(_first(row, "notional", "seat_usd", "seat_notional", "target_notional", "dollars"))
    if notional is None and entry_px and _num(_first(row, "qty", "quantity")):
        notional = entry_px * float(_first(row, "qty", "quantity"))
    mapped = map_underlying(traded, _first(row, "underlying", "option_underlying"))
    return {
        "traded": traded,
        "underlying": mapped["underlying"],
        "mapped": mapped["mapped"],
        "map_source": mapped["map_source"],
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_px": entry_px,
        "exit_px": exit_px,
        "notional": notional,
        "pnl_usd": _pnl_usd(row, notional),
        "reason": str(_first(row, "reason", "exit_reason") or ""),
        "seat_usd": _num(_first(row, "seat_usd", "seat_notional")) or notional,
    }


def _trades_from_obj(obj: Any) -> list[dict]:
    """Pull a trade list out of the JSON shapes Track 100 has used."""
    if isinstance(obj, list):
        return [row for row in obj if isinstance(row, dict)]
    if not isinstance(obj, dict):
        return []
    for key in ("trades", "fills", "closed", "rows"):
        val = obj.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    for key in ("book", "primary", "half_equity", "filter_on_c_ratchet", "c_ratchet"):
        nested = obj.get(key)
        found = _trades_from_obj(nested)
        if found:
            return found
    books = obj.get("books") or obj.get("variants")
    if isinstance(books, dict):
        preferred = None
        for name, val in books.items():
            blob = f"{name} {json.dumps(val)[:400] if not isinstance(val, (str, int, float)) else val}".lower()
            if "c_ratchet" in blob and ("filter" in blob or "half" in blob):
                preferred = val
                break
        if preferred is None and books:
            preferred = next(iter(books.values()))
        return _trades_from_obj(preferred)
    return []


def load_tape_file(path: Path) -> dict[str, Any]:
    """Read one CSV or JSON tape and normalize rows."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
        meta: dict[str, Any] = {}
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = _trades_from_obj(payload)
        meta = payload if isinstance(payload, dict) else {}
    trades = [row for row in (normalize_trade(raw) for raw in rows) if row]
    return {"path": str(path), "meta": meta if isinstance(meta, dict) else {}, "trades": trades}


def _tape_score(loaded: dict[str, Any]) -> float:
    """Prefer the filter-on C_ratchet tape whose own stats sit near +33% / 77%."""
    trades = loaded["trades"]
    name = Path(loaded["path"]).name.lower()
    score = 0.0
    if "ops_stack_5k_trades" in name:
        score += 5
    if "half" in name or "capital_util" in name:
        score += 3
    blob = json.dumps(loaded.get("meta") or {}).lower()
    if "c_ratchet" in blob:
        score += 4
    if "filter" in blob and "on" in blob:
        score += 2
    closed = [row for row in trades if row.get("pnl_usd") is not None]
    if not closed:
        return score
    wins = sum(1 for row in closed if float(row["pnl_usd"]) > 0) / len(closed)
    pnl = sum(float(row["pnl_usd"]) for row in closed)
    ret = pnl / BOOK_USD
    score += max(0.0, 3 - abs(wins - TARGET_WIN) * 10)
    score += max(0.0, 3 - abs(ret - TARGET_RETURN) * 10)
    half = pnl / (BOOK_USD / 2.0)
    score += max(0.0, 3 - abs(half - TARGET_RETURN) * 10)
    return score


def load_best_tape() -> dict[str, Any]:
    """Highest-scoring tape on disk. Empty when nothing is present."""
    files = _candidate_files()
    if not files:
        return {"ok": False, "reason": "tape_not_found", "tried": [str(p) for p in track100_roots()]}
    loaded = []
    errors = []
    for path in files:
        try:
            got = load_tape_file(path)
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}")
            continue
        if got["trades"]:
            got["score"] = _tape_score(got)
            loaded.append(got)
    if not loaded:
        return {
            "ok": False,
            "reason": "tape_unreadable_or_empty",
            "files": [str(p) for p in files],
            "errors": errors,
        }
    loaded.sort(key=lambda row: row["score"], reverse=True)
    best = loaded[0]
    best["ok"] = True
    best["alternates"] = [row["path"] for row in loaded[1:]]
    return best


def _bar_on(bars: list[dict], day: str) -> dict | None:
    for bar in bars:
        if bar["date"] == day:
            return bar
    return None


def _prior_close(bars: list[dict], day: str) -> float | None:
    prior = [bar for bar in bars if bar["date"] < day]
    if not prior:
        return None
    return float(prior[-1]["c"])


def price_one_fill(
    trade: dict[str, Any],
    bars: list[dict],
    iv: float,
) -> dict[str, Any]:
    """BS call from the tape entry session to the tape exit session.

    The stock P&L is the tape's own dollar P&L. The option uses the
    underlying print: the tape price when that symbol was the underlying,
    otherwise the underlying daily bar.
    """
    out = dict(trade)
    out["status"] = "skipped"
    out["option_pnl_usd"] = None
    und = trade["underlying"]
    entry_date = trade.get("entry_date")
    exit_date = trade.get("exit_date")
    if not entry_date or not exit_date or not iv or iv <= 0:
        out["reason_skip"] = "missing_exit_or_iv" if entry_date else "missing_entry"
        return out
    if exit_date < entry_date:
        out["reason_skip"] = "exit_before_entry"
        return out
    div = bs.DIVIDEND_YIELD.get(und, 0.0)
    own = trade["traded"] == und
    if own and trade.get("entry_px"):
        spot_entry = float(trade["entry_px"])
    else:
        bar = _bar_on(bars, entry_date)
        spot_entry = float(bar["o"]) if bar else None
    if own and trade.get("exit_px"):
        spot_exit = float(trade["exit_px"])
    else:
        bar = _bar_on(bars, exit_date)
        spot_exit = float(bar["c"]) if bar else None
    prior = _prior_close(bars, entry_date)
    strike_spot = prior if prior else spot_entry
    if not spot_entry or not spot_exit or not strike_spot or spot_entry <= 0 or spot_exit <= 0:
        out["reason_skip"] = "missing_underlying_price"
        return out
    picked = bs.choose_model_strike(float(strike_spot))
    expiry = bs.choose_model_expiry(date.fromisoformat(entry_date))
    if picked is None or expiry is None:
        out["reason_skip"] = "no_strike"
        return out
    t_entry = bs._years_left(expiry, date.fromisoformat(entry_date), at_close=False)
    t_exit = bs._years_left(expiry, date.fromisoformat(exit_date), at_close=True)
    premium_in = bs.bs_call_price(spot_entry, picked["strike"], t_entry, iv, bs.RISK_FREE_RATE, div)
    premium_out = bs.bs_call_price(spot_exit, picked["strike"], t_exit, iv, bs.RISK_FREE_RATE, div)
    if not math.isfinite(premium_in) or premium_in <= 0 or not math.isfinite(premium_out) or premium_out < 0:
        out["reason_skip"] = "bad_model_price"
        return out
    debit = premium_in * bs.CONTRACT_MULTIPLIER
    seat = trade.get("seat_usd") if trade.get("seat_usd") and trade["seat_usd"] > 0 else DEFAULT_SEAT_USD
    out.update({
        "strike": picked["strike"],
        "itm_pct": picked["itm_pct"],
        "expiry": expiry.isoformat(),
        "dte": (expiry - date.fromisoformat(entry_date)).days,
        "spot_entry": spot_entry,
        "spot_exit": spot_exit,
        "iv": iv,
        "debit_usd": debit,
        "seat_usd_used": seat,
        "entry_premium": premium_in,
        "exit_premium": premium_out,
    })
    if debit > seat + 1e-6:
        out["status"] = "skipped"
        out["reason_skip"] = "premium_exceeds_seat"
        return out
    pnl = (premium_out - premium_in) * bs.CONTRACT_MULTIPLIER
    if pnl < -debit - 1e-6:
        out["status"] = "skipped"
        out["reason_skip"] = "loss_exceeded_debit"
        return out
    out["status"] = "closed"
    out["reason_skip"] = None
    out["option_pnl_usd"] = pnl
    out["option_reason"] = "track100_exit_same_session"
    return out


def summarize(trades: list[dict], tape_meta: dict[str, Any]) -> dict[str, Any]:
    """Headline dollars for the call proxy and the tape's own stock P&L."""
    stock_rows = [row for row in trades if row.get("pnl_usd") is not None]
    stock_pnl = sum(float(row["pnl_usd"]) for row in stock_rows)
    stock_wins = sum(1 for row in stock_rows if float(row["pnl_usd"]) > 0)
    calls = [row for row in trades if row.get("status") == "closed" and row.get("option_pnl_usd") is not None]
    opt_pnl = sum(float(row["option_pnl_usd"]) for row in calls)
    opt_wins = sum(1 for row in calls if float(row["option_pnl_usd"]) > 0)
    same = [row for row in calls if row.get("pnl_usd") is not None]
    same_stock = sum(float(row["pnl_usd"]) for row in same)
    same_stock_wins = sum(1 for row in same if float(row["pnl_usd"]) > 0)
    skips: dict[str, int] = {}
    for row in trades:
        if row.get("status") == "closed":
            continue
        reason = str(row.get("reason_skip") or "skipped")
        skips[reason] = skips.get(reason, 0) + 1
    return {
        "n_tape": len(trades),
        "stock_pnl_usd": stock_pnl,
        "stock_pnl_pct_on_5k": stock_pnl / BOOK_USD,
        "stock_pnl_pct_on_half_equity": stock_pnl / (BOOK_USD / 2.0),
        "stock_win_rate": (stock_wins / len(stock_rows)) if stock_rows else None,
        "n_stock": len(stock_rows),
        "option_pnl_usd": opt_pnl,
        "option_pnl_pct_on_5k": opt_pnl / BOOK_USD,
        "option_win_rate": (opt_wins / len(calls)) if calls else None,
        "n_calls": len(calls),
        "same_trades_stock_pnl_usd": same_stock,
        "same_trades_stock_win_rate": (same_stock_wins / len(same)) if same else None,
        "n_same": len(same),
        "skips": skips,
        "tape_meta_keys": sorted(tape_meta.keys())[:40],
    }


def _fmt_usd(val: Any) -> str:
    if val is None or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
        return "n/a"
    return f"${float(val):,.2f}"


def _fmt_pct(val: Any) -> str:
    if val is None or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
        return "n/a"
    return f"{float(val) * 100:.2f}%"


def render(result: dict) -> str:
    """Markdown. First lines are the banner and the two P&Ls."""
    opt = result.get("option_pnl_usd")
    stk = result.get("stock_pnl_usd")
    lines = [
        f"# {BANNER}",
        "",
        f"**{LABEL}**. NOT gospel. NOT broker fills. NOT live paper. NOT a new entry rule.",
        "",
        f"Call proxy **{_fmt_usd(opt)}** ({_fmt_pct(result.get('option_pnl_pct_on_5k'))} on $5,000) "
        f"on {result.get('n_calls')} contracts. "
        f"Track 100 stock P&L on the same tape **{_fmt_usd(stk)}** "
        f"({_fmt_pct(result.get('stock_pnl_pct_on_5k'))} on $5,000, "
        f"{_fmt_pct(result.get('stock_pnl_pct_on_half_equity'))} on half of $5,000).",
        f"Win rate: calls {_fmt_pct(result.get('option_win_rate'))}, "
        f"tape {_fmt_pct(result.get('stock_win_rate'))}.",
        f"On the fills that bought a call, tape stock P&L was {_fmt_usd(result.get('same_trades_stock_pnl_usd'))} "
        f"(win {_fmt_pct(result.get('same_trades_stock_win_rate'))}, n={result.get('n_same')}).",
        "",
        "Premiums are Black–Scholes marks with volatility frozen from the recent window. They are not fills.",
        "",
        f"- Tape: `{result.get('tape_path')}`",
        f"- Tape rows: {result.get('n_tape')}. Status: {result.get('status')}.",
        f"- IV: {result.get('iv_note')}",
        f"- Stock bars: `{result.get('stock_bar_source')}`.",
        f"- 2x map: {result.get('mapping_note')}",
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
    lines += [
        "",
        "## IV",
        "",
    ]
    for symbol, row in sorted((result.get("iv") or {}).items()):
        lines.append(
            f"- **{symbol}**: {bs._fmt_num(row.get('iv'))} "
            f"via `{row.get('used')}`"
        )
    lines += [
        "",
        "## Assumptions",
        "",
        "- Entries and exits are the tape's. No SMA-cross filter from the other EXP-0027 study.",
        "- ~8% ITM (5–12% band), Friday expiry inside 21–45 DTE, one contract if debit ≤ that fill's notional, else $500.",
        "- Exit mark is the model value on the tape's exit session. Option stop rules are not a second exit when the tape has one.",
        "- Levered tickers use the tape underlying column, else the documented 2x table (NVDL→NVDA, TSLL→TSLA, …).",
        "- European BS, r = 0.04, q from the other study or 0. IV frozen backward. No Polygon option OHLC.",
        "",
        "## Laptop",
        "",
        "```powershell",
        LAPTOP_PS,
        "```",
        "",
    ]
    if result.get("status") != "OK":
        lines += ["## Why this copy has no dollars", "", str(result.get("detail") or ""), ""]
    lines += [
        "## Fills",
        "",
        "| Entry | Exit | Traded | Underlying | Reason | Stock $ | Call $ | Debit |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for row in result.get("trades") or []:
        lines.append(
            f"| {row.get('entry_date')} | {row.get('exit_date')} | {row.get('traded')} | "
            f"{row.get('underlying')} | {row.get('reason') or row.get('reason_skip')} | "
            f"{_fmt_usd(row.get('pnl_usd'))} | {_fmt_usd(row.get('option_pnl_usd'))} | "
            f"{_fmt_usd(row.get('debit_usd'))} |"
        )
    if not result.get("trades"):
        lines.append("| — | — | — | — | no tape rows | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def write_outputs(result: dict) -> None:
    """Write the markdown and JSON next to this script."""
    OUT_MD.write_text(render(result), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)


def _empty(reason: str, detail: str, runtime: float, extra: dict | None = None) -> dict[str, Any]:
    """No invented fills when the tape is missing."""
    payload = {
        "status": "NOT_RUN",
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "reason": reason,
        "detail": detail,
        "option_pnl_usd": None,
        "option_pnl_pct_on_5k": None,
        "option_win_rate": None,
        "n_calls": 0,
        "stock_pnl_usd": None,
        "stock_pnl_pct_on_5k": None,
        "stock_pnl_pct_on_half_equity": None,
        "stock_win_rate": None,
        "n_stock": 0,
        "n_tape": 0,
        "n_same": 0,
        "same_trades_stock_pnl_usd": None,
        "same_trades_stock_win_rate": None,
        "skips": {},
        "trades": [],
        "iv": {},
        "iv_note": "not measured — tape missing",
        "stock_bar_source": None,
        "tape_path": None,
        "mapping_note": "NVDL→NVDA, TSLL→TSLA, plus the table in this file. Tape underlying column wins.",
        "runtime_sec": round(runtime, 1),
        "laptop_powershell": LAPTOP_PS,
    }
    if extra:
        payload.update(extra)
    return payload


def run_tape(loaded: dict[str, Any]) -> dict[str, Any]:
    """Fetch underlying bars, freeze IV, price each tape fill."""
    trades = loaded["trades"]
    symbols = sorted({row["underlying"] for row in trades if row.get("underlying")})
    if "SPY" not in symbols:
        pass
    start = min(row["entry_date"] for row in trades if row.get("entry_date"))
    end = max(row["exit_date"] or row["entry_date"] for row in trades if row.get("entry_date"))
    # Warmup so a prior close exists for the strike.
    fetch_start = date.fromisoformat(start).isoformat()
    # 40 calendar days before the first entry is enough for one prior close.
    from datetime import timedelta
    fetch_start = (date.fromisoformat(start) - timedelta(days=40)).isoformat()
    bars_pack = _load_bars(symbols, fetch_start, end)
    bars_by = bars_pack["bars"]
    iv_by = bs.measure_iv(bars_by, symbols)
    priced = []
    for trade in trades:
        und = trade["underlying"]
        iv = (iv_by.get(und) or {}).get("iv")
        priced.append(price_one_fill(trade, bars_by.get(und) or [], float(iv or 0)))
    summary = summarize(priced, loaded.get("meta") or {})
    iv_sources = sorted({(row or {}).get("used") for row in iv_by.values()})
    summary.update({
        "status": "OK",
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "tape_path": loaded.get("path"),
        "tape_score": loaded.get("score"),
        "stock_bar_source": bars_pack.get("source"),
        "stock_bar_notes": bars_pack.get("notes") or [],
        "iv": {sym: {"iv": (iv_by.get(sym) or {}).get("iv"), "used": (iv_by.get(sym) or {}).get("used")} for sym in symbols},
        "iv_note": ", ".join(str(s) for s in iv_sources),
        "mapping_note": "Tape underlying column wins. Else NVDL→NVDA, TSLL→TSLA, and LEVERAGE_2X_TO_UNDERLYING.",
        "trades": priced,
        "laptop_powershell": LAPTOP_PS,
        "assumptions": {
            "r": bs.RISK_FREE_RATE,
            "seat_default_usd": DEFAULT_SEAT_USD,
            "book_usd": BOOK_USD,
            "target_return_hint": TARGET_RETURN,
            "target_win_hint": TARGET_WIN,
        },
    })
    return summary


def _load_bars(symbols: list[str], start: str, end: str) -> dict[str, Any]:
    """Polygon when the key or Modal secret is present, else chart daily OHLC."""
    key = (os.environ.get("POLYGON_API_KEY") or "").strip()
    notes: list[str] = []
    if key:
        got = bs.fetch_polygon_universe(key, symbols, start, end)
        got["notes"] = notes
        return got
    notes.append("POLYGON_API_KEY was not in the environment")
    if bs.modal is not None and bs.app is not None and not os.environ.get("MODAL_TASK_ID"):
        try:
            with bs.app.run():
                got = bs.fetch_stock_bars_modal.remote(list(symbols), start, end)
            if got.get("ok"):
                got["notes"] = notes
                got["source"] = "polygon_unadjusted_via_modal"
                return got
            notes.append(f"modal returned {got.get('error')}")
        except Exception as exc:
            notes.append(f"modal failed: {type(exc).__name__}")
    got = bs.fetch_yahoo_universe(symbols, start, end)
    got["notes"] = notes
    return got


def _rejected_local_books() -> list[str]:
    """Books on disk that are not the +33% / ~77% tape."""
    notes = []
    path = REPO / "candidates" / "track100_cloud" / "paper_book.json"
    if not path.is_file():
        return notes
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        closed = [row for row in (payload.get("closed") or []) if isinstance(row, dict)]
        if not closed:
            return notes
        wins = sum(1 for row in closed if float(row.get("pnl_pct") or 0) > 0) / len(closed)
        notes.append(
            f"Rejected {path.relative_to(REPO)}: {len(closed)} closed, win rate {wins:.1%}. "
            "That is the v12A paper ledger, not the filter-on C_ratchet half-equity tape."
        )
    except Exception as exc:
        notes.append(f"Could not read paper_book.json: {type(exc).__name__}")
    return notes


def main() -> dict[str, Any]:
    """Load the Track 100 tape and write the proxy report."""
    started = time.time()
    print("EXP-0027 Track 100 fills BS proxy — model marks only, no orders", flush=True)
    loaded = load_best_tape()
    if not loaded.get("ok"):
        rejected = _rejected_local_books()
        result = _empty(
            str(loaded.get("reason")),
            "Track 100 ops_stack_5k_trades (filter ON + C_ratchet) was not in this checkout. "
            "No entry list was invented. " + " ".join(rejected),
            time.time() - started,
            {
                "tried_roots": loaded.get("tried"),
                "files": loaded.get("files"),
                "errors": loaded.get("errors"),
                "rejected_books": rejected,
            },
        )
        write_outputs(result)
        print(f"{BANNER} tape missing — proxy not computed", flush=True)
        return result
    result = run_tape(loaded)
    result["runtime_sec"] = round(time.time() - started, 1)
    write_outputs(result)
    print(
        f"{BANNER} calls {_fmt_usd(result.get('option_pnl_usd'))} "
        f"vs tape {_fmt_usd(result.get('stock_pnl_usd'))}",
        flush=True,
    )
    return result


if __name__ == "__main__":
    main()
