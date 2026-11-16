"""The wizard and results pages driven through Streamlit's AppTest with a fake
API. No real calls (see helpers.py)."""
import json
import os
import unittest

from helpers import QUERIES, REPO, FakeAPI, TempDirTest, querygen_reply, quota_error, text_response
from streamlit.testing.v1 import AppTest

import querygen

EXTRACT_REPLY = json.dumps({"businesses": [
    {"name": "Lazarou Barbers", "variants": ["Lazarou Barbers"]},
    {"name": "Hard Grind", "variants": ["Hard Grind"]},
]})


def answer_for(query, attempt):
    n = [q["query"] for q in QUERIES].index(query)
    mine = " Lazarou Barbers is another." if n < 3 else ""
    return text_response(f"Try Hard Grind.{mine}")


def field(at, label):
    """A text input by its label (edit-field keys change when the set is rewritten)."""
    return next(t for t in at.text_input if t.label == label)


def html(at):
    return "\n".join(m.value for m in at.markdown)


class WizardTest(TempDirTest):
    def start(self, business="Lazarou Barbers", town="Cardiff", **fake):
        self.api = FakeAPI(**fake).install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30).run()
        at.text_input(key="in_business").set_value(business)
        at.text_input(key="in_town").set_value(town)
        at.button(key="btn_write").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_only_two_inputs_on_the_first_screen(self):
        FakeAPI().install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30).run()
        self.assertEqual([t.label for t in at.text_input], ["Business name", "Town or city"])

    def test_blank_fields_make_no_call(self):
        at = self.start(business="", town="Cardiff")
        self.assertEqual(self.api.calls, [])
        self.assertIn("Enter both", at.warning[0].value)

    def test_review_opens_read_only_with_niche_and_grouped_queries(self):
        at = self.start(generate=lambda kw: text_response(querygen_reply()))
        self.assertEqual(self.api.count("generate"), 1)
        self.assertFalse(at.toggle(key="editing").value)
        self.assertEqual(len(at.text_input), 0)                       # nothing editable yet
        page = html(at)
        self.assertIn("Niche", page)
        self.assertIn("barber", page)
        for _, label in querygen.INTENTS:
            self.assertIn(label, page)
        for q in QUERIES:
            self.assertIn(q["query"].replace("'", "&#x27;"), page)
        self.assertFalse(at.button(key="btn_run").disabled)

    def test_low_confidence_opens_in_edit_mode_with_niche_flagged(self):
        at = self.start(business="Bloom", generate=lambda kw: text_response(querygen_reply("florist", False)))
        self.assertTrue(at.toggle(key="editing").value)
        self.assertIn("couldn't tell what kind of business", at.warning[0].value)
        self.assertEqual(field(at, "Niche (the kind of business)").value, "florist")
        self.assertEqual(len(at.text_input), 11)                      # niche + 10 queries

    def test_edit_toggle_makes_queries_editable_and_edits_stick(self):
        at = self.start(generate=lambda kw: text_response(querygen_reply()))
        at.toggle(key="editing").set_value(True).run()
        self.assertEqual(field(at, "Question 1").value, QUERIES[0]["query"])
        field(at, "Question 1").set_value("Who is the best barber near Cardiff Bay?").run()
        at.toggle(key="editing").set_value(False).run()
        self.assertIn("Who is the best barber near Cardiff Bay?", html(at))

    def test_naming_the_business_in_a_query_blocks_the_run(self):
        at = self.start(generate=lambda kw: text_response(querygen_reply()))
        at.toggle(key="editing").set_value(True).run()
        field(at, "Question 3").set_value("Is Lazarou good for a fade?").run()
        self.assertTrue(any("Query 3 mentions your business by name" in w.value for w in at.warning))
        self.assertTrue(at.button(key="btn_run").disabled)

    def test_rewrite_uses_the_owners_niche(self):
        salon = [{"intent": q["intent"], "query": f"Salon question {i} in Cardiff?"} for i, q in enumerate(QUERIES)]
        replies = iter([querygen_reply("florist", False), querygen_reply("ignored", False, salon)])
        at = self.start(business="Bloom", generate=lambda kw: text_response(next(replies)))
        field(at, "Niche (the kind of business)").set_value("hair salon").run()
        at.button(key="btn_rewrite").click().run()
        self.assertEqual(self.api.count("generate"), 2)
        self.assertIn('"hair salon"', self.api.calls[-1][1]["input"])
        self.assertFalse(at.toggle(key="editing").value)              # owner's niche → confident
        self.assertIn("hair salon", html(at))
        at.toggle(key="editing").set_value(True).run()                # edit fields show the new set
        self.assertEqual(field(at, "Question 1").value, "Salon question 0 in Cardiff?")
        self.assertEqual(field(at, "Niche (the kind of business)").value, "hair salon")

    def test_generation_quota_error_shows_clear_message(self):
        at = self.start(generate=lambda kw: quota_error())
        self.assertIn("out of credits", at.error[0].value)
        self.assertEqual(self.api.count("generate"), 1)

    def test_run_saves_query_set_and_run_then_second_visit_reuses_it(self):
        at = self.start(generate=lambda kw: text_response(querygen_reply()),
                        search=answer_for, extract=lambda kw: text_response(EXTRACT_REPLY))
        at.button(key="btn_run").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(self.api.count("search"), 10)
        searched = sorted(kw["input"] for kind, kw in self.api.calls if kind == "search")
        self.assertEqual(searched, sorted(q["query"] for q in QUERIES))
        self.assertFalse(any("lazarou" in s.lower() for s in searched))   # name never sent in a query
        saved = self.saved_run()
        self.assertEqual([q["query"] for q in saved["queries"]], [q["query"] for q in QUERIES])
        self.assertEqual(querygen.load_query_set("Lazarou Barbers", "Cardiff")["queries"], QUERIES)

        # same business again, typed differently: no model call, same queries, "saved" note
        at2 = self.start(business="lazarou barbers ", town="cardiff")
        self.assertEqual(self.api.calls, [])
        self.assertIn("Reused so this run is comparable", html(at2))
        self.assertIn(QUERIES[0]["query"].replace("'", "&#x27;"), html(at2))


