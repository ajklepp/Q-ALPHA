"""
Q-ALPHA dashboard visual theme — dark fintech console (cyan/teal accent).

Inject EXACTLY once per run into the parent document <head> via a zero-height
component (never renders CSS as page text). No purple/indigo/violet.
"""
from __future__ import annotations

from html import escape

# Design tokens (literal hex — mirrored in THEME_CSS :root)
BG = "#0B1220"
SURFACE = "#121A2B"
SURFACE_2 = "#1A2438"
BORDER = "#2A3548"
TEXT = "#E8EEF7"
MUTED = "#94A3B8"
ACCENT = "#2DD4BF"
ACCENT_2 = "#22D3EE"
POSITIVE = "#34D399"
NEGATIVE = "#FB7185"
WARN = "#FBBF24"
RADIUS = "16px"

# Single stylesheet. Do NOT wrap in an f-string when building inject HTML —
# curly braces must stay CSS-literal.
THEME_CSS = """
:root {
  --qa-bg: #0B1220;
  --qa-surface: #121A2B;
  --qa-surface-2: #1A2438;
  --qa-border: #2A3548;
  --qa-text: #E8EEF7;
  --qa-muted: #94A3B8;
  --qa-accent: #2DD4BF;
  --qa-accent-2: #22D3EE;
  --qa-up: #34D399;
  --qa-down: #FB7185;
  --qa-warn: #FBBF24;
  --qa-radius: 16px;
  --qa-font-body: 16.5px;
  --qa-font-caption: 13.5px;
  --qa-font-tab: 15.5px;
  --qa-font-section: 1.35rem;
  --qa-font-metric: 1.55rem;
}

html, body, .stApp, [data-testid="stAppViewContainer"] {
  font-family: "Sora", sans-serif !important;
  font-size: var(--qa-font-body) !important;
  line-height: 1.55 !important;
  color: var(--qa-text) !important;
}

.stApp {
  background:
    radial-gradient(900px 480px at 8% -5%, #16304a 0%, transparent 55%),
    radial-gradient(700px 400px at 95% 0%, #0f2a3a 0%, transparent 50%),
    var(--qa-bg) !important;
}

[data-testid="stHeader"] {
  background: rgba(11, 18, 32, 0.72) !important;
  backdrop-filter: blur(8px);
}
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }

.block-container {
  padding-top: 1.25rem !important;
  padding-bottom: 2.75rem !important;
  max-width: 1240px !important;
}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span {
  font-size: var(--qa-font-body) !important;
  line-height: 1.55 !important;
}

/* Tabs: segmented control — larger tap targets */
.stTabs [data-baseweb="tab-list"] {
  gap: 0.35rem;
  background: var(--qa-surface) !important;
  border: 1px solid var(--qa-border) !important;
  border-radius: var(--qa-radius) !important;
  padding: 0.45rem !important;
  margin-bottom: 1.25rem !important;
  flex-wrap: wrap !important;
}
.stTabs [data-baseweb="tab"] {
  border-radius: 12px !important;
  color: var(--qa-muted) !important;
  font-family: "Sora", sans-serif !important;
  font-weight: 600 !important;
  font-size: var(--qa-font-tab) !important;
  padding: 0.65rem 1rem !important;
  min-height: 2.6rem !important;
  background: transparent !important;
  border: 1px solid transparent !important;
}
.stTabs [data-baseweb="tab"]:hover {
  color: var(--qa-text) !important;
  background: var(--qa-surface-2) !important;
}
.stTabs [aria-selected="true"] {
  background: var(--qa-surface-2) !important;
  color: var(--qa-accent) !important;
  border: 1px solid rgba(45, 212, 191, 0.45) !important;
  box-shadow: inset 0 -2px 0 0 var(--qa-accent) !important;
}
.stTabs [data-baseweb="tab-highlight],
.stTabs [data-baseweb="tab-border"] {
  display: none !important;
}

/* KPI metric cards — full dollar amounts, no ellipsis */
div[data-testid="stMetric"] {
  background: var(--qa-surface) !important;
  border: 1px solid var(--qa-border) !important;
  border-radius: var(--qa-radius) !important;
  padding: 1.05rem 1rem 0.95rem 1rem !important;
  min-height: 6.25rem;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22);
  overflow: visible !important;
}
div[data-testid="stMetric"] label,
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
  color: var(--qa-muted) !important;
  font-family: "Sora", sans-serif !important;
  font-size: 0.9rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.03em !important;
  text-transform: none !important;
  white-space: normal !important;
  overflow: visible !important;
  line-height: 1.3 !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-weight: 600 !important;
  font-size: var(--qa-font-metric) !important;
  color: var(--qa-text) !important;
  line-height: 1.3 !important;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  word-break: break-word !important;
  overflow-wrap: anywhere !important;
  max-width: 100% !important;
}
div[data-testid="stMetric"] [data-testid="stMetricValue"] > div,
div[data-testid="stMetric"] [data-testid="stMetricValue"] p,
div[data-testid="stMetric"] [data-testid="stMetricValue"] span {
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  word-break: break-word !important;
}
div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-size: 0.95rem !important;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
}

/* Narrow columns still show full pool values */
div[data-testid="column"] div[data-testid="stMetric"] {
  min-width: 0 !important;
}

/* Bordered containers — more vertical breathing room */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--qa-surface) !important;
  border: 1px solid var(--qa-border) !important;
  border-radius: var(--qa-radius) !important;
  padding: 1.15rem 1.2rem 1.25rem 1.2rem !important;
  margin: 0.85rem 0 1.45rem 0 !important;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.18);
}

/* Tables */
div[data-testid="stDataFrame"],
div[data-testid="stTable"] {
  border: 1px solid var(--qa-border) !important;
  border-radius: 12px !important;
  overflow: hidden !important;
  background: var(--qa-surface-2) !important;
}
div[data-testid="stDataFrame"] * {
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-size: 0.95rem !important;
}
div[data-testid="stDataFrame"] [role="gridcell"],
div[data-testid="stDataFrame"] [role="columnheader"] {
  padding-top: 0.55rem !important;
  padding-bottom: 0.55rem !important;
  line-height: 1.4 !important;
}

/* Buttons / alerts / captions / headers */
.stButton > button {
  background: var(--qa-surface-2) !important;
  color: var(--qa-text) !important;
  border: 1px solid var(--qa-border) !important;
  border-radius: 12px !important;
  font-family: "Sora", sans-serif !important;
  font-weight: 600 !important;
  font-size: 0.95rem !important;
  padding: 0.55rem 1rem !important;
}
.stButton > button:hover {
  border-color: var(--qa-accent) !important;
  color: var(--qa-accent) !important;
}
div[data-testid="stAlert"] {
  border-radius: 12px !important;
  border: 1px solid var(--qa-border) !important;
  background: var(--qa-surface) !important;
  font-size: var(--qa-font-body) !important;
}
hr { border-color: var(--qa-border) !important; opacity: 0.65; }
[data-testid="stCaptionContainer"], .stCaption,
[data-testid="stCaptionContainer"] p {
  color: var(--qa-muted) !important;
  font-family: "Sora", sans-serif !important;
  font-size: var(--qa-font-caption) !important;
  line-height: 1.5 !important;
}
h1, [data-testid="stMarkdownContainer"] h1 {
  font-family: "Sora", sans-serif !important;
  letter-spacing: -0.02em !important;
  color: var(--qa-text) !important;
  font-weight: 700 !important;
  font-size: 1.75rem !important;
  line-height: 1.25 !important;
}
h2, [data-testid="stMarkdownContainer"] h2 {
  font-family: "Sora", sans-serif !important;
  letter-spacing: -0.02em !important;
  color: var(--qa-text) !important;
  font-weight: 600 !important;
  font-size: 1.4rem !important;
  line-height: 1.3 !important;
}
h3, [data-testid="stMarkdownContainer"] h3 {
  font-family: "Sora", sans-serif !important;
  letter-spacing: -0.015em !important;
  color: var(--qa-text) !important;
  font-weight: 600 !important;
  font-size: 1.2rem !important;
  line-height: 1.35 !important;
}

/* Brand + HTML helpers (class-only — no inline var() in page HTML) */
.qa-brand { display: flex; flex-direction: column; gap: 0.3rem; margin: 0 0 0.55rem 0; }
.qa-brand-mark {
  font-family: "Sora", sans-serif; font-weight: 700; font-size: 2.15rem;
  letter-spacing: -0.03em; color: var(--qa-text); line-height: 1.15;
}
.qa-brand-mark span { color: var(--qa-accent); }
.qa-brand-sub { font-size: 1rem; color: var(--qa-muted); line-height: 1.4; }
.qa-live-pill {
  display: inline-block; margin-top: 0.45rem; padding: 0.3rem 0.85rem;
  border-radius: 999px; border: 1px solid rgba(45, 212, 191, 0.4);
  background: rgba(45, 212, 191, 0.12); color: var(--qa-accent-2);
  font-size: 0.88rem; font-weight: 500;
}

.qa-section-title {
  font-family: "Sora", sans-serif; font-weight: 600;
  font-size: var(--qa-font-section); color: var(--qa-text);
  margin: 0 0 0.25rem 0; line-height: 1.3;
}
.qa-section-sub {
  font-family: "Sora", sans-serif; font-size: var(--qa-font-caption);
  color: var(--qa-muted); margin: 0 0 0.75rem 0; line-height: 1.45;
}

.qa-panel {
  background: var(--qa-surface);
  border: 1px solid var(--qa-border);
  border-radius: var(--qa-radius);
  padding: 1.15rem 1.25rem;
  margin: 0.65rem 0 1.25rem 0;
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.16);
}
.qa-panel-row {
  display: flex; flex-wrap: wrap; justify-content: space-between;
  align-items: flex-start; gap: 0.75rem 1.25rem;
}
.qa-panel-accent { border-color: rgba(45, 212, 191, 0.5); }
.qa-panel-up { border-left: 4px solid var(--qa-up); }
.qa-panel-down { border-left: 4px solid var(--qa-down); }
.qa-panel-warn { border-left: 4px solid var(--qa-warn); }
.qa-panel-muted { border-left: 4px solid var(--qa-muted); }
.qa-panel-title {
  font-family: "Sora", sans-serif; font-weight: 700; font-size: 1.15rem;
  color: var(--qa-accent-2); line-height: 1.35;
}
.qa-panel-headline {
  font-family: "Sora", sans-serif; font-weight: 700; font-size: 1.45rem;
  line-height: 1.3;
}
.qa-panel-headline.up { color: var(--qa-up); }
.qa-panel-headline.down { color: var(--qa-down); }
.qa-panel-headline.accent { color: var(--qa-accent); }
.qa-panel-headline.muted { color: var(--qa-muted); }
.qa-panel-body {
  font-family: "Sora", sans-serif; font-size: var(--qa-font-caption);
  color: var(--qa-muted); margin-top: 0.4rem; line-height: 1.5;
}
.qa-mono {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  color: var(--qa-text);
  font-size: 1rem;
}
.qa-footer-rule {
  margin-top: 1.25rem; padding-top: 1rem;
  border-top: 1px solid var(--qa-border);
}

@media (max-width: 768px) {
  :root {
    --qa-font-body: 16px;
    --qa-font-metric: 1.35rem;
    --qa-font-tab: 14.5px;
  }
  .stTabs [data-baseweb="tab"] {
    padding: 0.7rem 0.85rem !important;
  }
  div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.35rem !important;
  }
  div[data-testid="stVerticalBlockBorderWrapper"] {
    margin: 1rem 0 1.55rem 0 !important;
  }
}
"""

