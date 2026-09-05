"""
Shared Streamlit renderer for trade thesis cards (plain English + evidence).
"""
from __future__ import annotations

from typing import Any

import streamlit as st


def render_thesis_expander(thesis: Any, *, label: str = "Thesis") -> None:
    """Render a frozen thesis dict inside an expander (no strategy jargon)."""
    if not isinstance(thesis, dict) or not thesis:
        return
    headline = str(thesis.get("headline") or "").strip()
    bullets = thesis.get("bullets") or []
    used = thesis.get("sources_used") or []
    missing = thesis.get("sources_missing") or []
    if not headline and not bullets:
        return

    with st.expander(label, expanded=False):
        if headline:
            st.markdown(headline)
        if bullets:
            lines = []
            for b in bullets:
                if not isinstance(b, dict):
                    continue
                bucket = str(b.get("bucket") or "").strip()
                text = str(b.get("text") or "").strip()
                if bucket and text:
                    lines.append(f"**{bucket}** — {text}")
            if lines:
                st.markdown("\n\n".join(lines))
        cap_bits = []
        if used:
            cap_bits.append("Sources: " + " · ".join(str(x) for x in used))
        if missing:
            cap_bits.append("Missing: " + " · ".join(str(x) for x in missing))
        if cap_bits:
            st.caption("  |  ".join(cap_bits))
