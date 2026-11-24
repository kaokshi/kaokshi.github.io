"""Proof that learning proposals contain only aggregate evidence."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.editorial_learning import propose_pull_requests


class RolloutTests(unittest.TestCase):
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
