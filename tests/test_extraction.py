import json
import unittest

import helpers  # noqa: F401  (blocks real API calls, sets sys.path)

import extraction

# Five short answers with an obvious hand count:
#   Lazarou            → answers 1, 2, 4   (written "Lazarou Barbers", "Lazarou", "Lazarou's")
#   Sleep When You're Dead → answers 1, 3
#   Hard Grind         → answers 2, 3, 4, 5
#   City Barbers       → answer 5 only   ("city centre barbers" in answer 3 must NOT count)
ANSWERS = [
    "For a classic cut, Lazarou Barbers is a safe bet. Sleep When You're Dead is also popular.",
    "Try Hard Grind on Queen Street, or Lazarou if you want something traditional.",
    "Plenty of city centre barbers do walk-ins: Hard Grind and Sleep When You're Dead are two.",
    "Lazarou's gets strong reviews on Google. HARD GRIND is the trendier choice.",
    "City Barbers is the budget option; Hard Grind costs more. Check Booksy for slots.",
]

MODEL_REPLY = json.dumps({"businesses": [
    {"name": "Lazarou Barbers", "variants": ["Lazarou Barbers", "Lazarou", "Lazarou's"]},
    {"name": "Sleep When You're Dead", "variants": ["Sleep When You're Dead"]},
    {"name": "Hard Grind", "variants": ["Hard Grind", "Hard Grind Cardiff"]},       # 2nd variant invented
    {"name": "City Barbers", "variants": ["City Barbers"]},
    {"name": "The Bearded Barber", "variants": ["The Bearded Barber"]},             # business invented
]})


def by_name(businesses):
    return {b["name"]: b for b in businesses}


class PromptTest(unittest.TestCase):
    def test_answers_are_numbered_and_request_is_cheap(self):
        kwargs = extraction.request_kwargs("barber", "Cardiff", ANSWERS)
        self.assertEqual(kwargs["model"], "gpt-6-luna")
        self.assertNotIn("tools", kwargs)
        self.assertIn("Below are 5 answers", kwargs["input"])
        self.assertIn("[1] For a classic cut", kwargs["input"])
        self.assertIn("[5] City Barbers is", kwargs["input"])

    def test_malformed_reply_raises(self):
        with self.assertRaises(ValueError):
            extraction.parse_response("not json")
        with self.assertRaises(ValueError):
            extraction.parse_response('{"shops": []}')


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.kept, self.dropped = extraction.verify(extraction.parse_response(MODEL_REPLY), ANSWERS)
        self.kept_by_name = by_name(self.kept)

    def test_invented_business_is_dropped(self):
        self.assertEqual(self.dropped, 1)
        self.assertNotIn("The Bearded Barber", self.kept_by_name)

    def test_invented_variant_is_dropped(self):
        self.assertEqual(self.kept_by_name["Hard Grind"]["variants"], ["Hard Grind"])

    def test_counts_match_the_hand_count(self):
        counts = {name: b["answers"] for name, b in self.kept_by_name.items()}
        self.assertEqual(counts, {
            "Lazarou Barbers": [0, 1, 3],
            "Sleep When You're Dead": [0, 2],
            "Hard Grind": [1, 2, 3, 4],             # case-insensitive: "HARD GRIND" counts
            "City Barbers": [4],                    # not "city centre barbers" in answer 3
        })

    def test_duplicate_entries_are_merged(self):
        reply = [{"name": "Hard Grind", "variants": ["Hard Grind"]},
                 {"name": "hard grind", "variants": ["HARD GRIND"]}]
        kept, _ = extraction.verify(reply, ANSWERS)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["answers"], [1, 2, 3, 4])

    def test_too_short_variants_are_ignored(self):
        kept, dropped = extraction.verify([{"name": "Or", "variants": ["or"]}], ANSWERS)
        self.assertEqual((kept, dropped), ([], 1))


class MarkYouTest(unittest.TestCase):
    def setUp(self):
        self.kept, _ = extraction.verify(extraction.parse_response(MODEL_REPLY), ANSWERS)

    def never_judge(self, *args):
        raise AssertionError("judge must not be called for a distinctive name")

    def test_distinctive_name_uses_free_match_only(self):
        marked = by_name(extraction.mark_you("Lazarou Barbers", self.kept, "barber", "Cardiff",
                                             judge=self.never_judge))
        self.assertEqual({n for n, b in marked.items() if b["is_you"]}, {"Lazarou Barbers"})

    def test_generic_name_asks_the_judge_only_about_lookalikes(self):
        asked = []

        def judge(name, text, niche, town):
            asked.append(text)
            return True

        marked = by_name(extraction.mark_you("City Barbers", self.kept, "barber", "Cardiff", judge=judge))
        self.assertEqual(asked, ["City Barbers"])                   # 1 call, not 4
        self.assertEqual({n for n, b in marked.items() if b["is_you"]}, {"City Barbers"})

    def test_judge_failure_does_not_lose_the_list(self):
        def judge(*args):
            raise RuntimeError("API down")

        marked = extraction.mark_you("City Barbers", self.kept, "barber", "Cardiff", judge=judge)
        self.assertEqual(len(marked), 4)
        self.assertFalse(any(b["is_you"] for b in marked))


class RankingTest(unittest.TestCase):
    def setUp(self):
        kept, _ = extraction.verify(extraction.parse_response(MODEL_REPLY), ANSWERS)
        self.businesses = extraction.mark_you("Lazarou Barbers", kept, "barber", "Cardiff")

    def test_your_row_replaces_your_extracted_entry(self):
        rows = extraction.ranking("Lazarou Barbers", 3, self.businesses)
        self.assertEqual([(r["name"], r["count"], r["rank"], r["joint"], r["is_you"]) for r in rows], [
            ("Hard Grind", 4, 1, False, False),
            ("Lazarou Barbers", 3, 2, False, True),
            ("Sleep When You're Dead", 2, 3, False, False),
            ("City Barbers", 1, 4, False, False),
        ])

    def test_ties_share_a_rank_and_you_are_listed_first_among_equals(self):
        rows = extraction.ranking("Lazarou Barbers", 4, self.businesses)
        self.assertEqual([(r["name"], r["rank"], r["joint"]) for r in rows[:3]], [
            ("Lazarou Barbers", 1, True),
            ("Hard Grind", 1, True),
            ("Sleep When You're Dead", 3, False),
        ])

    def test_not_mentioned_has_no_rank_and_sits_last(self):
        rows = extraction.ranking("Lazarou Barbers", 0, self.businesses)
        self.assertEqual((rows[-1]["name"], rows[-1]["rank"], rows[-1]["joint"]), ("Lazarou Barbers", None, False))

    def test_no_businesses_extracted(self):
        rows = extraction.ranking("Lazarou Barbers", 2, [])
        self.assertEqual([(r["name"], r["rank"]) for r in rows], [("Lazarou Barbers", 1)])


if __name__ == "__main__":
    unittest.main()
