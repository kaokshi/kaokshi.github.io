"""Frozen rollout: no duplicate scheduled jobs, publication or loss of owner rules."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MainlineRolloutGuardTests(unittest.TestCase):
    def test_new_workflow_cannot_create_parallel_schedules(self):
        workflows = ROOT / ".github/workflows"
        self.assertFalse((workflows / "editorial-production.yml").exists())
        intake = (workflows / "zen-core-intake-runner.yml").read_text(encoding="utf-8")
        self.assertIn("7,22,37,52 * * * *", intake)
        self.assertIn("ZEN_CORE_INTAKE_SCHEDULE_ENABLED", intake)
        self.assertIn("ZEN_CORE_PRODUCTION_ENABLED", intake)

    def test_release_and_agent_remain_frozen(self):
        site = json.loads((ROOT / "config/site.json").read_text(encoding="utf-8"))
        cfg = json.loads((ROOT / "config/zen_core.json").read_text(encoding="utf-8"))
        self.assertIs(site["publication_enabled"], False)
        for flag in ("execution_enabled", "publication_enabled", "rss_enabled"):
            self.assertIs(cfg[flag], False)

    def test_owner_layout_survives_integration(self):
        agent = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        editorial = (ROOT / "docs/EDITORIAL_AUTOMATION.md").read_text(encoding="utf-8")
        self.assertIn("Mandatory Dzen layout", agent)
        self.assertIn("Новый обязательный стандарт вёрстки от 23.11.2026", editorial)
        self.assertIn("Up to five genuine editorial-agent revisions", agent)


if __name__ == "__main__":
    unittest.main()
