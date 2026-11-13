"""Competitors from the answers (no Streamlit, no API calls of its own except the
existing judge).

One cheap model call lists the businesses mentioned across a run's answers. The
model only *lists* them: every count on screen is computed here, by checking
which answers each spelling literally appears in. A business or spelling the
model made up matches nothing and is dropped, so the ranking can't contain an
invented number. The caller (app.py) makes the extraction call.
"""
import json
import re

import detection
from detection import LEGAL_SUFFIXES

MODEL = "gpt-5-mini"
MIN_VARIANT_CHARS = 3        # shorter spellings match too much by accident

_PROMPT = """Below are {n} answers an AI assistant gave to customer questions about
{niche} businesses in {town}. List every specific business the answers mention.

Rules:
- Include a business whether it is recommended, listed as one option, or only
  mentioned in passing.
- Only include individual businesses a customer could visit or hire. Exclude
  review, booking and directory sites, news outlets, areas, streets and landmarks.
- Treat different spellings or shortenings of the same business as one entry.
  Treat branches of one brand as one entry. Do not merge different businesses
  that merely share a word.
- name: the fullest form of the name that appears in the answers.
- variants: every distinct way that business is written in the answers, copied
  exactly, character for character, including the name itself. Never list a
  variant that does not appear in the text.
- Do not add businesses from your own knowledge.

Answers:
{answers}"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "businesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "variants": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "variants"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["businesses"],
    "additionalProperties": False,
}


def build_prompt(niche, town, answers):
    numbered = "\n\n".join(f"[{i}] {a}" for i, a in enumerate(answers, start=1))
    return _PROMPT.format(n=len(answers), niche=niche, town=town, answers=numbered)


def request_kwargs(niche, town, answers):
    """Arguments for client.responses.create — one cheap batched call, no web search."""
    return {
        "model": MODEL,
        "input": build_prompt(niche, town, answers),
        "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_schema", "name": "businesses_mentioned",
                            "schema": _SCHEMA, "strict": True}},
    }


def parse_response(text):
    """The model's reply as [{"name", "variants"}]. Raises ValueError if malformed."""
    try:
        data = json.loads(text)
        return [{"name": str(b["name"]).strip(), "variants": [str(v).strip() for v in b["variants"]]}
                for b in data["businesses"]]
    except (KeyError, TypeError) as e:
        raise ValueError(f"extraction reply was not the expected JSON: {e}") from e


def _appears(variant, answer):
    return re.search(rf"(?<!\w){re.escape(variant)}(?!\w)", answer, re.IGNORECASE) is not None


def verify(businesses, answers):
    """Keep only what is really in the text, and do the counting.

    Returns (kept, dropped): `kept` is [{"name", "variants", "answers"}] where
    `answers` are the 0-based indices of the answers mentioning the business;
    `dropped` is how many listed businesses matched no answer at all."""
    merged = {}                                           # same name listed twice → one entry
    for b in businesses:
        entry = merged.setdefault(b["name"].lower(), {"name": b["name"], "variants": []})
        for v in [b["name"]] + b["variants"]:
            if len(v) >= MIN_VARIANT_CHARS and v.lower() not in {x.lower() for x in entry["variants"]}:
                entry["variants"].append(v)

    kept, dropped = [], 0
    for entry in merged.values():
        real = [v for v in entry["variants"] if any(_appears(v, a) for a in answers)]
        if not real:
            dropped += 1
            continue
        name = entry["name"] if entry["name"] in real else max(real, key=len)
        hits = [i for i, a in enumerate(answers) if any(_appears(v, a) for v in real)]
        kept.append({"name": name, "variants": real, "answers": hits})
    return kept, dropped


def _words(text):
    """Lower-cased words with a trailing plural 's' removed, for loose comparison."""
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w
            for w in re.sub(r"[^\w\s]", " ", text.lower()).split()}


def mark_you(business, businesses, niche, town, judge=None):
    """Flag which extracted entries are the user's own business ("is_you"), so it
    isn't also listed as a competitor. Reuses the detection router:

    - distinctive name → free `cheap_match` against the entry's spellings;
    - generic name → the LLM `judge`, but only for entries that share a word with
      the name (usually none or a few), so this stays at a handful of cheap calls.
    """
    judge = judge or detection.judge
    anchored = detection.has_anchor(business, niche, town)
    generic = _words(niche) | _words(town) | LEGAL_SUFFIXES
    name_words = _words(business)
    core = name_words - generic                      # e.g. {"city"} for "City Barbers"

    for b in businesses:
        text = " / ".join(b["variants"])
        if anchored:
            b["is_you"] = bool(detection.cheap_match(business, text, niche, town))
            continue
        entry_words = _words(text)
        candidate = bool(core & entry_words) if core else name_words <= entry_words
        b["is_you"] = False
        if candidate:
            try:
                b["is_you"] = bool(judge(business, text, niche, town))
            except Exception as e:                   # a failed check must not lose the run
                print(f"[audit] couldn't check whether {b['name']!r} is {business!r}: {e}")
    return businesses


def ranking(business, you_count, businesses):
    """Rows for the "who the AI recommends" table, most-mentioned first:
    [{"name", "count", "is_you", "rank", "joint"}].

    `you_count` comes from the detection router (the same number as the visibility
    tab), not from extraction. Entries flagged is_you are left out — the user's row
    replaces them. Ties share a rank ("joint"). A business with no mentions gets
    rank None."""
    rows = [{"name": b["name"], "count": len(b["answers"]), "is_you": False}
            for b in businesses if not b.get("is_you")]
    rows.append({"name": business, "count": you_count, "is_you": True})
    rows.sort(key=lambda r: (-r["count"], not r["is_you"], r["name"].lower()))
    counts = [r["count"] for r in rows]
    for r in rows:
        r["rank"] = 1 + sum(c > r["count"] for c in counts) if r["count"] else None
        r["joint"] = bool(r["count"]) and counts.count(r["count"]) > 1
    return rows
