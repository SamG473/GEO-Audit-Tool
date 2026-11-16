"""The wizard and results pages driven through Streamlit's AppTest with a fake
API. No real calls (see helpers.py)."""
import json
import os
import unittest

from helpers import (PASSWORD, QUERIES, REPO, FakeAPI, TempDirTest, querygen_reply, quota_error,
                     text_response, unlock_live)
from streamlit.testing.v1 import AppTest

import extraction
import modes
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
        at = unlock_live(AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30).run())
        at.text_input(key="in_business").set_value(business)
        at.text_input(key="in_town").set_value(town)
        at.button(key="btn_write").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_only_two_inputs_on_the_first_screen(self):
        FakeAPI().install()
        at = unlock_live(AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30).run())
        self.assertEqual([t.label for t in at.text_input], ["Business name", "Town or city"])
        self.assertNotIn(modes.DEMO_LABEL, html(at))                  # live mode carries no demo label

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
        at = unlock_live(AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run())
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
        at = unlock_live(AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run())
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


class DemoModeTest(TempDirTest):
    """Demo mode: an invented business on canned data. FakeAPI() with no handlers
    fails the test on any API call, so every test here also proves "zero calls"."""

    def open(self, password=PASSWORD):
        os.environ["AUDIT_PASSWORD"] = password
        self.api = FakeAPI().install()
        return AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()

    def test_first_screen_is_prefilled_locked_and_labelled(self):
        at = self.open()
        self.assertIn(modes.DEMO_LABEL, html(at))
        name, town = at.text_input(key="demo_business"), at.text_input(key="demo_town")
        self.assertEqual((name.value, town.value), ("Northgate Barbers", "Cardiff"))
        self.assertTrue(name.disabled and town.disabled)

    def test_full_click_through_makes_no_api_calls_and_takes_about_nine_seconds(self):
        import time
        at = self.open()
        at.button(key="btn_write").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        page = html(at)                                               # review screen
        self.assertIn(modes.DEMO_LABEL, page)
        self.assertIn("Review your test questions", page)
        demo = modes.load_demo_run()
        for q in demo["queries"]:
            self.assertIn(q["query"].replace("'", "&#x27;"), page)
        self.assertEqual(len(at.toggle), 0)                           # canned questions aren't editable
        self.assertEqual(len(at.text_input), 0)

        start = time.perf_counter()
        at.button(key="btn_run").click().run()                        # loading page, then results
        elapsed = time.perf_counter() - start
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(8 <= elapsed <= 10.5, f"loading page took {elapsed:.1f}s")
        page = html(at)
        self.assertIn(modes.DEMO_LABEL, page)                         # label on the results too
        self.assertIn("Audit results", page)
        self.assertIn("Northgate Barbers appears in 6 of 10 AI search answers", page)
        self.assertIn("Northgate Barbers is 2nd of the 8 businesses the AI mentioned", page)
        self.assertIn("Fenwick &amp; Blade appears most (7)", page)

        self.assertEqual(self.api.calls, [])
        self.assertEqual(os.listdir("."), [])                         # no latest_run.json, no geo.db written
        self.assertIn("btn_restart", [b.key for b in at.button])      # results page offers Start again

    def test_loading_screen_carries_the_label(self):
        at = self.open()
        at.session_state["step"] = 3
        at.session_state["job"] = {"kind": "demo", "state": "running"}
        at.run()
        self.assertIn(modes.DEMO_LABEL, html(at))

    def test_without_the_env_var_there_is_no_way_into_live_mode(self):
        at = self.open(password="")
        self.assertIn(modes.DEMO_LABEL, html(at))
        self.assertEqual([b.key for b in at.button], ["btn_write"])   # no unlock control at all
        self.assertEqual(len(at.expander), 0)
        at.session_state["mode"] = "live"                             # even a forged flag stays in demo
        at.run()
        self.assertIn(modes.DEMO_LABEL, html(at))
        self.assertTrue(at.text_input(key="demo_business").disabled)

    def test_forged_live_job_without_password_replays_the_demo_instead(self):
        os.environ["AUDIT_PASSWORD"] = ""
        self.api = FakeAPI().install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60)
        at.session_state["mode"] = "live"
        at.session_state["step"] = 3
        at.session_state["answers"] = {"business": "Lazarou Barbers", "town": "Cardiff"}
        at.session_state["qs"] = {"niche": "barber", "niche_confident": True, "queries": QUERIES}
        at.session_state["job"] = {"kind": "live", "state": "pending"}
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(self.api.calls, [])
        self.assertIn("Northgate Barbers", html(at))

    def test_wrong_password_stays_in_demo(self):
        at = self.open()
        at.text_input(key="in_password").set_value("guess")
        at.button(key="btn_unlock").click().run()
        self.assertIn("That password isn't right", at.warning[0].value)
        self.assertIn(modes.DEMO_LABEL, html(at))
        self.assertTrue(at.text_input(key="demo_business").disabled)

    def test_right_password_unlocks_live_and_back_to_demo_relocks(self):
        at = unlock_live(self.open())
        self.assertNotIn(modes.DEMO_LABEL, html(at))
        self.assertFalse(at.text_input(key="in_business").disabled)
        self.assertEqual(at.text_input(key="in_business").value, "")
        at.button(key="btn_demo").click().run()
        self.assertIn(modes.DEMO_LABEL, html(at))


