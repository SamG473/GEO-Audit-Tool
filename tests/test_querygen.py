import json
import unittest

from helpers import QUERIES, TempDirTest, querygen_reply

import detection
import querygen


class DetectionNicheTest(unittest.TestCase):
    """The one detection change: a multi-word niche is dropped word by word."""

    def test_single_word_category_unchanged(self):
        self.assertEqual(detection.distinctive_tokens("Lazarou Barbers", "barber", "Cardiff"), ["lazarou"])
        self.assertEqual(detection.distinctive_tokens("Sushi Life", "Restaurant", "Cardiff"), [])
        self.assertEqual(detection.distinctive_tokens("Yakitori1", "Restaurant", "Cardiff"), ["yakitori1"])

    def test_multi_word_niche_drops_each_word(self):
        name = "Cardiff Trichology Clinic"
        self.assertEqual(detection.distinctive_tokens(name, "clinic", "Cardiff"), ["trichology"])
        self.assertEqual(detection.distinctive_tokens(name, "trichology clinic", "Cardiff"), [])

    def test_distinctive_name_survives_multi_word_niche(self):
        self.assertEqual(detection.distinctive_tokens("Lazarou Barbers", "barber shop", "Cardiff"), ["lazarou"])


class PromptTest(unittest.TestCase):
    def test_prompt_names_business_and_town_and_infers_niche(self):
        prompt = querygen.build_prompt("Lazarou Barbers", "Cardiff")
        self.assertIn('"Lazarou Barbers"', prompt)
        self.assertIn("Town: Cardiff", prompt)
        self.assertIn("work out what kind of business", prompt)
        for intent in querygen.INTENT_KEYS:
            self.assertIn(f"- {intent}:", prompt)

    def test_owner_niche_replaces_inference(self):
        prompt = querygen.build_prompt("Bloom", "Cardiff", niche="florist")
        self.assertIn('"florist"', prompt)
        self.assertNotIn("work out what kind of business", prompt)

    def test_request_is_cheap_model_without_web_search(self):
        kwargs = querygen.request_kwargs("Lazarou Barbers", "Cardiff")
        self.assertEqual(kwargs["model"], "gpt-5-mini")
        self.assertNotIn("tools", kwargs)
        self.assertTrue(kwargs["text"]["format"]["strict"])


class ParseTest(unittest.TestCase):
    def test_valid_reply(self):
        out = querygen.parse_response(querygen_reply())
        self.assertEqual(out["niche"], "barber")
        self.assertTrue(out["niche_confident"])
        self.assertEqual(out["queries"], QUERIES)

    def test_reply_is_reordered_into_fixed_intent_order(self):
        out = querygen.parse_response(querygen_reply(queries=list(reversed(QUERIES))))
        self.assertEqual([q["intent"] for q in out["queries"]],
                         [k for k in querygen.INTENT_KEYS for _ in range(2)])

    def test_wrong_count_for_an_intent_is_rejected(self):
        with self.assertRaises(querygen.QueryGenError):
            querygen.parse_response(querygen_reply(queries=QUERIES[:9]))
        three_best = QUERIES[:9] + [{"intent": "best", "query": "Best barbers near Cardiff Bay?"}]
        with self.assertRaises(querygen.QueryGenError):
            querygen.parse_response(querygen_reply(queries=three_best))

    def test_blank_query_is_rejected(self):
        blank = [dict(q) for q in QUERIES]
        blank[3]["query"] = "   "
        with self.assertRaises(querygen.QueryGenError):
            querygen.parse_response(querygen_reply(queries=blank))

    def test_not_json_is_rejected(self):
        with self.assertRaises(querygen.QueryGenError):
            querygen.parse_response("Sure! Here are your queries:")

    def test_low_confidence_is_carried_through(self):
        self.assertFalse(querygen.parse_response(querygen_reply(confident=False))["niche_confident"])

    def test_owner_niche_overrides_the_models(self):
        out = querygen.parse_response(querygen_reply(niche="hairdresser", confident=False),
                                      niche_override="barber")
        self.assertEqual((out["niche"], out["niche_confident"]), ("barber", True))


