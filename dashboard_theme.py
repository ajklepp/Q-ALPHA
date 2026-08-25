"""
Q-ALPHA dashboard visual theme — dark fintech console (cyan/teal accent).

No purple/indigo/violet. Injected once per Streamlit session via inject_theme().
"""
from __future__ import annotations

# Design tokens (keep in sync with .streamlit/config.toml + CSS below)
BG = "#0B1220"
SURFACE = "#121A2B"
SURFACE_2 = "#182235"
BORDER = "#2A3548"
TEXT = "#E8EEF7"
MUTED = "#94A3B8"
ACCENT = "#2DD4BF"
ACCENT_2 = "#22D3EE"
POSITIVE = "#34D399"
NEGATIVE = "#FB7185"
WARN = "#FBBF24"


def inject_theme() -> None:
    """Inject Google Fonts + global CSS. Safe to call once after set_page_config."""
    import streamlit as st

    st.markdown(
        f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --qa-bg: {BG};
  --qa-surface: {SURFACE};
  --qa-surface-2: {SURFACE_2};
  --qa-border: {BORDER};
  --qa-text: {TEXT};
  --qa-muted: {MUTED};
  --qa-accent: {ACCENT};
  --qa-accent-2: {ACCENT_2};
  --qa-pos: {POSITIVE};
  --qa-neg: {NEGATIVE};
  --qa-warn: {WARN};
  --qa-radius: 14px;
}}

html, body, [class*="css"] {{
  font-family: "Sora", sans-serif !important;
}}

.stApp {{
  background: radial-gradient(1200px 600px at 10% -10%, #132033 0%, var(--qa-bg) 55%) !important;
  color: var(--qa-text);
}}

/* Hide Streamlit chrome noise */
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}
header[data-testid="stHeader"] {{
  background: transparent !important;
}}

.block-container {{
  padding-top: 1.25rem !important;
  padding-bottom: 2rem !important;
  max-width: 1280px !important;
}}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{
  gap: 0.35rem;
  background: var(--qa-surface);
  border: 1px solid var(--qa-border);
  border-radius: var(--qa-radius);
  padding: 0.35rem;
}}
.stTabs [data-baseweb="tab"] {{
  border-radius: 10px;
  color: var(--qa-muted);
  font-weight: 500;
  font-size: 0.92rem;
}}
.stTabs [aria-selected="true"] {{
  background: var(--qa-surface-2) !important;
  color: var(--qa-accent) !important;
  border: 1px solid color-mix(in srgb, var(--qa-accent) 35%, transparent);
}}

/* Metrics as elevated cards */
div[data-testid="stMetric"] {{
  background: var(--qa-surface);
  border: 1px solid var(--qa-border);
  border-radius: var(--qa-radius);
  padding: 0.9rem 1rem 0.75rem 1rem;
  box-shadow: none;
}}
div[data-testid="stMetric"] label {{
  color: var(--qa-muted) !important;
  font-size: 0.78rem !important;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-weight: 600 !important;
  font-size: 1.35rem !important;
  color: var(--qa-text) !important;
}}
div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-size: 0.85rem !important;
}}

/* Dataframes / tables — numeric feel */
div[data-testid="stDataFrame"],
div[data-testid="stTable"] {{
  border: 1px solid var(--qa-border);
  border-radius: var(--qa-radius);
  overflow: hidden;
  background: var(--qa-surface);
}}
div[data-testid="stDataFrame"] * {{
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
  font-size: 0.82rem !important;
}}

/* Inputs / buttons */
.stButton > button {{
  background: var(--qa-surface-2);
  color: var(--qa-text);
  border: 1px solid var(--qa-border);
  border-radius: 12px;
  font-family: "Sora", sans-serif !important;
  font-weight: 600;
}}
.stButton > button:hover {{
  border-color: var(--qa-accent);
  color: var(--qa-accent);
}}

/* Alerts */
div[data-testid="stAlert"] {{
  border-radius: 12px;
  border: 1px solid var(--qa-border);
}}

/* Dividers softer */
hr {{
  border-color: var(--qa-border) !important;
  opacity: 0.7;
}}

/* Captions */
.stCaption, [data-testid="stCaptionContainer"] {{
  color: var(--qa-muted) !important;
}}

/* Markdown headers */
h1, h2, h3 {{
  font-family: "Sora", sans-serif !important;
  letter-spacing: -0.02em;
  color: var(--qa-text) !important;
}}

/* Brand hero */
.qa-brand {{
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 0.35rem;
}}
.qa-brand-mark {{
  font-family: "Sora", sans-serif;
  font-weight: 700;
  font-size: 1.85rem;
  letter-spacing: -0.03em;
  color: var(--qa-text);
  line-height: 1.15;
}}
.qa-brand-mark span {{
  color: var(--qa-accent);
}}
.qa-brand-sub {{
  font-size: 0.92rem;
  color: var(--qa-muted);
  font-weight: 400;
}}
.qa-live-pill {{
  display: inline-block;
  margin-top: 0.45rem;
  padding: 0.2rem 0.65rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--qa-accent) 40%, transparent);
  background: color-mix(in srgb, var(--qa-accent) 12%, transparent);
  color: var(--qa-accent-2);
  font-size: 0.78rem;
  font-weight: 500;
}}

/* Generic panel / banner */
.qa-panel {{
  background: var(--qa-surface);
  border: 1px solid var(--qa-border);
  border-radius: var(--qa-radius);
  padding: 1rem 1.15rem;
  margin: 0.5rem 0 1rem 0;
}}
.qa-panel-title {{
  font-family: "Sora", sans-serif;
  font-weight: 700;
  font-size: 1.05rem;
  color: var(--qa-text);
}}
.qa-panel-body {{
  font-size: 0.88rem;
  color: var(--qa-muted);
  margin-top: 0.35rem;
}}

.qa-footer {{
  margin-top: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--qa-border);
  color: var(--qa-muted);
  font-size: 0.8rem;
}}
</style>
        """,
        unsafe_allow_html=True,
    )
