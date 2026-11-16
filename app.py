import streamlit as st
import openai
from openai import OpenAI
from dotenv import load_dotenv
import json
import random
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from html import escape

import extraction
import modes
import querygen
from theme import inject_theme, app_header, demo_banner, stepper, question

st.set_page_config(page_title="GEO Audit Tool", page_icon="🔎", layout="centered")
inject_theme()

load_dotenv()

MAX_WORKERS = 10        # concurrent web-search calls; lower this if you hit rate limits
MAX_ATTEMPTS = 6        # per call: 1 try + 5 retries, backoff ~1+2+4+8+16s (plus jitter)
TICK = 0.25             # seconds between progress refreshes while waiting on the API

# demo pacing: seconds before each canned answer "arrives" (7.5s), then extraction — about 9s in all
DEMO_GAPS = [1.2, 0.5, 0.9, 0.4, 1.0, 0.6, 0.8, 0.5, 0.9, 0.7]
DEMO_EXTRACT_SECONDS = 1.5
DEMO_WRITE_SECONDS = 1.5

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


def run_audit(business, niche, town, queries, report=None):
    """The real, paid audit: one web search per query (10, ~£2), then one cheap
    call to list the businesses the answers mention. Writes the run to
    latest_run.json (overwriting any previous run) for the Results page to read.

    `queries` is the approved set: [{"intent", "query"}, ...]. The searches run
    concurrently (MAX_WORKERS threads) but are saved in the given order, so runs
    stay comparable.

    `report(statuses, stage)` is how the loading page follows along. `statuses`
    has one entry per query ("waiting", "done" or "failed"); `stage` is
    "searching", "extracting" or "finished". It is called on every change and at
    least every TICK seconds, always from the calling thread, so it can safely
    draw to the page and keep a clock ticking.

    Returns (saved, failed_count, fatal). A single failed query is skipped so it
    doesn't bin the whole run; `saved` is False only if every query failed, in
    which case the previous saved run is left untouched. `fatal` is a user-facing
    message if any call hit a quota/auth error (not retried), else None. If the
    extraction step fails the run is still saved, without the business list."""
    report = report or (lambda statuses, stage: None)
    total = len(queries)
    results = [None] * total                        # slot per query keeps the original order
    statuses = ["waiting"] * total
    fatal = None
    businesses = None                               # None = extraction didn't complete
    report(statuses, "searching")
    # Only this thread touches Streamlit; workers just make the API calls.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(search_one, q["query"]): i for i, q in enumerate(queries)}
        pending = set(futures)
        while pending:
            finished, pending = wait(pending, timeout=TICK, return_when=FIRST_COMPLETED)
            for future in finished:
                i = futures[future]
                try:
                    response = future.result()
                    results[i] = {
                        "intent": queries[i]["intent"],
                        "query": queries[i]["query"],
                        "answer": response.output_text,
                        "citations": extract_citations(response),
                    }
                    statuses[i] = "done"
                except FatalAPIError as e:
                    fatal = str(e)
                    statuses[i] = "failed"
                    print(f"[audit] query failed (not retried): {queries[i]['query']!r} — {e.__cause__}")
                except Exception as e:              # one bad call shouldn't bin the whole run
                    statuses[i] = "failed"
                    print(f"[audit] query failed: {queries[i]['query']!r} — {e}")
            report(statuses, "searching")

        run_queries = [r for r in results if r is not None]
        failed = total - len(run_queries)
        if not run_queries:                         # everything failed — leave the saved run untouched
            return False, failed, fatal

        # extraction runs on a worker too, so the page keeps ticking while it waits
        job = pool.submit(extract_businesses, business, niche, town, [r["answer"] for r in run_queries])
        while not job.done():
            report(statuses, "extracting")
            wait([job], timeout=TICK)
        try:
            businesses = job.result()
        except FatalAPIError as e:
            fatal = str(e)
            print(f"[audit] business extraction failed (not retried) — {e.__cause__}")
        except Exception as e:                      # the answers are paid for — keep them regardless
            print(f"[audit] business extraction failed — {e}")
    report(statuses, "finished")

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

STEP_LABELS = ["Your business", "Review questions", "Running audit"]

if "step" not in st.session_state:
    st.session_state.step = 1
    st.session_state.answers = {}

# Every visitor starts in demo mode (canned data, zero API calls). Live audits cost
# money, so they need AUDIT_PASSWORD to be set AND entered in this session; this
# one flag gates every path that can reach the API.
DEMO = not (st.session_state.get("mode") == "live" and modes.audit_password())
if DEMO:
    demo_banner(modes.DEMO_LABEL)

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


