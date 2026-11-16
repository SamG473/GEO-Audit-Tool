"""Shared visual theme for the GEO Audit Tool.

One source of truth for the `geo-` design system, used by both the wizard
(`app.py`) and the dashboard (`pages/results.py`). All classes are `geo-`
prefixed so they never collide with Streamlit's own styling. Palette and the
"whitespace + thin dividers, not heavy boxes" rule are fixed — see CLAUDE.md.
"""

import math
from html import escape

import streamlit as st

# palette (kept in sync with .streamlit/config.toml)
PRIMARY = "#2E6F95"
TEXT = "#1A1A1A"
MUTED = "#5A6672"
FAINT = "#8A95A1"
PANEL = "#F4F6F8"
BORDER = "#ECEFF2"
TRACK = "#E1E6EB"

_STYLE = """
<style>
/* hide the auto-generated multipage sidebar nav — the flow is wizard -> audit ->
   results, driven by st.switch_page, not free navigation between pages */
[data-testid="stSidebar"],
[data-testid="stSidebarNav"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] { display: none !important; }

/* a little more breathing room at the top of every page */
.block-container { padding-top: 3rem; max-width: 820px; }

/* ---- app header (wizard + dashboard) ---- */
.geo-app-header { margin: 2px 0 6px 0; }
.geo-app-title {
  font-size: 30px; font-weight: 700; color: #1A1A1A; line-height: 1.15;
  letter-spacing: -0.01em;
}
.geo-app-tagline { font-size: 15px; color: #5A6672; margin-top: 4px; }

/* ---- wizard stepper ---- */
.geo-steps { display: flex; margin: 22px 0 26px 0; }
.geo-step {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  gap: 8px; position: relative;
}
.geo-step-num {
  width: 30px; height: 30px; border-radius: 50%; z-index: 1;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; background: #E1E6EB; color: #8A95A1;
  transition: background .15s ease;
}
.geo-step-label { font-size: 12px; color: #8A95A1; text-align: center; }
.geo-step::before, .geo-step::after {
  content: ""; position: absolute; top: 14px; height: 2px;
  background: #E1E6EB; width: 50%; z-index: 0;
}
.geo-step::before { left: 0; }
.geo-step::after { right: 0; }
.geo-step:first-child::before { display: none; }
.geo-step:last-child::after { display: none; }
.geo-step.is-active .geo-step-num,
.geo-step.is-done .geo-step-num { background: #2E6F95; color: #fff; }
.geo-step.is-active .geo-step-label,
.geo-step.is-done .geo-step-label { color: #1A1A1A; font-weight: 600; }
.geo-step.is-done::before, .geo-step.is-done::after,
.geo-step.is-active::before { background: #2E6F95; }

/* ---- wizard question + review ---- */
.geo-q-kicker {
  font-size: 12px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; color: #2E6F95; margin-bottom: 2px;
}
.geo-q-title { font-size: 19px; font-weight: 600; color: #1A1A1A; margin-bottom: 2px; }
.geo-q-help { font-size: 13px; color: #5A6672; margin-bottom: 4px; }
.geo-review-row {
  display: flex; justify-content: space-between; gap: 16px;
  padding: 11px 0; border-bottom: 1px solid #ECEFF2;
}
.geo-review-row:last-child { border-bottom: none; }
.geo-review-label { font-size: 13px; color: #8A95A1; }
.geo-review-val { font-size: 14px; color: #1A1A1A; font-weight: 600; text-align: right; }

/* ---- dashboard: shared ---- */
.geo-note {
  font-size: 13px; color: #5A6672; background: #F4F6F8; border-radius: 8px;
  padding: 8px 12px; margin: 6px 0 4px 0;
}
.geo-intent {
  font-size: 12px; font-weight: 600; color: #8A95A1; text-transform: uppercase;
  letter-spacing: 0.04em; margin: 16px 0 4px 0;
}
.geo-query {
  font-size: 14px; color: #1A1A1A; padding: 7px 0; border-bottom: 1px solid #ECEFF2;
}
/* ---- loading page + "working" panel ---- */
@keyframes geo-pulse { 0%, 100% { opacity: 0.35; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }
.geo-pulse {
  flex: 0 0 auto; width: 10px; height: 10px; border-radius: 50%; background: #2E6F95;
  animation: geo-pulse 1.2s ease-in-out infinite;
}
.geo-tick, .geo-dash {
  flex: 0 0 auto; width: 18px; height: 18px; border-radius: 50%; font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; margin: 0 -4px;
}
.geo-tick { background: #2E6F95; color: #fff; }
.geo-dash { background: #E1E6EB; color: #5A6672; }
.geo-working {
  display: flex; align-items: center; gap: 14px; background: #F4F6F8; border-radius: 10px;
  padding: 14px 16px; margin: 10px 0 4px 0;
}
.geo-working-body { flex: 1; min-width: 0; }
.geo-working-title { font-size: 15px; font-weight: 600; color: #1A1A1A; }
.geo-working-sub { font-size: 13px; color: #5A6672; margin-top: 2px; }
.geo-run-head { display: flex; justify-content: space-between; align-items: baseline; margin: 14px 0 8px 0; }
.geo-run-count { font-size: 15px; font-weight: 600; color: #1A1A1A; }
.geo-run-clock { font-size: 15px; font-weight: 600; color: #2E6F95; font-variant-numeric: tabular-nums; }
.geo-rankbar-fill.is-live { background: #2E6F95; }
.geo-run-row {
  display: flex; align-items: center; gap: 14px; padding: 10px 2px; border-bottom: 1px solid #ECEFF2;
  font-size: 14px;
}
.geo-run-row:last-child { border-bottom: none; }
.geo-run-text { flex: 1; min-width: 0; }
.geo-run-state { font-size: 12px; white-space: nowrap; }
.geo-run-row.is-waiting { color: #5A6672; }
.geo-run-row.is-waiting .geo-run-state { color: #8A95A1; }
.geo-run-row.is-done { color: #1A1A1A; }
.geo-run-row.is-done .geo-run-state { color: #2E6F95; font-weight: 600; }
.geo-run-row.is-failed, .geo-run-row.is-failed .geo-run-state { color: #8A95A1; }
.geo-run-row.is-queued .geo-pulse { animation: none; background: transparent; border: 2px solid #B8C2CC; }
.geo-run-divider { height: 10px; }

/* ---- demo label (every screen in demo mode) ---- */
.geo-demo {
  font-size: 13px; font-weight: 600; color: #2E6F95; background: #F4F6F8;
  border: 1px solid #ECEFF2; border-radius: 8px; padding: 8px 12px; margin: 0 0 14px 0;
  text-align: center; letter-spacing: 0.01em;
}

.geo-section-title { font-size: 15px; font-weight: 600; color: #1A1A1A; margin: 30px 0 2px 0; }
.geo-section-sub { font-size: 13px; color: #5A6672; margin-bottom: 10px; }
.geo-headline {
  background: #F4F6F8; border: 1px solid #ECEFF2; border-radius: 14px;
  padding: 20px 24px; font-size: 15px; color: #1A1A1A; margin-bottom: 8px;
}
.geo-empty {
  font-size: 13px; color: #8A95A1; background: #F4F6F8;
  border: 1px dashed #D4DAE0; border-radius: 10px; padding: 14px 16px;
}

/* ---- results header ---- */
.geo-results-header { margin: 2px 0 20px 0; }
.geo-results-kicker {
  font-size: 12px; font-weight: 600; letter-spacing: .05em;
  text-transform: uppercase; color: #2E6F95;
}
.geo-results-title {
  font-size: 28px; font-weight: 700; color: #1A1A1A;
  letter-spacing: -0.01em; margin-top: 3px; line-height: 1.1;
}
.geo-results-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.geo-meta-chip {
  font-size: 12px; color: #5A6672; background: #F4F6F8;
  border: 1px solid #ECEFF2; border-radius: 999px; padding: 4px 12px;
}

/* ---- visibility hero + donut ---- */
.geo-hero {
  display: flex; flex-wrap: wrap; align-items: center; gap: 26px;
  background: #F4F6F8; border: 1px solid #ECEFF2; border-radius: 16px;
  padding: 24px 28px; margin-bottom: 6px;
}
.geo-hero-body { flex: 1; min-width: 220px; }
.geo-hero-headline { font-size: 18px; font-weight: 600; color: #1A1A1A; line-height: 1.4; }
.geo-hero-note { font-size: 13px; color: #5A6672; margin-top: 8px; }
.geo-donut { position: relative; width: 148px; height: 148px; flex: 0 0 auto; }
.geo-donut svg { display: block; }
.geo-donut-center {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.geo-donut-pct { font-size: 34px; font-weight: 700; color: #1A1A1A; line-height: 1; }
.geo-donut-sub { font-size: 12px; color: #8A95A1; margin-top: 3px; }

/* ---- hit / miss cards ---- */
.geo-card {
  background: #FFFFFF; border: 1px solid #ECEFF2; border-radius: 14px;
  padding: 18px 20px; height: 100%;
}
.geo-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px; }
.geo-card-title { font-size: 14px; font-weight: 600; color: #1A1A1A; }
.geo-card-sub { font-size: 12px; color: #8A95A1; margin-bottom: 8px; }
.geo-count-badge {
  font-size: 12px; font-weight: 600; color: #2E6F95;
  background: #EAF1F5; border-radius: 999px; padding: 3px 10px;
}
.geo-count-badge.is-muted { color: #5A6672; background: #F4F6F8; }
.geo-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 0; border-bottom: 1px solid #F1F4F6; font-size: 13.5px;
}
.geo-item:last-child { border-bottom: none; }
.geo-item-dot { flex: 0 0 auto; width: 10px; height: 10px; border-radius: 50%; }
.geo-item.is-hit { color: #1A1A1A; }
.geo-item.is-hit .geo-item-dot { background: #2E6F95; }
.geo-item.is-miss { color: #5A6672; }
.geo-item.is-miss .geo-item-dot { background: transparent; border: 2px solid #B8C2CC; }

/* ---- ranked comparison bars ---- */
.geo-rankbar { display: flex; align-items: center; gap: 12px; margin: 14px 0; }
.geo-rank {
  flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
  background: #E1E6EB; color: #5A6672; font-size: 12px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.geo-rankbar-main { flex: 1; min-width: 0; }
.geo-rankbar-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; gap: 10px; }
.geo-rankbar-name { font-size: 14px; color: #1A1A1A; }
.geo-you-tag {
  font-size: 11px; font-weight: 600; color: #2E6F95;
  background: #EAF1F5; border-radius: 999px; padding: 2px 8px; margin-left: 8px;
}
.geo-rankbar-val { font-size: 13px; color: #5A6672; white-space: nowrap; }
.geo-rankbar-track { height: 12px; background: #E1E6EB; border-radius: 999px; overflow: hidden; }
.geo-rankbar-fill { height: 100%; border-radius: 999px; background: #C2CDD6; transition: width .4s ease; }
.geo-rankbar.is-you .geo-rank { background: #2E6F95; color: #fff; }
.geo-rankbar.is-you .geo-rankbar-name { font-weight: 700; }
.geo-rankbar.is-you .geo-rankbar-fill { background: #2E6F95; }

/* ---- sources ---- */
.geo-src { display: flex; align-items: center; gap: 12px; padding: 11px 0; border-bottom: 1px solid #ECEFF2; }
.geo-src:last-child { border-bottom: none; }
.geo-src-rank {
  flex: 0 0 auto; width: 22px; height: 22px; border-radius: 50%;
  background: #F4F6F8; color: #8A95A1; font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.geo-src-main { flex: 1; min-width: 0; }
.geo-src-top { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.geo-src-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.geo-src-name a { color: #2E6F95; text-decoration: none; font-size: 14px; }
.geo-src-meta { font-size: 12px; color: #8A95A1; white-space: nowrap; }
.geo-src-track { height: 6px; background: #E1E6EB; border-radius: 999px; margin-top: 7px; overflow: hidden; }
.geo-src-fill { height: 100%; background: #C2CDD6; border-radius: 999px; }

/* ---- what's next recap ---- */
.geo-recap {
  display: flex; align-items: center; gap: 22px;
  background: #F4F6F8; border: 1px solid #ECEFF2; border-radius: 16px;
  padding: 22px 26px; margin-bottom: 18px;
}
.geo-recap-num { font-size: 42px; font-weight: 700; color: #2E6F95; line-height: 1; flex: 0 0 auto; }
.geo-recap-body { flex: 1; min-width: 200px; }
.geo-recap-title { font-size: 16px; font-weight: 600; color: #1A1A1A; }
.geo-recap-sub { font-size: 13px; color: #5A6672; margin-top: 4px; }
</style>
"""