_FONT_HREF = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600"
    "&family=Sora:wght@400;500;600;700&display=swap"
)


def inject_theme() -> None:
    """
    Inject fonts + one <style> into the parent document head.

    Uses a zero-height component with JS so stylesheet source never appears
    as Streamlit markdown/page text. Idempotent via element ids.
    """
    import streamlit.components.v1 as components

    # Escape for JS template literal (no f-string over CSS braces).
    css_js = (
        THEME_CSS.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )
    href = _FONT_HREF.replace("\\", "\\\\").replace("'", "\\'")

    script = (
        "<script>(function(){"
        "var doc=window.parent.document;"
        "if(!doc.getElementById('q-alpha-fonts')){"
        "var link=doc.createElement('link');"
        "link.id='q-alpha-fonts';link.rel='stylesheet';"
        "link.href='" + href + "';"
        "doc.head.appendChild(link);"
        "}"
        "var el=doc.getElementById('q-alpha-theme-css');"
        "if(!el){el=doc.createElement('style');el.id='q-alpha-theme-css';"
        "doc.head.appendChild(el);}"
        "el.textContent=`" + css_js + "`;"
        "})();</script>"
    )
    components.html(script, height=0, scrolling=False)


def _md_html(html: str) -> None:
    """Render trusted HTML fragments (classes only) into the main app DOM."""
    import streamlit as st

    st.markdown(html, unsafe_allow_html=True)


