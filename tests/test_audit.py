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


class RunAuditTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.reports = []                    # (time, statuses copy, stage) for every report() call
        self._attempts = app.MAX_ATTEMPTS

    def tearDown(self):
        app.MAX_ATTEMPTS = self._attempts
        super().tearDown()

    def report(self, statuses, stage):
        self.reports.append((time.perf_counter(), list(statuses), stage))

    def run_audit(self, search, extract=lambda kw: text_response(EXTRACT_REPLY)):
        self.api = FakeAPI(search=search, extract=extract).install()
        start = time.perf_counter()
        out = app.run_audit("Lazarou Barbers", "barber", "Cardiff", QUERIES, self.report)
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

    def test_each_question_goes_from_waiting_to_done_as_its_answer_arrives(self):
        delays = {q["query"]: 0.1 * n for n, q in enumerate(reversed(QUERIES))}   # last question answers first

        def search(query, attempt):
            time.sleep(delays[query])
            return answer_for(query)

        self.run_audit(search)
        self.assertEqual(self.reports[0][1:], (["waiting"] * 10, "searching"))
        self.assertEqual(self.reports[-1][1:], (["done"] * 10, "finished"))
        seen = []                                                    # distinct states, in order
        for _, statuses, stage in self.reports:
            if stage == "searching" and (not seen or seen[-1] != statuses):
                seen.append(statuses)
        self.assertGreater(len(seen), 5)                             # updated as answers arrived, not at the end
        self.assertEqual(seen[1], ["waiting"] * 9 + ["done"])        # the last question's tick came first
        for before, after in zip(seen, seen[1:]):                    # a tick never reverts
            self.assertTrue(all(b == "waiting" or a == b for b, a in zip(before, after)))

    def test_stages_run_searching_then_extracting_then_finished(self):
        def slow_extract(kwargs):
            time.sleep(0.6)
            return text_response(EXTRACT_REPLY)

        self.run_audit(lambda q, n: answer_for(q), extract=slow_extract)
        stages = [stage for _, _, stage in self.reports]
        collapsed = [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s]
        self.assertEqual(collapsed, ["searching", "extracting", "finished"])
        self.assertGreaterEqual(stages.count("extracting"), 2)       # kept reporting while extraction ran

    def test_reports_keep_coming_while_nothing_arrives(self):
        """The loading page's clock relies on this: no silent gap longer than about a second."""
        def search(query, attempt):
            time.sleep(1.2)
            return answer_for(query)

        self.run_audit(search)
        times = [t for t, _, _ in self.reports]
        self.assertLess(max(b - a for a, b in zip(times, times[1:])), 0.6)

    def test_failed_question_is_marked_failed_not_left_waiting(self):
        bad = QUERIES[5]["query"]
        self.run_audit(lambda q, n: quota_error() if q == bad else answer_for(q))
        final = self.reports[-1][1]
        self.assertEqual(final[5], "failed")
        self.assertEqual(final.count("done"), 9)

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
        self.assertEqual(self.reports[-1][1], ["failed"] * 10)

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



class Placeholder:
    """Stands in for st.empty(): remembers every HTML block drawn into it."""
    def __init__(self):
        self.drawn = []

    def markdown(self, html, unsafe_allow_html=False):
        self.drawn.append(html)


class LoadingPageTest(unittest.TestCase):
    def setUp(self):
        self.slots = []

        def empty():
            self.slots.append(Placeholder())
            return self.slots[-1]

        self._empty, app.st.empty = app.st.empty, empty
        self.report = app.loading_page("Title", "Sub", QUERIES)
        self.summary, self.listing = self.slots

    def tearDown(self):
        app.st.empty = self._empty

    def test_lists_all_ten_questions_waiting_with_extraction_step(self):
        self.report(["waiting"] * 10, "searching")
        page = self.listing.drawn[-1]
        for q in QUERIES:
            self.assertIn(q["query"].replace("'", "&#x27;"), page)
        self.assertEqual(page.count(">Waiting<"), 10)
        self.assertIn("Finding the businesses mentioned…", page)
        self.assertIn(">Up next<", page)
        self.assertIn("0 of 10 answered", self.summary.drawn[-1])
        self.assertIn("0:00", self.summary.drawn[-1])

    def test_rows_tick_as_answers_arrive(self):
        statuses = ["waiting"] * 10
        self.report(statuses, "searching")
        statuses[3] = "done"
        statuses[7] = "failed"
        self.report(statuses, "searching")
        page = self.listing.drawn[-1]
        self.assertEqual((page.count(">Done<"), page.count(">No answer<"), page.count(">Waiting<")), (1, 1, 8))
        self.assertEqual(page.count("geo-tick"), 1)
        self.assertIn("2 of 10 answered", self.summary.drawn[-1])

    def test_extraction_step_is_visible_then_done(self):
        self.report(["done"] * 10, "extracting")
        self.assertIn(">Working<", self.listing.drawn[-1])
        self.report(["done"] * 10, "finished")
        self.assertEqual(self.listing.drawn[-1].count(">Done<"), 11)

    def test_clock_ticks_every_second_and_list_is_not_redrawn_needlessly(self):
        statuses = ["waiting"] * 10
        end = time.perf_counter() + 2.3
        while time.perf_counter() < end:
            self.report(statuses, "searching")
            time.sleep(0.1)
        clocks = [c for c in ("0:00", "0:01", "0:02") if any(c in html for html in self.summary.drawn)]
        self.assertEqual(clocks, ["0:00", "0:01", "0:02"])
        self.assertEqual(len(self.summary.drawn), 3)                 # once per second, not per call
        self.assertEqual(len(self.listing.drawn), 1)                 # unchanged list drawn once

    def test_elapsed_format(self):
        self.assertEqual([app.fmt_elapsed(s) for s in (0, 9.9, 61, 600)], ["0:00", "0:09", "1:01", "10:00"])


if __name__ == "__main__":
    unittest.main()