def inject_theme():
    """Emit the shared `<style>` block. Call once per page, after set_page_config."""
    st.markdown(_STYLE, unsafe_allow_html=True)


def demo_banner(label):
    """The "this is sample data" label shown at the top of every demo-mode screen."""
    st.markdown(f'<div class="geo-demo">{escape(label)}</div>', unsafe_allow_html=True)


def app_header(title, tagline):
    """Designed page header: product title + one-line description."""
    st.markdown(
        f'<div class="geo-app-header">'
        f'<div class="geo-app-title">{escape(title)}</div>'
        f'<div class="geo-app-tagline">{escape(tagline)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def stepper(current, labels):
    """Horizontal numbered step indicator.

    `current` is the 1-based step. Steps before it render as done, the current
    one as active. Pass current > len(labels) to show every step complete.
    """
    cells = ""
    for i, label in enumerate(labels, start=1):
        state = "is-done" if i < current else "is-active" if i == current else ""
        cells += (
            f'<div class="geo-step {state}">'
            f'<div class="geo-step-num">{i}</div>'
            f'<div class="geo-step-label">{escape(label)}</div>'
            f'</div>'
        )
    st.markdown(f'<div class="geo-steps">{cells}</div>', unsafe_allow_html=True)


def question(kicker, title, help_text=""):
    """Styled heading for a wizard question (kicker + title + optional help)."""
    help_html = f'<div class="geo-q-help">{escape(help_text)}</div>' if help_text else ""
    st.markdown(
        f'<div class="geo-q-kicker">{escape(kicker)}</div>'
        f'<div class="geo-q-title">{escape(title)}</div>'
        f'{help_html}',
        unsafe_allow_html=True,
    )


def donut(pct, label="visible"):
    """SVG ring gauge with the percentage in the centre. `pct` is 0–100 (real data)."""
    pct = max(0, min(100, pct))
    r = 52
    circ = 2 * math.pi * r
    offset = circ * (1 - pct / 100)
    return (
        '<div class="geo-donut">'
        '<svg viewBox="0 0 120 120" width="148" height="148">'
        f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="#E1E6EB" stroke-width="13"/>'
        f'<circle cx="60" cy="60" r="{r}" fill="none" stroke="#2E6F95" stroke-width="13" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}" '
        'transform="rotate(-90 60 60)"/>'
        '</svg>'
        f'<div class="geo-donut-center"><span class="geo-donut-pct">{pct}%</span>'
        f'<span class="geo-donut-sub">{escape(label)}</span></div>'
        '</div>'
    )