class NameGuardTest(unittest.TestCase):
    def check(self, business, query, niche="barber"):
        return querygen.mentions_business(business, query, niche, "Cardiff")

    def test_distinctive_name(self):
        self.assertTrue(self.check("Lazarou Barbers", "Is Lazarou any good for a fade in Cardiff?"))
        self.assertTrue(self.check("Lazarou Barbers", "is lazarou's worth it?"))
        self.assertFalse(self.check("Lazarou Barbers", "Who's the best barber in Cardiff?"))
        self.assertFalse(self.check("Lazarou Barbers", "Which barbers in Cardiff are open late?"))

    def test_generic_name_only_matches_as_a_phrase(self):
        self.assertTrue(self.check("City Barbers", "Is City Barbers in Cardiff any good?"))
        self.assertTrue(self.check("City Barbers Ltd", "what about city barbers?"))
        self.assertFalse(self.check("City Barbers", "Which city centre barbers in Cardiff do walk-ins?"))

    def test_check_queries_reports_each_problem(self):
        edited = [dict(q) for q in QUERIES]
        edited[1]["query"] = ""
        edited[4]["query"] = "Is Lazarou Barbers cheap?"
        problems = querygen.check_queries("Lazarou Barbers", "barber", "Cardiff", edited)
        self.assertEqual(len(problems), 2)
        self.assertIn("Query 2 is blank", problems[0])
        self.assertIn("Query 5 mentions your business by name", problems[1])
        self.assertEqual(querygen.check_queries("Lazarou Barbers", "barber", "Cardiff", QUERIES), [])
        self.assertIn("niche is blank", querygen.check_queries("Lazarou Barbers", " ", "Cardiff", QUERIES)[0])

    def test_generated_fixture_never_names_the_business(self):
        for q in QUERIES:
            self.assertFalse(self.check("Lazarou Barbers", q["query"]), q["query"])


class StorageTest(TempDirTest):
    def test_round_trip(self):
        self.assertIsNone(querygen.load_query_set("Lazarou Barbers", "Cardiff"))
        querygen.save_query_set("Lazarou Barbers", "Cardiff", "barber", QUERIES)
        saved = querygen.load_query_set("Lazarou Barbers", "Cardiff")
        self.assertEqual(saved["niche"], "barber")
        self.assertEqual(saved["queries"], QUERIES)
        self.assertTrue(saved["niche_confident"])
        self.assertTrue(saved["saved_at"])

    def test_same_business_whatever_the_typing(self):
        querygen.save_query_set("Lazarou Barbers", "Cardiff", "barber", QUERIES)
        self.assertIsNotNone(querygen.load_query_set("  lazarou   barbers ", "CARDIFF"))
        self.assertIsNotNone(querygen.load_query_set("Lazarou-Barbers!", "cardiff"))
        self.assertIsNone(querygen.load_query_set("Lazarou Barbers", "Swansea"))
        self.assertIsNone(querygen.load_query_set("Lazaro Barbers", "Cardiff"))

    def test_editing_overwrites_and_identical_resave_keeps_the_date(self):
        import sqlite3
        querygen.save_query_set("Lazarou Barbers", "Cardiff", "barber", QUERIES)
        conn = sqlite3.connect("geo.db")
        conn.execute("UPDATE query_sets SET updated_at = '2020-01-01T00:00'")
        conn.commit()
        conn.close()

        querygen.save_query_set("Lazarou Barbers", "Cardiff", "barber", QUERIES)      # identical
        self.assertEqual(querygen.load_query_set("Lazarou Barbers", "Cardiff")["saved_at"], "2020-01-01T00:00")

        edited = [dict(q) for q in QUERIES]
        edited[0]["query"] = "Who is the best barber in Cardiff Bay?"
        querygen.save_query_set("lazarou barbers", "cardiff", "barber", edited)
        saved = querygen.load_query_set("Lazarou Barbers", "Cardiff")
        self.assertEqual(saved["queries"][0]["query"], "Who is the best barber in Cardiff Bay?")
        self.assertNotEqual(saved["saved_at"], "2020-01-01T00:00")
        conn = sqlite3.connect("geo.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM query_sets").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
