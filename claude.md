# GEO Audit Tool — project context for Claude Code

## What this is
A GEO (Generative Engine Optimisation) audit tool: it asks an LLM a set of
customer-style questions and measures how often a given business appears in the
answers, producing a "share of voice" %, plus a ranking of every business the
answers mention. It's a portfolio project to land a
software engineering placement. The reviewer ("Chris") is a senior engineer who
values evaluation methodology, honest trade-offs/limitations, and production
habits over visual polish — reasoning quality matters more than a slick demo.
Test case throughout: "Lazarou Barbers", town "Cardiff" (niche "barber").

## ⚠️ Cost — read first
A full audit is ~10 OpenAI web-search calls and costs ~£2. `latest_run.json`, holds one real run's query→answer
pairs; the Results page reads it with no API calls. Detection over saved answers
(the judge) is cheap and fine — it's the web-search calls that are expensive.
Query generation and business extraction are one `gpt-6-luna` call each (no web
search, well under 1p). Sam approves every real call, cheap ones included.
`python -m unittest discover -s tests` runs everything against a fake API (free).

## Stack / layout
- Python, Streamlit multipage (`pages/`). macOS, zsh, venv at `venv/` (`venv/bin/streamlit`).
- `app.py` — two-screen wizard (`st.session_state`): business name + town, then
  a review screen for the generated queries (read-only, with an edit toggle) +
  `run_audit()` (the paid pipeline: concurrent web searches, then extraction).
- `querygen.py` — query-generation prompt, reply validation, business-name guard,
  saved query sets (no Streamlit).
- `extraction.py` — business-extraction prompt, code-side counting, ranking
  (no Streamlit).
- `pages/results.py` — dashboard, 3 tabs via `st.tabs`
  ("Your visibility", "You vs competitors", "What's next").
- `detection.py` — pure detection logic (no Streamlit).
- `theme.py` — shared `geo-` design system (`inject_theme()`, `donut()`,
  `app_header()`, `stepper()`, `question()`); used by both pages.
- `init_db.py` — SQLite (`geo.db`); `leads` and `query_sets` live, `runs` vestigial.
- `tests/` — stdlib `unittest`, all API calls faked.
- `latest_run.json` — one saved paid run (iterate against this).
- OpenAI Responses API, model `gpt-5.5`, `web_search` tool for audits; plain
  calls for the judge; `gpt-6-luna` for query generation and extraction.

## Queries
Five fixed intent types (best, service, constraint, situation, recommendation),
two queries each. One cheap call infers the niche from name + town and writes
them; they must never contain the business name (checked in code, on generated
and edited queries). The approved set is saved per business + town in
`query_sets`, and a rerun reuses it unchanged so runs stay comparable.

## Detection (don't redesign without asking)
Hybrid router in `detection.py`: `distinctive_tokens()` uses wordfreq zipf
(threshold 2.5) to find rare/coined tokens after dropping category/location/
legal-suffix words → `has_anchor()` → if anchored, free `cheap_match()`
(word-boundary regex); else fall back to the paid LLM `judge()`. Guarantee is
"changes cost, not accuracy" — ambiguous names always hit the judge. Judge
verdicts are cached (`@st.cache_data`).

## Locked decisions — do NOT reopen
- LLM judge replaced the original string matcher (deliberate, validated 5/5
  adversarial). Not a downgrade.
- Hybrid wordfreq routing as above. No spaCy / GLiNER / heavy NER.
- Single saved-run dashboard, not daily tracking-over-time.
- Results via `st.tabs`, not a separate pages/ structure for the three tabs.
- Competitors are extracted from the answers (one batched cheap call at audit
  time, saved to `latest_run.json` under `businesses`), not user-entered. The
  model only lists businesses; counts are computed in code from what literally
  appears in the answers. The user's own count always comes from `detect()`.
- Intent types are fixed; the queries within them are LLM-written and
  user-editable.

## UI / design system
Custom theme lives in `theme.py` as one `<style>` block, injected by both
`app.py` and `pages/results.py` via `inject_theme()`. All classes prefixed `geo-`.

primary `#2E6F95`, page white, secondary bg `#F4F6F8`, text `#1A1A1A`,
borders `#ECEFF2`, muted `#5A6672`/`#8A95A1`, empty-track `#E1E6EB`.
`.streamlit/config.toml` sets the matching Streamlit theme. Style with
whitespace + thin dividers, not heavy boxes. No red for "missing" — keep it calm.

**Honesty rule:** never put fabricated numbers on screen. Every stat/metric must
come from real data or a citable source. (Avoid past mistakes: an invented
"potential visibility %" and a made-up "70% of search is LLM".)

## Known limitations (keep honest — these are for Chris)
- `cheap_match` detects presence, not endorsement; the judge backstops
  dismissive mentions.
- Router is only safe for distinctive/coined names, by design.
- The niche is inferred from the name alone; an unclear name opens the review
  screen in edit mode for the owner to correct.
- Queries are LLM-written, so two businesses get different wording: compare
  across businesses by intent type, not by query.
- Extraction recall is unverified: a business the model fails to list is missing
  from the ranking. (Invented ones can't appear — they match no answer.)
- The ranking counts mentions, not endorsements.

## How to work
- Focused, reviewable changes — Sam reviews diffs and decides. Explain reasoning
  before non-trivial changes. Don't over-engineer or add unrequested scope.
  Push back when something's wrong rather than just agreeing.