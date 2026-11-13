"""Shared test plumbing. Importing this module makes real OpenAI calls impossible:

- OPENAI_API_KEY is forced to a dummy before the app loads (load_dotenv() does not
  override an existing variable), so nothing could be billed even if a call escaped;
- Responses.create is replaced for the whole process with a function that fails
  loudly unless a test has installed a fake.

Run from the repo root:  python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import threading
import types
import unittest

os.environ["OPENAI_API_KEY"] = "sk-DUMMY-tests-never-call-openai"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import httpx
import openai
from openai.resources.responses import Responses

NS = types.SimpleNamespace


def _unmocked(self, **kwargs):
    raise AssertionError("a test reached the OpenAI API without installing a fake")


Responses.create = _unmocked


def api_error(cls, status, etype, code, message):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls(message, response=httpx.Response(status, request=request),
               body={"message": message, "type": etype, "code": code})


def quota_error():
    return api_error(openai.RateLimitError, 429, "insufficient_quota",
                     "credit_balance_exhausted", "You have no credits remaining.")


def rate_limit_error():
    return api_error(openai.RateLimitError, 429, "tokens", "rate_limit_exceeded", "Rate limit reached")


def auth_error():
    return api_error(openai.AuthenticationError, 401, "invalid_request_error",
                     "invalid_api_key", "Incorrect API key")


def text_response(text, citations=()):
    """A minimal stand-in for a Responses API result."""
    annotations = [NS(type="url_citation", url=u, title=t) for u, t in citations]
    return NS(output_text=text, output=[NS(type="message", content=[NS(annotations=annotations)])])


QUERIES = [
    {"intent": "best", "query": "Who's the best barber in Cardiff right now?"},
    {"intent": "best", "query": "What are the top-rated barbers in Cardiff city centre?"},
    {"intent": "service", "query": "Where can I get a proper skin fade in Cardiff?"},
    {"intent": "service", "query": "I want a hot towel shave in Cardiff, where should I go?"},
    {"intent": "constraint", "query": "Any decent barbers in Cardiff that do a cut for under £20?"},
    {"intent": "constraint", "query": "Which Cardiff barbers are open on a Sunday?"},
    {"intent": "situation", "query": "I've got a wedding on Saturday, where in Cardiff can I get a smart haircut?"},
    {"intent": "situation", "query": "Just moved to Cardiff and need a new regular barber, any ideas?"},
    {"intent": "recommendation", "query": "Can you recommend a barber in Cardiff?"},
    {"intent": "recommendation", "query": "Which barber in Cardiff would you go to?"},
]


def querygen_reply(niche="barber", confident=True, queries=None):
    return json.dumps({"niche": niche, "niche_confident": confident,
                       "queries": QUERIES if queries is None else queries})


class FakeAPI:
    """Stands in for Responses.create. Routes by the kind of call:
    `search(query, attempt)`, `generate(kwargs)` and `extract(kwargs)` each return
    a response or an exception to raise. Records every call."""

    def __init__(self, search=None, generate=None, extract=None):
        self.search, self.generate, self.extract = search, generate, extract
        self.calls = []                    # ("search" | "generate" | "extract", kwargs)
        self.attempts = {}
        self.live = self.peak = 0
        self.lock = threading.Lock()

    def count(self, kind):
        return sum(1 for k, _ in self.calls if k == kind)

    def __call__(self, _self, **kwargs):
        if kwargs.get("tools"):
            kind = "search"
        elif kwargs.get("text", {}).get("format", {}).get("name") == "query_set":
            kind = "generate"
        elif kwargs.get("text", {}).get("format", {}).get("name") == "businesses_mentioned":
            kind = "extract"
        else:
            raise AssertionError(f"unexpected API call: {kwargs.get('model')}")
        with self.lock:
            self.calls.append((kind, kwargs))
            n = self.attempts[kwargs["input"]] = self.attempts.get(kwargs["input"], 0) + 1
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            handler = getattr(self, kind)
            if handler is None:
                raise AssertionError(f"test did not expect a {kind} call")
            result = handler(kwargs["input"], n) if kind == "search" else handler(kwargs)
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            with self.lock:
                self.live -= 1

    def install(self):
        Responses.create = lambda _self, **kwargs: self(_self, **kwargs)
        return self


class TempDirTest(unittest.TestCase):
    """Runs each test in an empty temp directory, so latest_run.json and geo.db
    (both opened by relative path) never touch the repo's copies."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        Responses.create = _unmocked

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()
        Responses.create = _unmocked

    def saved_run(self):
        if not os.path.exists("latest_run.json"):
            return None
        with open("latest_run.json", encoding="utf-8") as f:
            return json.load(f)