class LoadingFlowTest(TempDirTest):
    """Run audit → separate loading page → results, and the sample replay."""

    def to_review(self, **fake):
        self.api = FakeAPI(generate=lambda kw: text_response(querygen_reply()), **fake).install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()
        at.text_input(key="in_business").set_value("Lazarou Barbers")
        at.text_input(key="in_town").set_value("Cardiff")
        at.button(key="btn_write").click().run()
        return at

    def test_writing_questions_works_through_the_ticking_panel(self):
        import time

        def slow(kw):
            time.sleep(1.2)
            return text_response(querygen_reply())

        self.api = FakeAPI(generate=slow).install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()
        at.text_input(key="in_business").set_value("Lazarou Barbers")
        at.text_input(key="in_town").set_value("Cardiff")
        at.button(key="btn_write").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(self.api.count("generate"), 1)
        self.assertIn("Review your test questions", html(at))

    def test_failed_audit_stays_on_loading_page_and_is_never_rerun(self):
        at = self.to_review(search=lambda q, n: quota_error())
        at.button(key="btn_run").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(at.session_state["step"], 3)
        self.assertIn("out of credits", at.error[0].value)
        self.assertIn("Nothing was saved", at.error[0].value)
        self.assertNotIn("Review your test questions", html(at))       # a separate page, not the review screen
        self.assertEqual(self.api.count("search"), 10)

        at.run()                                                       # a page rerun must not pay again
        self.assertEqual(self.api.count("search"), 10)
        at.button(key="btn_back_to_review").click().run()
        self.assertIn("Review your test questions", html(at))

    def test_partial_failure_goes_to_results_with_a_notice(self):
        bad = QUERIES[4]["query"]
        at = self.to_review(search=lambda q, n: quota_error() if q == bad else answer_for(q, n),
                            extract=lambda kw: text_response(EXTRACT_REPLY))
        at.button(key="btn_run").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(len(self.saved_run()["queries"]), 9)
        page = html(at)                                                # now on the results page
        self.assertIn("Audit results", page)
        self.assertIn("1 of 10 questions got no answer", page)
        self.assertIn("out of credits", page)
        self.assertNotIn("job", at.session_state)

    def test_interrupted_run_is_not_restarted(self):
        at = self.to_review(search=lambda q, n: quota_error())
        at.session_state["step"] = 3
        at.session_state["job"] = {"kind": "live", "state": "running"}  # as left by a reload mid-run
        at.run()
        self.assertEqual(self.api.count("search"), 0)
        self.assertIn("has not been started again", at.info[0].value)

    def test_sample_replay_uses_the_loading_page_and_makes_no_api_calls(self):
        run = {"business": "Lazarou Barbers", "niche": "barber", "location": "Cardiff",
               "run_at": "2026-11-13T14:19", "businesses": [],
               "queries": [{**q, "answer": "Try Hard Grind.", "citations": []} for q in QUERIES]}
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(run, f)
        before = self.saved_run()
        self.api = FakeAPI().install()                                 # any API call fails the test
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()
        self.assertIn("Watch a saved audit of", html(at))
        at.button(key="btn_sample").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.saved_run(), before)
        self.assertIn("Audit results", html(at))                       # went straight to the results
        self.assertNotIn("job", at.session_state)

    def test_no_sample_button_without_a_saved_run(self):
        FakeAPI().install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()
        self.assertEqual([b.key for b in at.button], ["btn_write"])


