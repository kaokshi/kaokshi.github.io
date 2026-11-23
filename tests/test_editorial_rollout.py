"""Safety and idempotency of the production cutover and quota status."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.editorial_brain import settings
from src.editorial_providers import Blocked
from src.editorial_learning import propose_pull_requests
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


    def test_learning_proposal_sends_only_aggregate_candidate_evidence(self):
        calls = []
        responses = [([], 0), ({"status": 404}, 1), ({"object": {"sha": "base-sha"}}, 0),
                     ({"tree": {"sha": "base-tree"}}, 0), ({"sha": "proposal-tree"}, 0),
                     ({"sha": "proposal-commit"}, 0), ({}, 0),
                     ({"html_url": "https://github.com/example/repo/pull/42"}, 0)]

        def fake_run(args, **kwargs):
            calls.append((args[4], json.loads(kwargs["input"]) if kwargs["input"] else None))
            value, code = responses.pop(0)
            return SimpleNamespace(stdout=json.dumps(value), returncode=code)

        proposal = {"status": "candidate_improved", "channel": "legends", "issue": "missing_cta",
                    "instruction": "Keep the CTA.", "held_out_cases": 5, "baseline_resolved": 1,
                    "candidate_resolved": 4, "regressions": 0, "rollback": "Keep the original prompt.",
                    "private_feedback": "PRIVATE-FIXTURE-DO-NOT-UPLOAD"}
        with patch("subprocess.run", fake_run):
            self.assertEqual(len(propose_pull_requests({"proposals": [
                {**proposal, "status": "rejected_regression"}, proposal]}, "fixture-only")), 1)
        self.assertFalse(responses)
        self.assertNotIn("PRIVATE-FIXTURE", str(calls))
        self.assertTrue(calls[-1][1]["draft"])
        proposed_tree = next(payload for route, payload in calls if route.endswith("/git/trees"))
        self.assertTrue(proposed_tree["tree"][0]["path"].startswith("config/editorial_prompt_candidates/"))
