"""run_audit against a fake API: concurrency, ordering, progress, retries,
fail-fast, and the extraction step. No real calls (see helpers.py)."""
import json
import time
import unittest

from helpers import (QUERIES, FakeAPI, TempDirTest, auth_error, quota_error,
                     rate_limit_error, text_response)

import app

EXTRACT_REPLY = json.dumps({"businesses": [
    {"name": "Lazarou Barbers", "variants": ["Lazarou Barbers"]},
    {"name": "Hard Grind", "variants": ["Hard Grind"]},
    {"name": "Made Up Barbers", "variants": ["Made Up Barbers"]},
]})


def answer_for(query):
    """Every answer mentions Hard Grind; only the 'best' and 'recommend' ones mention Lazarou."""
    n = [q["query"] for q in QUERIES].index(query)
    mine = " Lazarou Barbers is another." if n in (0, 1, 8) else ""
    return text_response(f"Answer {n}: try Hard Grind.{mine}", citations=[(f"https://example.com/{n}", f"Source {n}")])


class Bar:
    """Records what the progress bar was told."""
    events = []

    def __init__(self, value, text=""):
        Bar.events.append((time.perf_counter(), text))

    def progress(self, value, text=""):
        Bar.events.append((time.perf_counter(), text))

    def empty(self):
        Bar.events.append((time.perf_counter(), "<emptied>"))


