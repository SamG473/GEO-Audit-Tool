import json
import sqlite3
from datetime import datetime
from html import escape
from urllib.parse import urlparse

import streamlit as st
from detection import detect
import modes
from extraction import ranking
from theme import inject_theme, demo_banner, donut

# The "Book a consultation" link under the lead form. Live mode only, and hidden
# while this is empty: put the real Calendly link here to show it.
CONSULTATION_URL = ""

st.set_page_config(page_title="Results · GEO Audit", page_icon="🔎", layout="centered")
inject_theme()

# The saved live run is only shown to a session that has unlocked live audits;
# everyone else gets the canned demo audit, clearly labelled.
DEMO = not (st.session_state.get("mode") == "live" and modes.audit_password())
if DEMO:
    demo_banner(modes.DEMO_LABEL)
    run = modes.load_demo_run()
else:
    try:
        with open("latest_run.json", "r", encoding="utf-8") as f:
            run = json.load(f)
    except FileNotFoundError:
        st.info("No audit yet. Run one from the first page.")
        st.stop()

business = run["business"].strip()
category = (run.get("niche") or run.get("category", "")).strip()   # "category" = runs saved before niches
location = run["location"].strip()
answers = [q["answer"] for q in run["queries"]]
total = len(answers)

# --- page header ---
meta_chips = [f"{escape(category)} in {escape(location)}", f"{total} queries"]
run_at = run.get("run_at", "")
if run_at:
    meta_chips.append(f"audited {escape(run_at.replace('T', ' '))}")
st.markdown(
    f'<div class="geo-results-header">'
    f'<div class="geo-results-kicker">Audit results</div>'
    f'<div class="geo-results-title">{escape(business)}</div>'
    f'<div class="geo-results-meta">'
    + "".join(f'<span class="geo-meta-chip">{chip}</span>' for chip in meta_chips)
    + '</div></div>',
    unsafe_allow_html=True,
)