def section_header(title: str, subtitle: str = "") -> None:
    """Shared section title (Sora) — use on every tab panel."""
    sub = (
        f'<div class="qa-section-sub">{escape(subtitle)}</div>'
        if subtitle
        else ""
    )
    _md_html(
        f'<div class="qa-section-title">{escape(title)}</div>{sub}'
    )


def brand_block(live_et: str = "") -> None:
    """Top-left brand mark + optional live pill."""
    pill = ""
    if live_et:
        pill = (
            f'<div class="qa-live-pill">Live data · '
            f"{escape(live_et)} ET · every 30m RTH</div>"
        )
    _md_html(
        '<div class="qa-brand">'
        '<div class="qa-brand-mark">Q-<span>ALPHA</span></div>'
        '<div class="qa-brand-sub">Quantitative momentum · paper console</div>'
        f"{pill}</div>"
    )


def regime_banner(spy_regime: str, vix_regime: str, sizing_pct: str) -> None:
    """Live Status regime strip — class modifiers only (no inline CSS vars)."""
    is_bull = spy_regime == "BULL"
    side = "up" if is_bull else "down"
    emoji = "🐂" if is_bull else "🐻"
    vix_cls = "warn" if vix_regime == "ELEVATED" else "up"
    _md_html(
        f'<div class="qa-panel qa-panel-row qa-panel-{side}">'
        f'<div class="qa-panel-headline {side}">{emoji} {escape(spy_regime)} MARKET</div>'
        f'<div class="qa-panel-body">VIX: <b class="qa-panel-headline {vix_cls}">'
        f"{escape(vix_regime)}</b> · Sizing: "
        f'<span class="qa-mono">{escape(sizing_pct)}</span></div></div>'
    )


