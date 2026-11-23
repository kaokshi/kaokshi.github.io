"""The configured private store must never silently authorize production."""
from copy import deepcopy
from unittest.mock import patch
from src.editorial_delivery import owner_key
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

    def test_public_x25519_key_is_parseable_and_matches_owner_validation(self):
        config = settings()
        public_key = config["schedule"]["owner_public_key"]
        self.assertRegex(public_key, r"^[0-9a-f]{64}$")
        with patch.dict("os.environ", {"EDITORIAL_REVIEW_PUBLIC_KEY": public_key}):
            self.assertEqual(owner_key(), public_key)

    def test_no_owner_approval_means_no_automatic_launch(self):
        config = deepcopy(settings())
        config["schedule"]["enabled"] = True
        with self.assertRaisesRegex(Blocked, "activation_evidence_pending"):
            plan(config, command="daily", branch="refs/heads/main")


if __name__ == "__main__":
    unittest.main()
