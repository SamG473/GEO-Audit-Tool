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

import extraction
import querygen
from theme import inject_theme, app_header, stepper, question

st.set_page_config(page_title="GEO Audit Tool", page_icon="🔎", layout="centered")
inject_theme()

load_dotenv()

MAX_WORKERS = 10        # concurrent web-search calls; lower this if you hit rate limits
MAX_ATTEMPTS = 6        # per call: 1 try + 5 retries, backoff ~1+2+4+8+16s (plus jitter)

# SDK retries are off because the SDK retries every 429 — including "out of
# credits", which can never succeed. create_with_retry() does the retrying instead
# so it can tell a rate limit (retry) from a quota/auth failure (give up at once).
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


def create_with_retry(label, **kwargs):
    """One Responses API call, retrying rate limits and transient failures with
    exponential backoff. Raises FatalAPIError immediately on quota/auth errors.
    `label` only identifies the call in the retry log."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.responses.create(**kwargs)
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError,
                openai.AuthenticationError, openai.PermissionDeniedError) as e:
            reason = fatal_reason(e)
            if reason:
                raise FatalAPIError(reason) from e
            if attempt == MAX_ATTEMPTS:
                raise
            delay = 2 ** (attempt - 1) + random.uniform(0, 1)      # jitter de-syncs the workers
            print(f"[audit] retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.1f}s: {label!r} — {type(e).__name__}")
            time.sleep(delay)


def search_one(query):
    """One paid web-search call for one query."""
    return create_with_retry(query, model="gpt-5.5", tools=[{"type": "web_search"}], input=query)


def generate_queries(business, town, niche=None):
    """One cheap call (no web search): infer the niche and write the 10 queries.
    Pass `niche` to keep the owner's niche and only rewrite the queries."""
    response = create_with_retry("query generation", **querygen.request_kwargs(business, town, niche))
    return querygen.parse_response(response.output_text, niche_override=niche)


def extract_businesses(business, niche, town, answers):
    """One cheap batched call listing the businesses mentioned across `answers`;
    the counting and the "which one is you" check happen in code (extraction.py)."""
    response = create_with_retry("business extraction", **extraction.request_kwargs(niche, town, answers))
    kept, dropped = extraction.verify(extraction.parse_response(response.output_text), answers)
    if dropped:
        print(f"[audit] extraction listed {dropped} business(es) not found in any answer — dropped")
    return extraction.mark_you(business, kept, niche, town)


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


