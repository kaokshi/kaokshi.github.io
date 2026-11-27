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


class DefaultBranchTriggerTests(unittest.TestCase):
    """The offline regression and the Studio preflight must run on the default branch.

    Both workflows used to fire only on branches that are not the default
    branch, so a merged change never produced a check on main.
    """

    def test_offline_regression_runs_on_the_default_branch(self):
        offline = (ROOT / ".github/workflows/zen-core-offline-tests.yml").read_text(encoding="utf-8")
        push = offline.split("pull_request:")[0]
        self.assertIn("branches: [main,", push)

    def test_studio_preflight_runs_on_the_default_branch(self):
        studio = (ROOT / ".github/workflows/zen-studio-deploy.yml").read_text(encoding="utf-8")
        push = studio.split("workflow_dispatch:")[0]
        self.assertIn("branches: [main,", push)

    def test_studio_preflight_cannot_update_the_worker_on_a_push(self):
        """A push may verify the Worker, never replace it."""
        deploy = (ROOT / "telegram-control/deploy-studio.mjs").read_text(encoding="utf-8")
        self.assertIn('ZEN_STUDIO_DEPLOY_MODE === "deploy"', deploy)
        self.assertIn("if (!deploy) return;", deploy)
        studio = (ROOT / ".github/workflows/zen-studio-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("ZEN_STUDIO_DEPLOY_MODE: ${{ inputs.mode || 'preflight' }}", studio)

    def test_exclusive_browser_collection_is_not_displaced_by_a_push(self):
        """The Studio statistics workflow owns the browser account and stays manual."""
        sync = (ROOT / ".github/workflows/zen-studio-sync.yml").read_text(encoding="utf-8")
        push = sync.split("schedule:")[0]
        self.assertNotIn("branches: [main,", push)
        self.assertIn("zen-browser-account-exclusive", sync)


if __name__ == "__main__":
    unittest.main()
