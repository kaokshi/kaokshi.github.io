"""The configured private store must never silently authorize production."""
from copy import deepcopy
import unittest

from src.editorial_brain import settings
from src.editorial_providers import Blocked
from src.editorial_schedule import plan


class PrivateStorageBindingTests(unittest.TestCase):
    def test_existing_repo_configured_without_activating_production(self):
        config = settings()
        schedule = config["schedule"]
        self.assertEqual(schedule["private_repository"], "kaokshi/zen-editorial-review")
        self.assertIs(schedule["enabled"], False)
        self.assertIs(schedule["owner_approved"], False)
        self.assertIsNone(schedule["successful_validation_run"])

        result = plan(config, command="daily", branch="refs/heads/main")
        self.assertEqual(result["command"], "skip")
        self.assertEqual(result["schedule"], "activation_pending")

    def test_no_owner_approval_means_no_automatic_launch(self):
        config = deepcopy(settings())
        config["schedule"]["enabled"] = True
        with self.assertRaisesRegex(Blocked, "activation_evidence_pending"):
            plan(config, command="daily", branch="refs/heads/main")


if __name__ == "__main__":
    unittest.main()