class DemoDataTest(TempDirTest):
    """The committed demo file must be internally honest: its numbers are what the
    app's own code computes from its text, and it can never trigger an API call."""

    def setUp(self):
        super().setUp()
        FakeAPI().install()
        self.run_ = modes.load_demo_run()
        self.answers = [q["answer"] for q in self.run_["queries"]]
        self.args = (self.run_["business"], self.run_["niche"], self.run_["location"])

    def test_two_queries_per_intent_and_none_names_the_business(self):
        intents = [q["intent"] for q in self.run_["queries"]]
        self.assertEqual(intents, [k for k in querygen.INTENT_KEYS for _ in range(2)])
        business, niche, town = self.args
        self.assertEqual(querygen.check_queries(business, niche, town, self.run_["queries"]), [])

    def test_business_counts_match_the_answer_text(self):
        listed = [{"name": b["name"], "variants": b["variants"]} for b in self.run_["businesses"]]
        kept, dropped = extraction.verify(listed, self.answers)
        self.assertEqual(dropped, 0)
        self.assertEqual({b["name"]: b["answers"] for b in kept},
                         {b["name"]: b["answers"] for b in self.run_["businesses"]})

    def test_demo_business_takes_the_free_detection_path(self):
        import detection
        self.assertTrue(detection.has_anchor(*self.args))             # so the results page never calls the judge
        business, niche, town = self.args
        hits = [detection.detect(business, a, niche, town) for a in self.answers]
        self.assertEqual(sum(hits), 6)
        self.assertEqual([b["name"] for b in self.run_["businesses"] if b["is_you"]], ["Northgate Barbers"])

    def test_no_citations_are_invented(self):
        self.assertTrue(all(q["citations"] == [] for q in self.run_["queries"]))


class ResultsPageTest(TempDirTest):
    def render(self, run):
        FakeAPI().install()                                           # any API call fails the test
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(run, f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30)
        at.session_state["mode"] = "live"
        at.run()
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
        at.session_state["mode"] = "live"
        at.session_state["run_notice"] = "1 of 10 questions got no answer and were skipped."
        at.run()
        self.assertIn("1 of 10 questions got no answer", html(at))
        at.run()
        self.assertNotIn("1 of 10 questions got no answer", html(at))

    def test_saved_live_run_is_not_shown_without_unlocking(self):
        FakeAPI().install()
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(self.new_run([]), f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30).run()
        page = html(at)
        self.assertIn(modes.DEMO_LABEL, page)
        self.assertIn("Northgate Barbers", page)
        self.assertNotIn("Lazarou", page)

    def test_live_results_carry_no_demo_label(self):
        self.assertNotIn(modes.DEMO_LABEL, self.render(self.new_run([])))

    def test_demo_lead_form_is_a_locked_example_that_cannot_submit(self):
        FakeAPI().install()
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        fields = {t.label: t for t in at.text_input}
        self.assertEqual((fields["Your name"].value, fields["Your email"].value),
                         ("Jane Smith", "jane@example.com"))
        self.assertTrue(fields["Your name"].disabled and fields["Your email"].disabled)
        send = next(b for b in at.button if b.label == "Send me the breakdown")
        self.assertTrue(send.disabled)
        self.assertIn("In a live audit, this is where a business leaves its details.", html(at))

        send.click().run()                                             # even a forced submit saves nothing
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(len(at.success), 0)
        self.assertFalse(os.path.exists("geo.db"))

    def test_live_lead_form_is_empty_and_usable(self):
        FakeAPI().install()
        with open("latest_run.json", "w", encoding="utf-8") as f:
            json.dump(self.new_run([]), f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30)
        at.session_state["mode"] = "live"
        at.run()
        fields = {t.label: t for t in at.text_input}
        self.assertEqual((fields["Your name"].value, fields["Your email"].value), ("", ""))
        self.assertFalse(fields["Your name"].disabled or fields["Your email"].disabled)
        send = next(b for b in at.button if b.label == "Send me the breakdown")
        self.assertFalse(send.disabled)
        self.assertNotIn("this is where a business leaves its details", html(at))

    def test_no_consultation_link_or_next_banners_in_either_mode(self):
        demo = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30).run()
        live_page = self.render(self.new_run([]))
        for page, captions in ((html(demo), demo.caption), (live_page, [])):
            self.assertNotIn("Next:", page)
            self.assertNotIn('class="geo-nudge"', page)
            self.assertNotIn("calendly", page.lower())
            self.assertFalse(any("consultation" in c.value.lower() for c in captions))

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



