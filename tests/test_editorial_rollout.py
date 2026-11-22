"""Safety and idempotency of the production cutover and quota status."""
from copy import deepcopy
import unittest

from src.editorial_brain import settings
from src.editorial_providers import Blocked
from src.editorial_quota import quota_status
from src.editorial_schedule import plan


class RolloutTests(unittest.TestCase):
    def test_schedule_disabled_for_both_crons_and_manual_production(self):
        conf = settings()
        for cron in ("0 5 * * *", "0 8 * * *"):
            self.assertEqual(plan(conf, command="scheduled", cron=cron, branch="refs/heads/main")
                             ["command"], "skip")
        self.assertEqual(plan(conf, command="daily", branch="refs/heads/main")["command"], "skip")
        result = plan(conf, command="daily", branch="refs/heads/codex/editorial-production",
                      isolated=True, test_free=True)
        self.assertTrue(result["groq_free"])
        self.assertEqual(result["repository"], "")

    def test_activation_requires_all_evidence_private_store_and_main(self):
        conf = deepcopy(settings())
        conf["schedule"]["enabled"] = True
        with self.assertRaisesRegex(Blocked, "activation_evidence_pending"):
            plan(conf, command="scheduled", cron="0 5 * * *", branch="refs/heads/main")
        conf["schedule"].update({"successful_validation_run": 123, "owner_access_verified": True,
                                 "legacy_automation_retired": True, "owner_approved": True})
        with self.assertRaisesRegex(Blocked, "storage_configuration_pending"):
            plan(conf, command="daily", branch="refs/heads/main")
        conf["schedule"].update(private_repository="kaokshi/private-fixture", owner_public_key="12" * 32)
        with self.assertRaisesRegex(Blocked, "production_requires_main"):
            plan(conf, command="daily", branch="refs/heads/work")
        with self.assertRaisesRegex(Blocked, "unknown_editorial_schedule"):
            plan(conf, command="scheduled", cron="5 5 * * *", branch="refs/heads/main")
        for cron, expected in (("0 5 * * *", "daily"), ("0 8 * * *", "review")):
            self.assertEqual(plan(conf, command="scheduled", cron=cron, branch="refs/heads/main")
                             ["command"], expected)

    def test_known_daily_block_beats_ready_and_new_day_needs_evidence(self):
        conf = settings()
        self.assertEqual(quota_status(conf, "2026-11-22", {"ready"}), "blocked")
        self.assertEqual(quota_status(conf, "2026-11-23", ()), "unverified")
        self.assertEqual(quota_status(conf, "2026-11-23", {"attempted"}), "probe_attempted")
        self.assertEqual(quota_status(conf, "2026-11-23", {"attempted", "ready"}), "ready")
        self.assertEqual(quota_status(conf, "2026-11-23", {"ready", "blocked"}), "blocked")