class RunAuditTest(TempDirTest):
    def setUp(self):
        super().setUp()
        Bar.events = []
        self._progress, app.st.progress = app.st.progress, Bar
        self._attempts = app.MAX_ATTEMPTS

    def tearDown(self):
        app.st.progress = self._progress
        app.MAX_ATTEMPTS = self._attempts
        super().tearDown()

    def run_audit(self, search, extract=lambda kw: text_response(EXTRACT_REPLY)):
        self.api = FakeAPI(search=search, extract=extract).install()
        start = time.perf_counter()
        out = app.run_audit("Lazarou Barbers", "barber", "Cardiff", QUERIES)
        return out, time.perf_counter() - start

    # ---- concurrency, order, progress (unchanged behaviour from the speed work) ----
    def test_runs_concurrently_and_saves_in_query_order(self):
        delays = [0.50, 0.10, 0.40, 0.05, 0.30, 0.45, 0.15, 0.35, 0.20, 0.25]
        texts = [q["query"] for q in QUERIES]

        def search(query, attempt):
            time.sleep(delays[texts.index(query)])
            return answer_for(query)

        out, elapsed = self.run_audit(search)
        self.assertEqual(out, (True, 0, None))
        self.assertEqual(self.api.peak, 10)                          # all ten in flight at once
        self.assertLess(elapsed, max(delays) + 0.4)                  # ~slowest call, not the 2.75s sum
        saved = self.saved_run()
        self.assertEqual([q["query"] for q in saved["queries"]], texts)
        self.assertEqual([q["intent"] for q in saved["queries"]], [q["intent"] for q in QUERIES])
        for n, q in enumerate(saved["queries"]):
            self.assertTrue(q["answer"].startswith(f"Answer {n}:"))
            self.assertEqual(q["citations"], [{"url": f"https://example.com/{n}", "title": f"Source {n}"}])

    def test_progress_bar_counts_up_then_shows_extraction(self):
        self.run_audit(lambda q, n: answer_for(q))
        self.assertEqual([text for _, text in Bar.events],
                         [f"{k} of 10 answered" for k in range(11)]
                         + ["Finding the businesses mentioned…", "<emptied>"])

    def test_saved_run_shape(self):
        self.run_audit(lambda q, n: answer_for(q))
        saved = self.saved_run()
        self.assertEqual((saved["business"], saved["niche"], saved["location"]),
                         ("Lazarou Barbers", "barber", "Cardiff"))
        self.assertNotIn("competitors", saved)
        self.assertNotIn("category", saved)

    # ---- fail fast / retries ----
    def test_quota_error_fails_fast_without_retrying(self):
        out, elapsed = self.run_audit(lambda q, n: quota_error(), extract=None)
        self.assertEqual(out[:2], (False, 10))
        self.assertIn("out of credits", out[2])
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.api.count("search"), 10)               # one attempt each
        self.assertIsNone(self.saved_run())
        self.assertEqual(Bar.events[-1][1], "<emptied>")

    def test_bad_key_fails_fast(self):
        out, elapsed = self.run_audit(lambda q, n: auth_error(), extract=None)
        self.assertIn("rejected the API key", out[2])
        self.assertEqual(self.api.count("search"), 10)

    def test_quota_on_one_query_keeps_the_other_nine(self):
        bad = QUERIES[5]["query"]
        out, _ = self.run_audit(lambda q, n: quota_error() if q == bad else answer_for(q))
        self.assertEqual(out[:2], (True, 1))
        self.assertIn("out of credits", out[2])
        self.assertEqual([q["query"] for q in self.saved_run()["queries"]],
                         [q["query"] for q in QUERIES if q["query"] != bad])

    def test_rate_limit_is_retried_with_backoff(self):
        slow = QUERIES[8]["query"]
        out, elapsed = self.run_audit(lambda q, n: rate_limit_error() if (q == slow and n == 1) else answer_for(q))
        self.assertEqual(out, (True, 0, None))
        self.assertEqual(self.api.attempts[slow], 2)
        self.assertEqual(self.api.count("search"), 11)
        self.assertGreaterEqual(elapsed, 1.0)                        # backed off ~1s before retrying

    def test_rate_limit_gives_up_after_max_attempts(self):
        app.MAX_ATTEMPTS = 2
        stuck = QUERIES[2]["query"]
        out, _ = self.run_audit(lambda q, n: rate_limit_error() if q == stuck else answer_for(q))
        self.assertEqual(out, (True, 1, None))
        self.assertEqual(self.api.attempts[stuck], 2)

    # ---- extraction step ----
    def test_extraction_runs_once_over_all_answers_and_is_saved(self):
        self.run_audit(lambda q, n: answer_for(q))
        self.assertEqual(self.api.count("extract"), 1)
        prompt = [kw for kind, kw in self.api.calls if kind == "extract"][0]["input"]
        self.assertIn("[1] Answer 0:", prompt)
        self.assertIn("[10] Answer 9:", prompt)
        businesses = {b["name"]: b for b in self.saved_run()["businesses"]}
        self.assertEqual(set(businesses), {"Lazarou Barbers", "Hard Grind"})     # invented one dropped
        self.assertEqual(businesses["Hard Grind"]["answers"], list(range(10)))
        self.assertEqual(businesses["Lazarou Barbers"]["answers"], [0, 1, 8])
        self.assertTrue(businesses["Lazarou Barbers"]["is_you"])
        self.assertFalse(businesses["Hard Grind"]["is_you"])

    def test_extraction_failure_still_saves_the_answers(self):
        out, _ = self.run_audit(lambda q, n: answer_for(q), extract=lambda kw: text_response("not json"))
        self.assertEqual(out, (True, 0, None))
        saved = self.saved_run()
        self.assertEqual(len(saved["queries"]), 10)
        self.assertIsNone(saved["businesses"])

    def test_extraction_quota_error_saves_answers_and_reports_why(self):
        out, _ = self.run_audit(lambda q, n: answer_for(q), extract=lambda kw: quota_error())
        self.assertEqual(out[:2], (True, 0))
        self.assertIn("out of credits", out[2])
        self.assertEqual(self.api.count("extract"), 1)               # not retried
        self.assertIsNone(self.saved_run()["businesses"])

    def test_extraction_rate_limit_is_retried(self):
        replies = iter([rate_limit_error(), text_response(EXTRACT_REPLY)])
        out, _ = self.run_audit(lambda q, n: answer_for(q), extract=lambda kw: next(replies))
        self.assertEqual(out, (True, 0, None))
        self.assertEqual(self.api.count("extract"), 2)
        self.assertEqual(len(self.saved_run()["businesses"]), 2)


if __name__ == "__main__":
    unittest.main()
