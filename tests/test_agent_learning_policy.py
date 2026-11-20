"""Safety tests for self-learning policy, no network/model training needed."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
        self.assertEqual(self.policy["model_a"]["owner_editorial_review_index_threshold"], 80)
        self.assertEqual(self.learning["proposal_workflow"]["max_agent_edits_per_article"], 2)
        self.assertEqual(self.learning["proposal_workflow"]["max_chatgpt_edits_per_article"], 1)
        self.assertTrue(self.learning["proposal_workflow"]["never_change_model_a_threshold_80_without_owner_approval"])

    def test_no_automatic_publication_or_false_runtime_claim(self):
        self.assertFalse(self.site["publication_enabled"])
        self.assertFalse(self.policy["future_rss_delivery"]["project_launched"])
        self.assertFalse(self.learning["publication"]["cannot_change_publication_enabled"] is False)
        self.assertTrue(self.learning["publication"]["cannot_activate_rss"])
        self.assertTrue(self.learning["publication"]["cannot_unpause_cinema"])
        self.assertIn("not_implemented", self.learning["implementation_status"])

    def test_detector_scores_not_gold_labels(self):
        self.assertTrue(self.learning["memory_policy"]["never_assume_detector_score_is_ground_truth"])
        self.assertTrue(self.learning["memory_policy"]["prevent_train_holdout_leakage"])
        self.assertTrue(self.learning["memory_policy"]["no_secrets_or_personal_data"])

if __name__ == "__main__":
    unittest.main()
