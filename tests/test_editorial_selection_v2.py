"""Photo pre-selection and fact excerpts for policy v2. Synthetic fixtures only; no network."""
import unittest
from unittest.mock import patch

from src import editorial_routerai_two_articles as pipeline
from src.editorial_length import length_policy
from src.editorial_review import fact_evidence
from src.editorial_routerai import UnsafeToTest

ROWS = [{"asset_url": f"https://upload.wikimedia.org/fixture/{n}.jpg", "domains": ("upload.wikimedia.org",),
         "rights_status": "license_metadata_verified", "title": f"fixture {n}"} for n in range(5)]
# Synthetic relevance per candidate: [scene score, unrelated score].
SCORES = {0: [0.20, 0.30], 1: [0.31, 0.30], 2: [0.60, 0.10], 3: [0.45, 0.15], 4: [0.10, 0.40]}
SCENE = {"kind": "photo", "query": "lamp post courtyard", "subject": "a lamp post in a courtyard", "brief": "x"}


def fakes():
    return (patch.object(pipeline, "commons_photos", lambda query: ROWS),
            patch.object(pipeline, "request", lambda url, domains, timeout=10: url.encode()),
            patch.object(pipeline, "decode_candidate", lambda raw: (int(raw.decode()[-5]), None)),
            patch.object(pipeline, "compare_pixels", lambda model, image, texts: (SCORES[image], None)))


class PhotoSelectionTest(unittest.TestCase):
    def run_with_fakes(self, fn):
        a, b, c, d = fakes()
        with a, b, c, d:
            return fn()

    def test_pool_drops_unrelated_and_orders_best_first(self):
        pool = self.run_with_fakes(lambda: pipeline.ranked_photo_pool(SCENE, {}))
        self.assertEqual([meta["title"] for _, meta in pool], ["fixture 2", "fixture 3", "fixture 1"])
        self.assertTrue(all(meta["ai_generated"] is False for _, meta in pool))

    def test_v2_attempts_follow_the_ranking_and_stop_when_exhausted(self):
        def run():
            cache = {}
            with length_policy("v2"):
                picks = [pipeline.science_asset(SCENE, [], n, cache)[1]["title"] for n in range(3)]
                with self.assertRaises(UnsafeToTest):
                    pipeline.science_asset(SCENE, [], 3, cache)
            return picks
        self.assertEqual(self.run_with_fakes(run), ["fixture 2", "fixture 3", "fixture 1"])

    def test_legacy_orders_keep_search_order(self):
        title = self.run_with_fakes(lambda: pipeline.science_asset(SCENE, [], 0, {})[1]["title"])
        self.assertEqual(title, "fixture 0", "v1 keeps the historical candidate order")

    def test_unfindable_photo_slots_are_reported_before_paid_work(self):
        hopeless = {**SCENE, "query": "nothing", "subject": "nothing"}
        diagram = {"kind": "diagram", "template": "x", "subject": "d", "brief": "d"}
        def run():
            with patch.object(pipeline, "compare_pixels", lambda model, image, texts: ([0.1, 0.9], None)):
                with self.assertRaises(UnsafeToTest) as caught:
                    pipeline.require_findable_photos([SCENE, diagram, hopeless], {})
            return caught.exception
        error = self.run_with_fakes(run)
        self.assertEqual(str(error), "image_plan_photo_unavailable")
        self.assertEqual(error.slots, [0, 2])
        self.run_with_fakes(lambda: pipeline.require_findable_photos([SCENE, diagram], {}))


class FactExcerptsTest(unittest.TestCase):
    RECORDS = [{"status": "accessible", "url": "https://example.invalid/card",
                "text": "Synthetic card\nRelease year: 2031\nDirector: Fixture Person\nCast: Another Fixture"}]
    TEXT = "The synthetic film from 2031 is directed by Fixture Person."

    def test_several_verbatim_excerpts_support_one_claim(self):
        audit = {"claims": [{"claim": "The synthetic film from 2031 is directed by Fixture Person.", "source": "S1",
                             "quote": ["Release year: 2031", "Director: Fixture Person"], "verdict": "supported"}]}
        verified, issues = fact_evidence(self.TEXT, audit, self.RECORDS)
        self.assertEqual(issues, [])
        self.assertEqual(len(verified), 1)

    def test_paraphrased_or_too_many_excerpts_are_rejected(self):
        for quote in (["Released in 2031", "Director: Fixture Person"],
                      ["Release year: 2031", "Director: Fixture Person", "Cast: Another Fixture", "Synthetic card"],
                      ["Director: Fixture Person"]):
            audit = {"claims": [{"claim": "The synthetic film from 2031 is directed by Fixture Person.",
                                 "source": "S1", "quote": quote, "verdict": "supported"}]}
            _, issues = fact_evidence(self.TEXT, audit, self.RECORDS)
            self.assertTrue(issues, quote)

    def test_single_string_quote_keeps_the_original_rule(self):
        audit = {"claims": [{"claim": "The synthetic film from 2031 is directed by Fixture Person.", "source": "S1",
                             "quote": "Release year: 2031\nDirector: Fixture Person", "verdict": "supported"}]}
        self.assertEqual(fact_evidence(self.TEXT, audit, self.RECORDS)[1], [])


if __name__ == "__main__":
    unittest.main()
