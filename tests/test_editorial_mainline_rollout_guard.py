"""Frozen rollout: no duplicate scheduled jobs, publication or loss of owner rules."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MainlineRolloutGuardTests(unittest.TestCase):
    def test_new_workflow_cannot_create_parallel_schedules(self):
        workflow = (ROOT / ".github/workflows/editorial-production.yml").read_text(encoding="utf-8")
        self.assertNotIn("\n  schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)

    def test_release_and_agent_remain_frozen(self):
        site = json.loads((ROOT / "config/site.json").read_text(encoding="utf-8"))
        cfg = json.loads((ROOT / "config/editorial_production.json").read_text(encoding="utf-8"))
        self.assertIs(site["publication_enabled"], False)
        self.assertIs(cfg["schedule"]["enabled"], False)
        self.assertIs(cfg["schedule"]["owner_approved"], False)
        self.assertIsNone(cfg["schedule"]["successful_validation_run"])

    def test_owner_layout_survives_integration(self):
        agent = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        editorial = (ROOT / "docs/EDITORIAL_AUTOMATION.md").read_text(encoding="utf-8")
        self.assertIn("Mandatory Dzen layout", agent)
        self.assertIn("Новый обязательный стандарт вёрстки от 23.11.2026", editorial)
        self.assertIn("Up to five genuine editorial-agent revisions", agent)


if __name__ == "__main__":
    unittest.main()