class ResultsPageTest(TempDirTest):
    def render(self, run):
        FakeAPI().install()                                           # any API call fails the test
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(run, f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return html(at)

    def new_run(self, businesses):
        answers = ["Try Hard Grind. Lazarou Barbers is another."] * 3 + ["Try Hard Grind."] * 7
        return {
            "business": "Lazarou Barbers", "niche": "barber", "location": "Cardiff",
            "run_at": "2026-11-13T10:00",
            "queries": [{**q, "answer": a, "citations": []} for q, a in zip(QUERIES, answers)],
            "businesses": businesses,
        }

    def test_ranking_shows_everyone_mentioned_and_where_you_sit(self):
        page = self.render(self.new_run([
            {"name": "Lazarou Barbers", "variants": ["Lazarou Barbers"], "answers": [0, 1, 2], "is_you": True},
            {"name": "Hard Grind", "variants": ["Hard Grind"], "answers": list(range(10)), "is_you": False},
            {"name": "Sleep When You're Dead", "variants": ["Sleep When You're Dead"], "answers": [4], "is_you": False},
        ]))
        self.assertIn("Lazarou Barbers is 2nd of the 3 businesses the AI mentioned, appearing in 3 of 10", page)
        self.assertIn("Hard Grind appears most (10)", page)
        self.assertIn("10 of 10 · 100%", page)
        self.assertIn("3 of 10 · 30%", page)
        self.assertEqual(page.count('class="geo-you-tag"'), 1)                # you are listed once
        self.assertLess(page.index("Hard Grind</span>"), page.index('class="geo-you-tag"'))

    def test_not_mentioned_is_stated_calmly(self):
        run = self.new_run([{"name": "Hard Grind", "variants": ["Hard Grind"],
                             "answers": list(range(10)), "is_you": False}])
        for q in run["queries"]:
            q["answer"] = "Try Hard Grind."
        page = self.render(run)
        self.assertIn("isn&#x27;t mentioned in these answers yet", page)
        self.assertIn("Hard Grind appears most, in 10 of 10", page)

    def test_notice_from_the_loading_page_is_shown_once(self):
        FakeAPI().install()
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(self.new_run([]), f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30)
        at.session_state["run_notice"] = "1 of 10 questions got no answer and were skipped."
        at.run()
        self.assertIn("1 of 10 questions got no answer", html(at))
        at.run()
        self.assertNotIn("1 of 10 questions got no answer", html(at))

    def test_run_without_extraction_shows_empty_state(self):
        self.assertIn("No ranking for this run", self.render(self.new_run(None)))

    def test_old_format_run_still_renders(self):
        old = {
            "business": "Lazarou Barbers", "category": "barber", "location": "Cardiff",
            "competitors": ["Hard Grind"], "run_at": "2026-06-23T15:57",
            "queries": [{"query": "best barber in Cardiff", "answer": "Lazarou Barbers.", "citations": []}],
        }
        page = self.render(old)
        self.assertIn("appears in 1 of 1 AI search answers", page)
        self.assertIn("No ranking for this run", page)


if __name__ == "__main__":
    unittest.main()