def status_panel(
    title: str,
    status_text: str,
    time_ago: str,
    message: str,
    *,
    tone: str = "muted",
    icon: str = "",
    status_icon: str = "",
) -> None:
    """System Health row card."""
    tone = tone if tone in ("up", "down", "warn", "muted", "accent") else "muted"
    _md_html(
        f'<div class="qa-panel qa-panel-{tone}">'
        f"<b>{escape(icon)} {escape(title)}</b> {escape(status_icon)} "
        f'<span class="qa-panel-headline {tone}">{escape(status_text)}</span> '
        f'<span class="qa-panel-body">{escape(time_ago)}</span>'
        f'<div class="qa-panel-body">{escape(message)}</div></div>'
    )


def lab_sim_banner() -> None:
    """Strategy Lab SIM disclaimer card."""
    _md_html(
        '<div class="qa-panel qa-panel-accent">'
        '<div class="qa-panel-title">SIM · Polygon paper · not IBKR / not real money</div>'
        '<div class="qa-panel-body">Strategy Lab forward test — dual pools from '
        "<code>live_forward.py</code>. Independent of the live agent / "
        "Supabase paper book.</div></div>"
    )


def lab_ahead_banner(label: str, margin: float, a_val: float, b_val: float) -> None:
    """Who's-ahead strip for Strategy Lab."""
    if label == "TIE":
        tone = "muted"
        headline = "TIE"
    elif "Target" in label or label.startswith("Strategy B"):
        tone = "accent"
        headline = f"{label} ahead by ${margin:,.2f}"
    else:
        tone = "up"
        headline = f"{label} ahead by ${margin:,.2f}"
    _md_html(
        f'<div class="qa-panel qa-panel-{tone}">'
        f'<div class="qa-panel-headline {tone}">{escape(headline)}</div>'
        f'<div class="qa-panel-body qa-mono">'
        f"A ${a_val:,.2f} &nbsp;vs&nbsp; B ${b_val:,.2f}</div></div>"
    )


def footer_rule() -> None:
    _md_html('<div class="qa-footer-rule"></div>')