class StartAgainTest(TempDirTest):
    """The results page's Start again button. The test harness can't follow a page
    switch back to the wizard, so this is checked in two halves: the click clears
    the run's state, and the wizard's first screen is clean when given that state."""

    LEFTOVERS = {
        "step": 3,
        "answers": {"business": "Lazarou Barbers", "town": "Cardiff"},
        "qs": {"niche": "barber", "niche_confident": True, "queries": QUERIES},
        "job": {"kind": "live", "state": "failed", "message": "x"},
        "run_notice": "1 of 10 questions got no answer.",
        "editing": True,
    }

    def click_start_again(self, mode):
        FakeAPI().install()
        with open("latest_run.json", "w", encoding="utf-8") as f:      # a live session needs a saved run to show
            json.dump({"business": "Lazarou Barbers", "niche": "barber", "location": "Cardiff", "businesses": [],
                       "queries": [{**q, "answer": "Lazarou Barbers.", "citations": []} for q in QUERIES]}, f)
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30)
        for key, value in self.LEFTOVERS.items():
            at.session_state[key] = value
        if mode:
            at.session_state["mode"] = mode
        at.run()
        at.button(key="btn_restart").click().run()
        # the only error allowed is the harness not knowing the wizard page exists
        self.assertTrue(all("Could not find page: `app.py`" in str(e.value) for e in at.exception))
        return at

    def test_click_clears_the_old_run_and_keeps_a_live_session_unlocked(self):
        at = self.click_start_again("live")
        self.assertEqual((at.session_state["step"], at.session_state["answers"]), (1, {}))
        for key in ("qs", "job", "run_notice", "editing"):
            self.assertNotIn(key, at.session_state)
        self.assertEqual(at.session_state["mode"], "live")

    def test_click_in_demo_stays_in_demo(self):
        at = self.click_start_again(None)
        self.assertEqual((at.session_state["step"], at.session_state["answers"]), (1, {}))
        self.assertNotIn("qs", at.session_state)
        self.assertNotIn("mode", at.session_state)                     # never became live

    def first_screen_after_reset(self, mode):
        state = dict(self.LEFTOVERS, mode=mode)
        modes.reset_wizard(state)
        FakeAPI().install()
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30)
        for key, value in state.items():
            at.session_state[key] = value
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_live_first_screen_is_empty_and_still_unlocked(self):
        at = self.first_screen_after_reset("live")
        self.assertNotIn(modes.DEMO_LABEL, html(at))
        self.assertEqual((at.text_input(key="in_business").value, at.text_input(key="in_town").value), ("", ""))
        self.assertFalse(at.text_input(key="in_business").disabled)

    def test_demo_first_screen_is_the_locked_demo(self):
        at = self.first_screen_after_reset("demo")
        self.assertIn(modes.DEMO_LABEL, html(at))
        self.assertIn("Which business are we checking?", html(at))
        self.assertTrue(at.text_input(key="demo_business").disabled)


class PlainTextTest(unittest.TestCase):
    """On-screen text carries no dashes used as punctuation and no exclamation marks."""

    def test_no_dashes_or_exclamation_marks_in_screen_text(self):
        import ast
        for name in ("app.py", os.path.join("pages", "results.py"), "querygen.py", "modes.py"):
            with open(os.path.join(REPO, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                          if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                          and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            logs = {id(c) for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", "") == "print" for c in ast.walk(n)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings and id(node) not in logs
                        and not node.value.startswith(("Step 1 —", "You are helping"))):   # model prompts
                    self.assertNotRegex(node.value, "[—–]|[a-z]!", f"{name}: {node.value[:60]!r}")


if __name__ == "__main__":
    unittest.main()
