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
    bar_hour: Any = None,
    rank: Any = None,
    kind: str = "NEW",
) -> str:
    """Peak Hour entry alert text."""
    kill = f"{float(kill_pct):.1%}" if kill_pct is not None else "?"
    lines = [
        f"Peak Hour ENTERED {str(symbol).upper()} ({kind})",
        f"{int(shares)} @ ${float(fill_price):.2f}",
        f"kill={kill}  mode=KILL ONLY until +1R",
    ]
    if bar_hour is not None or rank is not None:
        lines.append(f"hour={bar_hour} rank={rank}")
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
