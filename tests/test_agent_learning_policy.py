"""Safety tests for self-learning policy, no network/model training needed."""
import json
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import editorial_learning as learner
from editorial_agent_v1 import article_digest

class AgentLearningPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.learning = json.loads((ROOT / "config" / "agent_learning.json").read_text(encoding="utf-8"))
        cls.policy = json.loads((ROOT / "config" / "ai_revision_policy.json").read_text(encoding="utf-8"))
        cls.site = json.loads((ROOT / "config" / "site.json").read_text(encoding="utf-8"))

    def test_automatic_weight_training_and_model_switching_not_approved(self):
        self.assertTrue(self.learning["enabled"])
        self.assertEqual(self.learning["mode"], "collect_observations_and_propose_changes")
        self.assertFalse(self.learning["automated_weight_training_enabled"])
        self.assertFalse(self.learning["automated_prompt_changes_enabled"])
        self.assertTrue(self.learning["proposal_workflow"]["require_human_approval_before_model_deployment"])
        self.assertTrue(self.learning["proposal_workflow"]["require_independent_holdout"])

    def test_immutable_owner_rules(self):
        self.assertEqual(self.policy["model_a"]["owner_editorial_review_index_threshold"], 85)
        self.assertTrue(self.policy["model_a"]["index_is_authorship_percent"] is False)
        self.assertIn("nonblocking", self.policy["model_a"]["status"])
        self.assertEqual(self.learning["proposal_workflow"]["max_agent_edits_per_article"], 2)
        self.assertEqual(self.learning["proposal_workflow"]["max_chatgpt_edits_per_article"], 1)
        self.assertTrue(self.learning["proposal_workflow"]["never_change_model_a_threshold_80_without_owner_approval"])

    def test_no_automatic_publication_or_false_runtime_claim(self):
        self.assertFalse(self.site["publication_enabled"])
        self.assertFalse(self.policy["future_rss_delivery"]["project_launched"])
        self.assertFalse(self.learning["publication"]["cannot_change_publication_enabled"] is False)
        self.assertTrue(self.learning["publication"]["cannot_activate_rss"])
        self.assertTrue(self.learning["publication"]["cannot_unpause_cinema"])
        self.assertEqual(self.learning["implementation_status"],
                         "owner_reviewed_local_observations_and_proposals_implemented")

    def test_detector_scores_not_gold_labels(self):
        self.assertTrue(self.learning["memory_policy"]["never_assume_detector_score_is_ground_truth"])
        self.assertTrue(self.learning["memory_policy"]["prevent_train_holdout_leakage"])
        self.assertTrue(self.learning["memory_policy"]["no_secrets_or_personal_data"])

class AgentLearningTests(unittest.TestCase):
    def observations(self, slug, channel="science", outcome="unresolved"):
        draft = {"slug": slug, "channel": channel, "body_html": "<p>Документ редакции.</p>"}
        digest = article_digest(draft)
        case = {"schema": "zen-editorial-revision-case-v1", "versions": [
            {"article": draft, "text_sha256": digest, "stage": "original",
             "report": {"text_sha256": digest, "automated_checks_completed": False,
                        "ai": {"any_score": 99}}}]}
        feedback = {"reviewer": "owner", "slug": slug, "channel": channel,
                    "source_text_sha256": digest, "reviewed_text_sha256": digest,
                    "findings": {"sources": outcome}}
        return case, feedback

    def test_reviewer_and_version_are_required(self):
        case, feedback = self.observations("science-test")
        for invalid in ({"reviewer": "agent"}, {"reviewed_text_sha256": "stale"},
                        {"findings": {"ai_authorship": "resolved"}}):
            with self.assertRaises(ValueError):
                learner.reviewed_observations(case, {**feedback, **invalid})

    def test_recurring_issues_create_advisory_proposals_per_genre(self):
        rows = []
        for slug, channel in (("science-first", "science"), ("science-second", "science"),
                              ("legends-first", "legends")):
            rows.extend(learner.reviewed_observations(*self.observations(slug, channel)))
        self.assertEqual(learner.report(rows)["genres"]["science"]["proposals"], [])
        result = learner.report(rows + rows[:1], on_request=True)
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(result["genres"]["science"]["proposals"][0]["affected_articles"], 2)
        self.assertEqual(result["genres"]["legends"]["proposals"], [])
        self.assertFalse(result["observations"][0]["checks_completed"])
        self.assertNotIn("any_score", str(result))

    def test_batch_uses_twenty_distinct_articles(self):
        rows = []
        for i in range(20):
            rows.extend(learner.reviewed_observations(*self.observations(f"science-{i}")))
        self.assertEqual(learner.report(rows[:-1] + rows[:1])["genres"]["science"]["proposals"], [])
        self.assertEqual(learner.report(rows)["genres"]["science"]["proposals"][0]["affected_articles"], 20)


if __name__ == "__main__":
    unittest.main()
