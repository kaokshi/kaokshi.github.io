"""Versioned length policy: synthetic fixtures only, no article content or provider calls."""
import json
import unittest

from src import editorial_length as length
from src import editorial_review as review
from src.editorial_memory import digest
from src.editorial_zen_core import Core, CoreBlocked

INTENT = {"id": "fixture", "mode": "autonomous",
          "plan": {"items": [{"channel": "science", "count": 1}], "day": None, "instructions": ""}}


class LengthPolicyTest(unittest.TestCase):
    def test_legacy_policy_is_the_default_and_unchanged(self):
        self.assertEqual(length.active_policy(), "v1")
        self.assertEqual((length.MIN_CHARS, length.STANDARD_MAX, length.HARD_MAX), (3000, 6000, 7000))
        self.assertEqual(length.check_length("a" * 3500), (3500, None))
        self.assertEqual(length.check_length("a" * 6900, "reason")[1], None)

    def test_legacy_prompt_rules_stay_byte_identical(self):
        # These strings are part of saved, already-paid request fingerprints.
        self.assertEqual(review.rules(), review.RULES)
        self.assertEqual(review.strict_rules(), review.STRICT_RULES)
        self.assertEqual(digest(review.STRICT_RULES), digest(
            review.LENGTH_RULE + review.STRUCTURE_RULES + "Возвращай только текст статьи."))
        self.assertIn("3000–6000", review.STRICT_RULES)

    def test_owner_policy_v2_bounds(self):
        with length.length_policy("v2"):
            self.assertEqual(length.check_length("a" * 3999), (3999, "short_text"))
            self.assertEqual(length.check_length("a" * 4000), (4000, None))
            self.assertEqual(length.check_length("a" * 6000), (6000, None))
            self.assertEqual(length.check_length("a" * 6200)[1], "length_exception_reason_missing")
            self.assertEqual(length.check_length("a" * 6500, "needed for the mechanism")[1], None)
            self.assertEqual(length.check_length("a" * 6501, "needed")[1], "long_text")
            self.assertEqual(length.check_length("a" * 4000 + " \n\t" * 50), (4000, None))
        self.assertEqual(length.active_policy(), "v1", "the policy is restored after the run")

    def test_v2_prompts_ask_for_varied_volume_and_allow_justified_6500(self):
        with length.length_policy("v2"):
            for text in (review.rules(), review.strict_rules()):
                self.assertIn("4000–6000", text)
                self.assertIn("6001–6500", text)
                self.assertIn("не сокращай текст до нижней границы", text)
                self.assertIn("length_exception_reason", text)
                self.assertNotIn("3000", text)

    def test_unknown_policy_is_refused(self):
        with self.assertRaises(ValueError):
            with length.length_policy("v9"):
                pass


class OrderPinningTest(unittest.TestCase):
    def setUp(self):
        self.c = Core(":memory:")

    def tearDown(self):
        self.c.db.close()

    def test_new_order_pins_the_current_policy(self):
        self.c.submit(INTENT)
        plan, _ = self.c.article_work("fixture/science/1")
        self.assertEqual(plan["lengthPolicy"], "v2")
        self.c.submit(INTENT)  # idempotent redelivery

    def test_redelivered_legacy_order_keeps_its_original_intent(self):
        legacy = json.dumps({"day": None, "instructions": "", "items": [{"channel": "science", "count": 1}],
                             "mode": "autonomous", "releaseAt": None}, sort_keys=True, ensure_ascii=False)
        with self.c.db:
            self.c.db.execute("INSERT INTO jobs VALUES (?, ?)", ("fixture", legacy))
        stored_before = self.c.db.execute("SELECT intent FROM jobs").fetchone()[0]
        try:
            self.c.submit(INTENT)
        except CoreBlocked as exc:
            self.fail(f"legacy redelivery must not conflict: {exc}")
        self.assertEqual(self.c.db.execute("SELECT intent FROM jobs").fetchone()[0], stored_before)
        plan, _ = self.c.article_work("fixture/science/1")
        self.assertNotIn("lengthPolicy", plan)

    def test_a_different_order_with_the_same_id_still_conflicts(self):
        self.c.submit(INTENT)
        other = {**INTENT, "plan": {**INTENT["plan"], "instructions": "different"}}
        with self.assertRaises(CoreBlocked):
            self.c.submit(other)


if __name__ == "__main__":
    unittest.main()
