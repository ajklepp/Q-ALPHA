"""
Peak Hour Performers / TSD — Telegram notifications (best-effort).

Never raises into the trade path. Uses the same TELEGRAM_* env as the gap agent.
"""
from __future__ import annotations

from typing import Any


def notify_tsd(msg: str) -> None:
    """Send Telegram via autonomous_agent.send_telegram; swallow all errors."""
    try:
        from autonomous_agent import send_telegram

        send_telegram(msg)
    except Exception as exc:
        try:
            print(f"  [telegram] {msg[:120]} ({exc})")
        except Exception:
            pass


def format_entered(
    symbol: str,
    *,
    shares: int,
    fill_price: float,
    kill_pct: float | None = None,
    kill_source: str | None = None,
    bar_hour: Any = None,
    bar_state: str | None = None,
    rank: Any = None,
    continuation_score: Any = None,
    print_tag: str | None = None,
    outlook: str | None = None,
    kind: str = "NEW",
) -> str:
    """Peak Hour entry alert text with kill/bar/outlook telemetry."""
    kill = f"{float(kill_pct):.1%}" if kill_pct is not None else "?"
    src = f" ({kill_source})" if kill_source else ""
    lines = [
        f"Peak Hour ENTERED {str(symbol).upper()} ({kind})",
        f"{int(shares)} @ ${float(fill_price):.2f}",
        f"kill={kill}{src}  mode=KILL ONLY until +1R",
    ]
    meta_bits = []
    if bar_hour is not None:
        meta_bits.append(f"hour={bar_hour}")
    if bar_state:
        meta_bits.append(f"bar={bar_state}")
    score = continuation_score if continuation_score is not None else rank
    if score is not None:
        meta_bits.append(f"cont={score}")
    if print_tag:
        meta_bits.append(f"PRINT={print_tag}")
    if outlook:
        meta_bits.append(f"OUTLOOK={outlook}")
    if meta_bits:
        lines.append(" ".join(meta_bits))
    return "\n".join(lines)


def format_exited(
    symbol: str,
    *,
    reason: str,
    shares: int | None = None,
    exit_price: float | None = None,
    pnl_dollars: float | None = None,
) -> str:
    """Peak Hour full-exit alert text."""
    lines = [f"Peak Hour EXITED {str(symbol).upper()}", f"reason={reason}"]
    if shares is not None and exit_price is not None:
        lines.append(f"{int(shares)} @ ${float(exit_price):.2f}")
    if pnl_dollars is not None:
        lines.append(f"pnl=${float(pnl_dollars):+.2f}")
    return "\n".join(lines)


def format_scan_summary(
    *,
    hour: Any,
    htf_pass: int,
    launches_n: int,
    take_n: int,
    entered_n: int = 0,
    reject_summary: dict[str, Any] | None = None,
    take_symbols: list[str] | None = None,
) -> str:
    """Telegram summary for every live 1H launch scan (including 0 launches)."""
    lines = [
        f"Peak Hour SCAN hour={hour}",
        f"HTF={htf_pass} launches={launches_n} take={take_n} entered={entered_n}",
    ]
    if take_symbols:
        lines.append("take: " + ", ".join(take_symbols[:8]))
    if reject_summary:
        top = sorted(
            ((str(k), int(v)) for k, v in reject_summary.items()),
            key=lambda kv: -kv[1],
        )[:4]
        if top:
            lines.append("rejects: " + ", ".join(f"{k}×{v}" for k, v in top))
    if launches_n == 0:
        lines.append("no 1H launches this slot")
    return "\n".join(lines)


def format_queue_skip_summary(
    taken: int,
    skipped: list[dict[str, Any]],
) -> str:
    """One summary when launch names exist but none were queue-admitted."""
    bits = []
    for s in skipped[:8]:
        bits.append(
            f"{s.get('symbol')}:{s.get('status')}/{s.get('reason', '')}"
        )
    extra = f" (+{len(skipped) - 8} more)" if len(skipped) > 8 else ""
    return (
        f"Peak Hour launch: {taken} signal(s), 0 queue-admitted\n"
        + (", ".join(bits) if bits else "(no detail)")
        + extra
    )