def run_audit(business, niche, town, queries):
    """The real, paid audit: one web search per query (10, ~£2), then one cheap
    call to list the businesses the answers mention. Writes the run to
    latest_run.json (overwriting any previous run) for the Results page to read.

    `queries` is the approved set: [{"intent", "query"}, ...]. The searches run
    concurrently (MAX_WORKERS threads) but are saved in the given order, so runs
    stay comparable.

    Returns (saved, failed_count, fatal). A single failed query is skipped so it
    doesn't bin the whole run; `saved` is False only if every query failed, in
    which case the previous saved run is left untouched. `fatal` is a user-facing
    message if any call hit a quota/auth error (not retried), else None. If the
    extraction step fails the run is still saved, without the business list."""
    total = len(queries)
    results = [None] * total                        # slot per query keeps the original order
    fatal = None
    done = 0
    bar = st.progress(0.0, text=f"0 of {total} answered")
    # Only the main thread touches Streamlit; workers just make the API call.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(search_one, q["query"]): i for i, q in enumerate(queries)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                response = future.result()
                results[i] = {
                    "intent": queries[i]["intent"],
                    "query": queries[i]["query"],
                    "answer": response.output_text,
                    "citations": extract_citations(response),
                }
            except FatalAPIError as e:
                fatal = str(e)
                print(f"[audit] query failed (not retried): {queries[i]['query']!r} — {e.__cause__}")
            except Exception as e:                  # one bad call shouldn't bin the whole run
                print(f"[audit] query failed: {queries[i]['query']!r} — {e}")
            done += 1
            bar.progress(done / total, text=f"{done} of {total} answered")

    run_queries = [r for r in results if r is not None]
    failed = total - len(run_queries)
    if not run_queries:                             # everything failed — leave the saved run untouched
        bar.empty()
        return False, failed, fatal

    bar.progress(1.0, text="Finding the businesses mentioned…")
    businesses = None                               # None = extraction didn't complete
    try:
        businesses = extract_businesses(business, niche, town, [r["answer"] for r in run_queries])
    except FatalAPIError as e:
        fatal = str(e)
        print(f"[audit] business extraction failed (not retried) — {e.__cause__}")
    except Exception as e:                          # the answers are paid for — keep them regardless
        print(f"[audit] business extraction failed — {e}")
    bar.empty()

    run_data = {
        "business": business,
        "niche": niche,
        "location": town,
        "run_at": datetime.now().isoformat(timespec="minutes"),
        "queries": run_queries,
        "businesses": businesses,            # extracted from the answers, used by Tab 2
    }
    with open("latest_run.json", "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=2, ensure_ascii=False)
    return True, failed, fatal


# ---------------- input wizard ----------------
app_header("GEO Audit Tool", "See how often AI search results recommend a business.")

STEP_LABELS = ["Your business", "Review questions", "Results"]

if "step" not in st.session_state:
    st.session_state.step = 1
    st.session_state.answers = {}

ans = st.session_state.answers

stepper(st.session_state.step, STEP_LABELS)


def open_review(query_set):
    """Move to the review screen with `query_set`. It opens read-only unless the
    model wasn't sure of the niche, in which case it opens straight into editing."""
    st.session_state.qs = query_set
    # applied at the top of the review screen: a widget's state can't be set once
    # the widget exists in the current run (the Rewrite button lives on that screen)
    st.session_state.open_editing = not query_set["niche_confident"]
    st.session_state.edit_gen = st.session_state.get("edit_gen", 0) + 1   # fresh edit fields
    st.session_state.step = 2
    st.rerun()


def write_queries(business, town, niche=None):
    """Generate a query set, showing a calm error and stopping the page on failure."""
    with st.spinner("Writing your test questions…"):
        try:
            return generate_queries(business, town, niche)
        except FatalAPIError as e:
            st.error(str(e))
        except Exception as e:
            print(f"[audit] query generation failed — {e}")
            st.error("Couldn't write the questions just now. Please try again.")
    st.stop()


if st.session_state.step == 1:
    with st.container(border=True):
        question("Step 1 of 2", "Which business are we checking?",
                 "We'll work out what kind of business it is and write the test questions for you.")
        business = st.text_input("Business name", value=ans.get("business", ""),
                                 placeholder="e.g. Lazarou Barbers", key="in_business").strip()
        town = st.text_input("Town or city", value=ans.get("town", ""),
                             placeholder="e.g. Cardiff", key="in_town").strip()
        _, col2 = st.columns(2)
        if col2.button("Write my questions", type="primary", use_container_width=True, key="btn_write"):
            if not business or not town:
                st.warning("Enter both the business name and the town to continue.")
            else:
                ans["business"], ans["town"] = business, town
                # a saved set is reused as-is, so a rerun asks exactly the same questions
                query_set = querygen.load_query_set(business, town)
                if query_set is None:
                    query_set = write_queries(business, town)
                    query_set["saved_at"] = None
                open_review(query_set)

elif st.session_state.step == 2:
    qs = st.session_state.qs
    business, town = ans["business"], ans["town"]
    labels = dict(querygen.INTENTS)

    with st.container(border=True):
        question("Step 2 of 2", "Review your test questions",
                 "These are the questions we'll put to the AI. Your business name is left out on "
                 "purpose: the test is whether the AI recommends you unprompted.")

        if qs.get("saved_at"):
            st.markdown(
                f'<div class="geo-note">Saved on {escape(qs["saved_at"].replace("T", " at "))}. '
                f'Reused so this run is comparable with your last one.</div>',
                unsafe_allow_html=True,
            )

        if "open_editing" in st.session_state:
            st.session_state.editing = st.session_state.pop("open_editing")
        editing = st.toggle("Edit questions", key="editing")
        gen = st.session_state.get("edit_gen", 0)   # in the keys, so a rewrite resets the fields

        if editing:
            if not qs["niche_confident"]:
                st.warning("We couldn't tell what kind of business this is from the name alone. "
                           "Check the niche below. If it's wrong, correct it and rewrite the questions.")
            niche = st.text_input("Niche (the kind of business)", value=qs["niche"],
                                  key=f"in_niche_{gen}").strip()
            if niche != qs["niche"]:
                qs["niche"], qs["niche_confident"] = niche, True      # the owner has now set it
            if st.button("Rewrite questions for this niche", disabled=not qs["niche"], key="btn_rewrite"):
                query_set = write_queries(business, town, qs["niche"])
                query_set["saved_at"] = None
                open_review(query_set)

            for intent, label in querygen.INTENTS:
                st.markdown(f'<div class="geo-intent">{escape(label)}</div>', unsafe_allow_html=True)
                for i, q in enumerate(qs["queries"]):
                    if q["intent"] != intent:
                        continue
                    q["query"] = st.text_input(f"Question {i + 1}", value=q["query"], key=f"q_{gen}_{i}",
                                               label_visibility="collapsed").strip()
        else:
            rows = (f'<div class="geo-review-row"><span class="geo-review-label">Niche</span>'
                    f'<span class="geo-review-val">{escape(qs["niche"])}</span></div>')
            for intent, label in querygen.INTENTS:
                rows += f'<div class="geo-intent">{escape(label)}</div>'
                rows += "".join(f'<div class="geo-query">{escape(q["query"])}</div>'
                                for q in qs["queries"] if q["intent"] == intent)
            st.markdown(rows, unsafe_allow_html=True)

        problems = querygen.check_queries(business, qs["niche"], town, qs["queries"])
        for problem in problems:
            st.warning(problem)

        col1, col2 = st.columns(2)
        if col1.button("Back", use_container_width=True, key="btn_back"):
            st.session_state.step = 1
            st.rerun()
        if col2.button("Run audit", type="primary", disabled=bool(problems),
                       use_container_width=True, key="btn_run"):
            querygen.save_query_set(business, town, qs["niche"], qs["queries"])
            ok, failed, fatal = run_audit(business, qs["niche"], town, qs["queries"])
            if not ok:
                st.error(
                    f"{fatal} Nothing was saved." if fatal else
                    "Couldn't reach the search API — nothing was saved. "
                    "Check your API key and connection, then try again."
                )
            elif failed:
                st.warning(
                    f"{failed} of {len(qs['queries'])} queries failed and were skipped — "
                    f"your results cover the rest."
                    + (f" {fatal}" if fatal else "")
                )
                st.page_link("pages/results.py", label="View your results →")
            else:
                st.switch_page("pages/results.py")
