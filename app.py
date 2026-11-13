import streamlit as st
import openai
from openai import OpenAI
from dotenv import load_dotenv
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import escape

from theme import inject_theme, app_header, stepper, question

st.set_page_config(page_title="GEO Audit Tool", page_icon="🔎", layout="centered")
inject_theme()

load_dotenv()

MAX_WORKERS = 10        # concurrent web-search calls; lower this if you hit rate limits
MAX_ATTEMPTS = 6        # per query: 1 try + 5 retries, backoff ~1+2+4+8+16s (plus jitter)

# SDK retries are off because the SDK retries every 429 — including "out of
# credits", which can never succeed. search_one() does the retrying instead so
# it can tell a rate limit (retry) from a quota/auth failure (give up at once).
client = OpenAI(max_retries=0)

QUOTA_CODES = {"insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached"}


class FatalAPIError(Exception):
    """An error retrying can't fix (no credits, bad key). Carries a user-facing message."""


def fatal_reason(e):
    """Return a user-facing message if `e` is unrecoverable, else None."""
    if isinstance(e, openai.RateLimitError):
        if {getattr(e, "code", None), getattr(e, "type", None)} & QUOTA_CODES:
            return "The OpenAI account is out of credits. Add credits, then try again."
        return None                                  # a genuine rate limit — retryable
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "OpenAI rejected the API key. Check OPENAI_API_KEY, then try again."
    return None


