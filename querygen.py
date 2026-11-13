"""Query generation and saved query sets (no Streamlit, no API calls).

A cheap model infers the niche from the business name + town and writes two
customer-sounding queries for each of five fixed intent types. This module builds
the request, validates the reply, guards against the business name leaking into a
query, and stores the approved set in SQLite so a rerun for the same business
reuses exactly the same queries. The caller (app.py) makes the actual API call.
"""
import json
import re
import sqlite3
from datetime import datetime

from detection import LEGAL_SUFFIXES, distinctive_tokens

MODEL = "gpt-6-luna"
PER_INTENT = 2

# (key saved with each query, label shown on the review screen) — fixed, so every
# run covers the same kinds of question and runs stay comparable.
INTENTS = [
    ("best", "Looking for the best"),
    ("service", "A specific service"),
    ("constraint", "A practical constraint"),
    ("situation", "A situation or occasion"),
    ("recommendation", "A straight recommendation"),
]
INTENT_KEYS = [key for key, _ in INTENTS]

_NICHE_INFER = """Step 1 — Niche. From the name and town alone, work out what kind of business
this is, as a short singular noun phrase a customer would use (for example
"barber", "sushi restaurant", "emergency plumber"). If the name does not make it
clear, give your best guess and set niche_confident to false."""

_NICHE_GIVEN = """Step 1 — Niche. The owner has told us what kind of business this is:
"{niche}". Use it exactly as given and set niche_confident to true."""

_PROMPT = """You are helping test whether AI assistants recommend a local business unprompted.

Business name: "{business}"
Town: {town}

{niche_step}

Step 2 — Queries. Write exactly 2 queries for each of the 5 intent types below,
10 in total. Each is something a real customer in or near {town} might type into
ChatGPT when looking for this kind of business.

Intent types:
- best: wants the best or top-rated business of this kind in {town}.
- service: wants one specific service or product this kind of business
  typically offers. Use a different service in each of the two queries.
- constraint: has a practical constraint: price, opening hours, or
  location/parking/access. Use a different constraint in each.
- situation: has a specific situation or occasion prompting the search. Use a
  different one in each.
- recommendation: asks straight out who you would recommend, with little other
  detail.

Rules:
- Never include the business name "{business}", any distinctive part of it, or
  the name of any other specific business. Generic words for the trade are fine.
  The queries must work for a customer who has never heard of this business.
- Every query must name {town} or a well-known area of it, because the assistant
  will not know where the customer is.
- Write the way people type to a chatbot: natural, conversational, first person
  where it fits, 8 to 25 words. Not keyword strings. Vary how the queries open.
- One question per query. No lists, no follow-up questions, no quotation marks.
- Use the spelling and vocabulary normal for where {town} is.
- Only mention services and details that are typical for this kind of business.
  Do not invent facts about this particular business."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "niche": {"type": "string"},
        "niche_confident": {"type": "boolean"},
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": INTENT_KEYS},
                    "query": {"type": "string"},
                },
                "required": ["intent", "query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["niche", "niche_confident", "queries"],
    "additionalProperties": False,
}


class QueryGenError(ValueError):
    """The model's reply wasn't a usable query set."""


def build_prompt(business, town, niche=None):
    niche_step = _NICHE_GIVEN.format(niche=niche) if niche else _NICHE_INFER
    return _PROMPT.format(business=business, town=town, niche_step=niche_step)


def request_kwargs(business, town, niche=None):
    """Arguments for client.responses.create — one cheap call, no web search."""
    return {
        "model": MODEL,
        "input": build_prompt(business, town, niche),
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_schema", "name": "query_set",
                            "schema": _SCHEMA, "strict": True}},
    }


def parse_response(text, niche_override=None):
    """Validate the model's JSON reply and return
    {"niche", "niche_confident", "queries": [{"intent", "query"}, ...]} with the
    queries in fixed intent order. Raises QueryGenError if it isn't exactly
    PER_INTENT non-blank queries per intent."""
    try:
        data = json.loads(text)
        niche = str(data["niche"]).strip()
        raw = [(str(q["intent"]), " ".join(str(q["query"]).split())) for q in data["queries"]]
    except (ValueError, KeyError, TypeError) as e:
        raise QueryGenError(f"reply was not the expected JSON: {e}") from e

    ordered = []
    for intent in INTENT_KEYS:
        mine = [q for i, q in raw if i == intent and q]
        if len(mine) != PER_INTENT:
            raise QueryGenError(f"expected {PER_INTENT} queries for intent {intent!r}, got {len(mine)}")
        ordered += [{"intent": intent, "query": q} for q in mine]
    if len(ordered) != len(raw):
        raise QueryGenError("reply contained blank queries or unknown intent types")

    if niche_override:
        return {"niche": niche_override, "niche_confident": True, "queries": ordered}
    if not niche:
        raise QueryGenError("reply had no niche")
    return {"niche": niche, "niche_confident": bool(data.get("niche_confident")), "queries": ordered}


def _normalise(text):
    """Lower-case, punctuation to spaces, single-spaced."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def mentions_business(business, query, niche, town):
    """True if `query` names the business. Uses the detection router's notion of a
    distinctive token ("lazarou"); a name with none ("City Barbers") only counts
    when it appears as a whole phrase, so "city centre barbers" is still allowed."""
    q = _normalise(query)
    tokens = distinctive_tokens(business, niche, town)
    if tokens:
        return any(re.search(rf"\b{re.escape(t)}\b", q) for t in tokens)
    phrase = " ".join(w for w in _normalise(business).split() if w not in LEGAL_SUFFIXES)
    return bool(phrase) and re.search(rf"\b{re.escape(phrase)}\b", q) is not None