notice = st.session_state.pop("run_notice", None)      # set by the loading page, shown once
if notice:
    st.markdown(f'<div class="geo-note">{escape(notice)}</div>', unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def detect_cached(name, answer, category, location):
    return detect(name, answer, category, location)

tab1, tab2, tab3 = st.tabs(["Your visibility", "You vs competitors", "What's next"])

with tab1:
    visible, missing = [], []
    for q in run["queries"]:
        query_text = " ".join(q["query"].split())          # tidy the double spaces
        if detect_cached(business, q["answer"], category, location):
            visible.append(query_text)
        else:
            missing.append(query_text)

    pct = round(len(visible) / total * 100) if total else 0
    gap = len(missing)

    # hero: donut gauge + plain-language summary
    note = (f"{gap} of the {total} test queries don't mention you yet."
            if gap else "You appear in every query we tested.")
    st.markdown(
        f'<div class="geo-hero">'
        f'{donut(pct, "visible")}'
        f'<div class="geo-hero-body">'
        f'<div class="geo-hero-headline">{escape(business)} appears in '
        f'{len(visible)} of {total} AI search answers.</div>'
        f'<div class="geo-hero-note">{escape(note)}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # side-by-side: where you show up | queries to target
    if visible:
        hit_items = "".join(
            f'<div class="geo-item is-hit"><span class="geo-item-dot"></span>'
            f'<span>{escape(q)}</span></div>'
            for q in visible
        )
    else:
        hit_items = ('<div class="geo-item is-miss"><span class="geo-item-dot"></span>'
                     '<span>Not showing up anywhere yet.</span></div>')

    if missing:
        miss_items = "".join(
            f'<div class="geo-item is-miss"><span class="geo-item-dot"></span>'
            f'<span>{escape(q)}</span></div>'
            for q in missing
        )
    else:
        miss_items = ('<div class="geo-item is-hit"><span class="geo-item-dot"></span>'
                      '<span>You appear in every query.</span></div>')

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(
            f'<div class="geo-card">'
            f'<div class="geo-card-head"><span class="geo-card-title">Where you show up</span>'
            f'<span class="geo-count-badge">{len(visible)}</span></div>'
            f'<div class="geo-card-sub">AI answers that mention {escape(business)}</div>'
            f'{hit_items}</div>',
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            f'<div class="geo-card">'
            f'<div class="geo-card-head"><span class="geo-card-title">Queries to target</span>'
            f'<span class="geo-count-badge is-muted">{gap}</span></div>'
            f'<div class="geo-card-sub">Answers that don\'t mention you yet</div>'
            f'{miss_items}</div>',
            unsafe_allow_html=True,
        )

with tab2:
    # every business the answers mention, extracted at audit time and saved with the run
    businesses = run.get("businesses")

    if businesses is None:
        st.markdown(
            '<div class="geo-empty">No ranking for this run. The list of businesses the AI '
            'mentioned wasn\'t saved with it. Run a new audit to see who the AI recommends.</div>',
            unsafe_allow_html=True,
        )
    else:
        # your count comes from the detection router, so it always matches the first tab
        you_count = sum(detect_cached(business, a, category, location) for a in answers)
        rows = ranking(business, you_count, businesses)
        you = next(r for r in rows if r["is_you"])
        mentioned = sum(1 for r in rows if r["count"])
        leader = rows[0]

        def ordinal(n):
            suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suffix}"

        if not you["count"]:
            headline = f"{business} isn't mentioned in these answers yet."
            if leader["count"]:
                headline += (f" {leader['name']} appears most, in {leader['count']} of {total}.")
        else:
            place = ("joint " if you["joint"] else "") + ordinal(you["rank"])
            headline = (f"{business} is {place} of the {mentioned} businesses the AI mentioned, "
                        f"appearing in {you['count']} of {total} answers.")
            if not leader["is_you"] and leader["count"] > you["count"]:
                headline += f" {leader['name']} appears most ({leader['count']})."

        st.markdown(f'<div class="geo-headline">{escape(headline)}</div>', unsafe_allow_html=True)

        def bars(shown):
            html = ""
            for r in shown:
                bar_pct = round(r["count"] / total * 100) if total else 0
                you_cls = " is-you" if r["is_you"] else ""
                you_tag = '<span class="geo-you-tag">You</span>' if r["is_you"] else ""
                rank = r["rank"] if r["rank"] else "·"
                html += (
                    f'<div class="geo-rankbar{you_cls}">'
                    f'<div class="geo-rank">{rank}</div>'
                    f'<div class="geo-rankbar-main">'
                    f'<div class="geo-rankbar-head">'
                    f'<span class="geo-rankbar-name">{escape(r["name"])}{you_tag}</span>'
                    f'<span class="geo-rankbar-val">{r["count"]} of {total} · {bar_pct}%</span></div>'
                    f'<div class="geo-rankbar-track"><div class="geo-rankbar-fill" style="width:{bar_pct}%"></div></div>'
                    f'</div></div>'
                )
            return html

        TOP = 10
        top = rows[:TOP]
        if you not in top:                       # your row is always on screen
            top.append(you)
        st.markdown(
            f'<div class="geo-section-title">Who the AI recommends</div>'
            f'<div class="geo-section-sub">How many of the {total} answers mention each business. '
            f'A mention isn\'t always an endorsement.</div>'
            f'{bars(top)}',
            unsafe_allow_html=True,
        )
        rest = [r for r in rows[TOP:] if not r["is_you"]]
        if rest:
            with st.expander(f"Show the other {len(rest)} businesses mentioned"):
                st.markdown(bars(rest), unsafe_allow_html=True)

    # where the AI gets its answers — sources cited (empty until a live audit captures them)
    source_counts = {}
    for q in run["queries"]:
        seen = set()
        for cit in q.get("citations", []):
            url = cit.get("url", "")
            if not url:
                continue
            domain = urlparse(url).netloc.replace("www.", "") or url
            if domain in seen:                 # count each source once per answer
                continue
            seen.add(domain)
            if domain not in source_counts:
                source_counts[domain] = {"count": 0, "url": url}
            source_counts[domain]["count"] += 1

    ranked_sources = sorted(source_counts.items(), key=lambda kv: kv[1]["count"], reverse=True)

    if ranked_sources:
        src_rows = "".join(
            f'<div class="geo-src">'
            f'<div class="geo-src-rank">{rank}</div>'
            f'<div class="geo-src-main">'
            f'<div class="geo-src-top">'
            f'<span class="geo-src-name">'
            f'<a href="{escape(data["url"])}" target="_blank" rel="noopener">{escape(domain)}</a></span>'
            f'<span class="geo-src-meta">in {data["count"]} of {total} answers</span></div>'
            f'<div class="geo-src-track"><div class="geo-src-fill" '
            f'style="width:{round(data["count"] / total * 100) if total else 0}%"></div></div>'
            f'</div></div>'
            for rank, (domain, data) in enumerate(ranked_sources, start=1)
        )
    else:
        src_rows = ('<div class="geo-empty">The demo has no source list. In a live audit, the sites '
                    'the AI cited are listed here.</div>' if DEMO else
                    '<div class="geo-empty">This run has no source data. Run a new audit '
                    'and the sites the AI used will be listed here.</div>')

    st.markdown(
        f'<div class="geo-section-title">Where the AI gets its answers</div>'
        f'<div class="geo-section-sub">The sites the AI cited across all {total} answers</div>'
        f'{src_rows}',
        unsafe_allow_html=True,
    )

with tab3:
    missing = [" ".join(q["query"].split()) for q in run["queries"]
               if not detect_cached(business, q["answer"], category, location)]
    visible_count = total - len(missing)

    st.markdown(
        f'<div class="geo-recap">'
        f'<div class="geo-recap-num">{visible_count}/{total}</div>'
        f'<div class="geo-recap-body">'
        f'<div class="geo-recap-title">You\'re showing up in {visible_count} of {total} searches.</div>'
        f'<div class="geo-recap-sub">Want help closing the {len(missing)} you\'re missing? '
        f'Leave your details and we\'ll send the full breakdown.</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        if DEMO:
            st.markdown('<div class="geo-note">In a live audit, this is where a business leaves '
                        'its details.</div>', unsafe_allow_html=True)
        with st.form("lead_form"):
            # in the demo the form is a locked example: filled in, greyed out, nothing can be sent
            name = st.text_input("Your name", value="Jane Smith" if DEMO else "", disabled=DEMO)
            email = st.text_input("Your email", value="jane@example.com" if DEMO else "", disabled=DEMO)
            submitted = st.form_submit_button("Send me the breakdown", type="primary", disabled=DEMO)

    if submitted and not DEMO:
        if "@" not in email or "." not in email:
            st.error("That doesn't look like a valid email. Please check it.")
        else:
            conn = sqlite3.connect("geo.db")
            conn.execute(
                "INSERT INTO leads (created_at, name, email, business, score) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="minutes"), name, email,
                 business, f"{visible_count}/{total}"),
            )
            conn.commit()
            conn.close()
            st.success("Thanks. We'll be in touch with your full breakdown.")

    if CONSULTATION_URL and not DEMO:
        st.caption(f"Prefer to talk it through? [Book a consultation]({CONSULTATION_URL})")

# back to the first screen for another run; the mode (demo or live) is kept
if st.button("Start again", key="btn_restart"):
    modes.reset_wizard(st.session_state)
    st.switch_page("app.py")