def search_one(query):
    """One web-search call, retrying rate limits and transient failures with
    exponential backoff. Raises FatalAPIError immediately on quota/auth errors."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.responses.create(
                model="gpt-5.5",
                tools=[{"type": "web_search"}],
                input=query,
            )
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError,
                openai.AuthenticationError, openai.PermissionDeniedError) as e:
            reason = fatal_reason(e)
            if reason:
                raise FatalAPIError(reason) from e
            if attempt == MAX_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1) + random.uniform(0, 1)      # jitter de-syncs the workers
            print(f"[audit] retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.1f}s: {query!r} — {type(e).__name__}")
            time.sleep(delay)


def extract_citations(response):
    """Pull web-search source URLs out of a Responses API result.

    Returns a list of {"url", "title"} dicts, deduped by URL (empty if none).
    Written defensively: an unexpected response shape yields [] rather than an error.
    """
    citations = []
    seen = set()
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", []) or []:
            for ann in getattr(block, "annotations", []) or []:
                if getattr(ann, "type", None) != "url_citation":
                    continue
                url = getattr(ann, "url", "") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                citations.append({"url": url, "title": getattr(ann, "title", "") or ""})
    return citations


def run_audit(business, category, location, competitors):
    """The real, paid audit: 10 web searches, ~£2. Writes the run to
    latest_run.json (overwriting any previous run) for the Results page to read.

    The queries run concurrently (MAX_WORKERS threads) but are saved in their
    original order, so runs stay comparable.

    Returns (saved, failed_count, fatal). A single failed query is skipped so it
    doesn't bin the whole run; `saved` is False only if every query failed, in
    which case the previous saved run is left untouched. `fatal` is a user-facing
    message if any query hit a quota/auth error (not retried), else None."""
    queries = [
        f"best {category} in {location}",
        f"top-rated {category} in {location}",
        f"recommend a {category} in {location}",
        f"who are the best {category} in {location}",
        f"most popular {category} in {location}",
        f"affordable {category} in {location}",
        f"{category} in {location} city centre",
        f"where can I find a good {category} in {location}",
        f"{category} {location}",
        f"is there a good {category} in {location}",
    ]

    total = len(queries)
    results = [None] * total                        # slot per query keeps the original order
    fatal = None
    done = 0
    bar = st.progress(0.0, text=f"0 of {total} answered")
    # Only the main thread touches Streamlit; workers just make the API call.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(search_one, q): i for i, q in enumerate(queries)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                response = future.result()
                results[i] = {
                    "query": queries[i],
                    "answer": response.output_text,
                    "citations": extract_citations(response),
                }
            except FatalAPIError as e:
                fatal = str(e)
                print(f"[audit] query failed (not retried): {queries[i]!r} — {e.__cause__}")
            except Exception as e:                  # one bad call shouldn't bin the whole run
                print(f"[audit] query failed: {queries[i]!r} — {e}")
            done += 1
            bar.progress(done / total, text=f"{done} of {total} answered")
    bar.empty()

    run_queries = [r for r in results if r is not None]
    failed = total - len(run_queries)
    if not run_queries:                             # everything failed — leave the saved run untouched
        return False, failed, fatal

    run_data = {
        "business": business,
        "category": category,
        "location": location,
        "competitors": competitors,          # entered in the wizard, used by Tab 2
        "run_at": datetime.now().isoformat(timespec="minutes"),
        "queries": run_queries,
    }
    with open("latest_run.json", "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=2, ensure_ascii=False)
    return True, failed, fatal


# ---------------- input wizard ----------------
app_header("GEO Audit Tool", "See how often AI search results recommend a business.")

STEP_LABELS = ["Business", "Category", "Location", "Competitors"]

if "step" not in st.session_state:
    st.session_state.step = 1
    st.session_state.answers = {}

ans = st.session_state.answers

stepper(st.session_state.step, STEP_LABELS)

if st.session_state.step == 1:
    with st.container(border=True):
        question("Step 1 of 4", "What's your business name?")
        val = st.text_input(
            "Business name", value=ans.get("business", ""),
            placeholder="e.g. Lazarou Barbers", label_visibility="collapsed",
        )
        _, col2 = st.columns(2)
        if col2.button("Next", type="primary", disabled=not val.strip(), use_container_width=True):
            ans["business"] = val.strip()
            st.session_state.step = 2
            st.rerun()

elif st.session_state.step == 2:
    with st.container(border=True):
        question("Step 2 of 4", "How would someone search for you?", "Be specific — this drives your query topics.")
        val = st.text_input(
            "Category", value=ans.get("category", ""),
            placeholder="e.g. sushi restaurant, CrossFit gym, barber", label_visibility="collapsed",
        )
        col1, col2 = st.columns(2)
        if col1.button("Back", use_container_width=True):
            st.session_state.step = 1
            st.rerun()
        if col2.button("Next", type="primary", disabled=not val.strip(), use_container_width=True):
            ans["category"] = val.strip()
            st.session_state.step = 3
            st.rerun()

elif st.session_state.step == 3:
    with st.container(border=True):
        question("Step 3 of 4", "Where are you?", "Town or city your business serves.")
        val = st.text_input(
            "Location", value=ans.get("location", ""),
            placeholder="e.g. Cardiff", label_visibility="collapsed",
        )
        col1, col2 = st.columns(2)
        if col1.button("Back", use_container_width=True):
            st.session_state.step = 2
            st.rerun()
        if col2.button("Next", type="primary", disabled=not val.strip(), use_container_width=True):
            ans["location"] = val.strip()
            st.session_state.step = 4
            st.rerun()

elif st.session_state.step == 4:
    with st.container(border=True):
        question("Step 4 of 4", "Who are your main competitors?", "Optional — add as many as you like.")

        if "comp_n" not in st.session_state:
            st.session_state.comp_n = 1

        for i in range(st.session_state.comp_n):
            st.text_input(
                f"Competitor {i + 1}", key=f"comp_{i}",
                placeholder=f"Competitor {i + 1}", label_visibility="collapsed",
            )

        if st.button("＋ Add another competitor"):
            st.session_state.comp_n += 1
            st.rerun()

        col1, col2 = st.columns(2)
        if col1.button("Back", use_container_width=True):
            st.session_state.step = 3
            st.rerun()
        if col2.button("Next", type="primary", use_container_width=True):
            ans["competitors"] = [
                st.session_state.get(f"comp_{i}", "").strip()
                for i in range(st.session_state.comp_n)
                if st.session_state.get(f"comp_{i}", "").strip()
            ]
            st.session_state.step = 5
            st.rerun()

elif st.session_state.step == 5:
    comps = ans.get("competitors", [])
    review = {
        "Business": ans.get("business", ""),
        "Category": ans.get("category", ""),
        "Location": ans.get("location", ""),
        "Competitors": ", ".join(comps) if comps else "none entered",
    }
    with st.container(border=True):
        question("Ready to audit", "Check the details before you run.")
        rows = "".join(
            f'<div class="geo-review-row">'
            f'<span class="geo-review-label">{escape(label)}</span>'
            f'<span class="geo-review-val">{escape(value)}</span>'
            f'</div>'
            for label, value in review.items()
        )
        st.markdown(rows, unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        if col1.button("Back", use_container_width=True):
            st.session_state.step = 4
            st.rerun()
        if col2.button("Start audit", type="primary", use_container_width=True):
            ok, failed, fatal = run_audit(ans["business"], ans["category"], ans["location"], comps)
            if not ok:
                st.error(
                    f"{fatal} Nothing was saved." if fatal else
                    "Couldn't reach the search API — nothing was saved. "
                    "Check your API key and connection, then try again."
                )
            elif failed:
                st.warning(
                    f"{failed} of 10 queries failed and were skipped — your results cover the rest."
                    + (f" {fatal}" if fatal else "")
                )
                st.page_link("pages/results.py", label="View your results →")
            else:
                st.switch_page("pages/results.py")