def check_queries(business, niche, town, queries):
    """Problems that must be fixed before a run, as plain-English messages
    (empty list = good to go). Runs on generated and user-edited queries alike."""
    problems = []
    if not niche.strip():
        problems.append("The niche is blank — say what kind of business this is.")
    for n, q in enumerate(queries, start=1):
        if not q["query"].strip():
            problems.append(f"Query {n} is blank.")
        elif mentions_business(business, q["query"], niche, town):
            problems.append(f"Query {n} mentions your business by name — take the name out, "
                            f"so we test whether the AI recommends you unprompted.")
    return problems


# ---------------- saved query sets (SQLite) ----------------
_DDL = """
    CREATE TABLE IF NOT EXISTS query_sets (
        key          TEXT PRIMARY KEY,
        business     TEXT NOT NULL,
        town         TEXT NOT NULL,
        niche        TEXT NOT NULL,
        queries_json TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
"""


def set_key(business, town):
    """Same business + town → same key, whatever the casing/punctuation/spacing."""
    return f"{_normalise(business)}|{_normalise(town)}"


def load_query_set(business, town, db_path="geo.db"):
    """The saved set for this business + town, or None. Shape matches
    parse_response(), plus "saved_at"."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_DDL)
        row = conn.execute(
            "SELECT niche, queries_json, updated_at FROM query_sets WHERE key = ?",
            (set_key(business, town),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"niche": row[0], "niche_confident": True, "queries": json.loads(row[1]), "saved_at": row[2]}


def save_query_set(business, town, niche, queries, db_path="geo.db"):
    """Store the approved set. Re-saving an identical set leaves its date alone, so
    "saved on" reflects when the wording last changed."""
    key = set_key(business, town)
    payload = json.dumps([{"intent": q["intent"], "query": q["query"]} for q in queries],
                         ensure_ascii=False)
    now = datetime.now().isoformat(timespec="minutes")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_DDL)
        row = conn.execute("SELECT niche, queries_json FROM query_sets WHERE key = ?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO query_sets VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key, business, town, niche, payload, now, now))
        elif (row[0], row[1]) != (niche, payload):
            conn.execute("UPDATE query_sets SET niche = ?, queries_json = ?, updated_at = ? WHERE key = ?",
                         (niche, payload, now, key))
        conn.commit()
    finally:
        conn.close()