def fmt_elapsed(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def write_queries(business, town, niche=None, canned=None):
    """Generate a query set with a visible, ticking "working" panel. On failure,
    shows a calm error and stops the page. In demo mode pass `canned`: it is
    returned after a short pause and the model is never called."""
    def canned_after_pause():
        time.sleep(DEMO_WRITE_SECONDS)
        return canned

    panel = st.empty()
    started = time.perf_counter()
    shown = None
    # the call runs on a worker so this thread can keep the clock moving
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = (pool.submit(canned_after_pause) if canned is not None
               else pool.submit(generate_queries, business, town, niche))
        while True:
            clock = fmt_elapsed(time.perf_counter() - started)
            if clock != shown:
                shown = clock
                panel.markdown(
                    f'<div class="geo-working"><span class="geo-pulse"></span>'
                    f'<div class="geo-working-body"><div class="geo-working-title">Writing your test '
                    f'questions…</div><div class="geo-working-sub">Working out what kind of business '
                    f'{escape(business)} is, then writing 10 questions a customer might ask.</div></div>'
                    f'<span class="geo-run-clock">{clock}</span></div>',
                    unsafe_allow_html=True,
                )
            if job.done():
                break
            wait([job], timeout=TICK)
    panel.empty()
    try:
        return job.result()
    except FatalAPIError as e:
        st.error(str(e))
    except Exception as e:
        print(f"[audit] query generation failed — {e}")
        st.error("Couldn't write the questions just now. Please try again.")
    st.stop()


def start_job(kind, **details):
    """Hand over to the loading page. The job is marked "pending" exactly once, so
    a page rerun can never start (and pay for) the same audit twice."""
    st.session_state.job = {"kind": kind, "state": "pending", **details}
    st.session_state.step = 3
    st.rerun()


def loading_page(title, sub, queries, note=None):
    """Draw the loading page and return the `report(statuses, stage)` callback that
    keeps it up to date: a clock that ticks every second, one row per question
    (waiting → done), and the extraction step."""
    question("Running audit", title, sub)
    if note:
        st.markdown(f'<div class="geo-note">{escape(note)}</div>', unsafe_allow_html=True)
    summary, listing = st.empty(), st.empty()
    started = time.perf_counter()
    last = {"summary": None, "listing": None}        # only redraw what changed (keeps animations smooth)
    total = len(queries)
    marks = {
        "waiting": ('is-waiting', '<span class="geo-pulse"></span>', "Waiting"),
        "done": ('is-done', '<span class="geo-tick">✓</span>', "Done"),
        "failed": ('is-failed', '<span class="geo-dash">×</span>', "No answer"),
    }

    def row(cls, mark, text, state):
        return (f'<div class="geo-run-row {cls}">{mark}<span class="geo-run-text">{text}</span>'
                f'<span class="geo-run-state">{state}</span></div>')

    def report(statuses, stage):
        answered = sum(1 for s in statuses if s != "waiting")
        pct = round(answered / total * 100) if total else 0
        html = (f'<div class="geo-run-head"><span class="geo-run-count">{answered} of {total} answered</span>'
                f'<span class="geo-run-clock">{fmt_elapsed(time.perf_counter() - started)}</span></div>'
                f'<div class="geo-rankbar-track"><div class="geo-rankbar-fill is-live" '
                f'style="width:{pct}%"></div></div>')
        if html != last["summary"]:
            last["summary"] = html
            summary.markdown(html, unsafe_allow_html=True)

        rows = "".join(row(*marks[s][:2], escape(q["query"]), marks[s][2]) for q, s in zip(queries, statuses))
        step = {"searching": "waiting", "extracting": "waiting", "finished": "done"}[stage]
        step_state = {"searching": "Up next", "extracting": "Working", "finished": "Done"}[stage]
        step_cls = marks[step][0] + (" is-queued" if stage == "searching" else "")
        rows += ('<div class="geo-run-divider"></div>'
                 + row(step_cls, marks[step][1], "Finding the businesses mentioned…", step_state))
        if rows != last["listing"]:
            last["listing"] = rows
            listing.markdown(rows, unsafe_allow_html=True)

    return report


def replay_demo(run, report):
    """Play the canned demo audit through the loading page. No API calls: the
    answers already exist, they just "arrive" one at a time over DEMO_GAPS."""
    statuses = ["waiting"] * len(run["queries"])
    order = list(range(len(statuses)))
    random.Random(0).shuffle(order)                  # arrive out of order, like a live run

    def pause(seconds, stage):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            report(statuses, stage)
            time.sleep(min(TICK, max(0.0, end - time.perf_counter())))

    for n, i in enumerate(order):
        pause(DEMO_GAPS[n % len(DEMO_GAPS)], "searching")
        statuses[i] = "done"
    report(statuses, "searching")
    pause(DEMO_EXTRACT_SECONDS, "extracting")
    report(statuses, "finished")


def set_mode(mode):
    """Switch between demo and live, starting the wizard afresh. Runs as a button
    callback, i.e. before the page is redrawn."""
    st.session_state.mode = mode
    modes.reset_wizard(st.session_state)


def try_unlock():
    """Button callback: switch to live audits if the typed password is right."""
    if modes.password_ok(st.session_state.get("in_password", "")):
        set_mode("live")
    else:
        st.session_state.bad_password = True


if st.session_state.step == 1 and DEMO:
    demo = modes.load_demo_run()
    with st.container(border=True):
        question("Step 1 of 2", "Which business are we checking?",
                 "The demo uses a made-up business, so these are filled in for you. "
                 "Next we work out what kind of business it is and write the test questions.")
        st.text_input("Business name", value=demo["business"], disabled=True, key="demo_business")
        st.text_input("Town or city", value=demo["location"], disabled=True, key="demo_town")
        _, col2 = st.columns(2)
        if col2.button("Write my questions", type="primary", use_container_width=True, key="btn_write"):
            ans["business"], ans["town"] = demo["business"], demo["location"]
            canned = {"niche": demo["niche"], "niche_confident": True, "saved_at": None,
                      "queries": [{"intent": q["intent"], "query": q["query"]} for q in demo["queries"]]}
            open_review(write_queries(demo["business"], demo["location"], canned=canned))

    if modes.audit_password():                       # live audits exist only if the owner enabled them
        with st.expander("Run a live audit (password needed)"):
            st.text_input("Password", type="password", key="in_password")
            st.button("Continue", key="btn_unlock", on_click=try_unlock)
            if st.session_state.pop("bad_password", False):
                st.warning("That password isn't right.")

elif st.session_state.step == 1:
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

    st.button("Back to the demo", key="btn_demo", on_click=set_mode, args=("demo",))

elif st.session_state.step == 2:
    qs = st.session_state.qs
    business, town = ans["business"], ans["town"]
    labels = dict(querygen.INTENTS)

    with st.container(border=True):
        question("Step 2 of 2", "Review your test questions",
                 "These are the questions we'll ask the AI. Your business name is left out on "
                 "purpose. We want to see if the AI recommends you without being asked.")

        if qs.get("saved_at"):
            st.markdown(
                f'<div class="geo-note">Saved on {escape(qs["saved_at"].replace("T", " at "))}. '
                f'Reused so this run is comparable with your last one.</div>',
                unsafe_allow_html=True,
            )

        if "open_editing" in st.session_state:
            st.session_state.editing = st.session_state.pop("open_editing")
        # the demo's answers are canned to match its questions, so they aren't editable
        editing = False if DEMO else st.toggle("Edit questions", key="editing")
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
            # capital first letter for display only; the stored niche stays as it is,
            # because it is used inside sentences and prompts
            shown_niche = qs["niche"][:1].upper() + qs["niche"][1:]
            rows = (f'<div class="geo-review-row"><span class="geo-review-label">Niche</span>'
                    f'<span class="geo-review-val">{escape(shown_niche)}</span></div>')
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
            if DEMO:
                start_job("demo")
            querygen.save_query_set(business, town, qs["niche"], qs["queries"])
            start_job("live")

elif st.session_state.step == 3:
    job = st.session_state.get("job") or {"kind": "live", "state": "interrupted"}

    with st.container(border=True):
        if job["state"] == "pending" and (DEMO or job["kind"] != "live"):
            job["state"] = "running"
            run = modes.load_demo_run()
            report = loading_page(
                f"Asking the AI about {run['niche']} businesses in {run['location']}",
                f"In a live audit, each question is a real AI search. The questions never name "
                f"{run['business']}.",
                run["queries"],
            )
            replay_demo(run, report)
            st.session_state.step = 1
            del st.session_state["job"]
            st.switch_page("pages/results.py")

        elif job["state"] == "pending":
            job["state"] = "running"                 # set before the first paid call
            qs = st.session_state.qs
            business, town = ans["business"], ans["town"]
            report = loading_page(
                f"Asking the AI about {qs['niche']} businesses in {town}",
                f"Each question is a real AI search. The questions never name {business}.",
                qs["queries"],
            )
            ok, failed, fatal = run_audit(business, qs["niche"], town, qs["queries"], report)
            if ok:
                if failed or fatal:
                    notice = (f"{failed} of {len(qs['queries'])} questions got no answer and were "
                              f"skipped, so these results cover the rest." if failed else
                              "The list of businesses mentioned couldn't be built for this run.")
                    st.session_state.run_notice = notice + (f" {fatal}" if fatal else "")
                st.session_state.step = 1
                del st.session_state["job"]
                st.switch_page("pages/results.py")
            job["state"] = "failed"
            job["message"] = (f"{fatal} Nothing was saved." if fatal else
                              "Couldn't reach the search API, so nothing was saved. "
                              "Check your API key and connection, then try again.")
            st.rerun()

        elif job["state"] == "failed":
            question("Running audit", "The audit didn't complete")
            st.error(job["message"])
            if st.button("Back to your questions", key="btn_back_to_review"):
                st.session_state.step = 2
                st.rerun()

        else:                                        # the page reloaded mid-run: never start it again
            question("Running audit", "This audit was interrupted")
            st.info("The page reloaded while the audit was running, so we lost track of it. "
                    "It has not been started again. Go back to your questions to run it.")
            if st.button("Back to your questions", key="btn_back_to_review"):
                st.session_state.step = 2 if "qs" in st.session_state else 1
                st.rerun()
