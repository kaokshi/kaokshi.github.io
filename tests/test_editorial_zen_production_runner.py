"""Offline regression tests for the fifteen-minute production cycle."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

from src.editorial_memory import digest
from src.editorial_zen_cloud import CloudCore
from src.editorial_zen_core import Core, CoreBlocked
from tests.test_editorial_zen_executor import Store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "telegram-control"))
import production_runner as runner


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.cloud = CloudCore(self.store)
        self.job = "agent:test"
        self.article = self.job + "/legends/1"
        self.cloud.access(lambda c: c.submit({"id": self.job, "plan": {
            "items": [{"channel": "legends", "count": 1}], "day": None,
            "instructions": "Synthetic fixture"}}), change=True)

    def test_saved_response_settles_without_transport(self):
        operation = self.article + "/plan"
        self.cloud.access(lambda c: c.reserve_call(operation, self.article, "plan", "new_article", 2), change=True)
        closed = []
        def account(action, *args):
            if action == "status":
                return {"production": {"id": self.job, "operation": operation}}
            closed.append(args)
        with self.assertRaisesRegex(CoreBlocked, "financial_reconciliation_required"):
            runner.recover_recorded_response(self.cloud, account)
        self.assertEqual(closed, [])
        self.store.immutable("articles/" + digest(self.article) + "/requests/plan.json",
                             json.dumps({"answer": {"usage": {"cost": "1.000000000000001"}}}).encode())
        runner.recover_recorded_response(self.cloud, account)
        self.assertEqual(closed, [(self.job, operation, "settled")])
        self.assertEqual(self.cloud.access(lambda c: c.snapshot())["budget"]["meteredRub"], "1.000000000000001")

    def test_bounded_dispatch_reports_checkpointed_progress(self):
        dispatched, receipts = [], []
        def advance(cloud, article, token, **kwargs):
            self.assertEqual(article, self.article)
            dispatched.append(article)
            cloud.access(lambda c: c.stage_result(article, "plan", {"status": "passed"}), change=True)
            return {"status": "attempt_finished"}
        from unittest.mock import patch
        from intake_client import build_receipt
        def receipt(core, job_id, *, production):
            self.assertTrue(production)
            core.policy["execution_enabled"] = True
            return build_receipt(core, job_id, production=True)
        with patch.object(runner, "build_receipt", receipt):
            result = runner.dispatch_cycle(self.cloud, "fixture", "fixture", "fixture",
                                           guard=None, account=lambda *_: {"production": None}, advance=advance,
                                           deliver=lambda _, __, payload: receipts.append(payload))
        self.assertEqual(result, {"attempted": 1, "receipts": 1})
        self.assertEqual(receipts[0]["articles"][0]["stages"][0]["status"], "passed")
        self.assertFalse(receipts[0]["publicationAuthorized"])
        self.assertFalse(receipts[0]["rssEnabled"])

    def test_midnight_preparation_is_only_for_scheduled_jobs(self):
        core = Core(":memory:", clock=lambda: datetime(2026, 11, 24, 20, tzinfo=timezone.utc))
        self.addCleanup(core.db.close)
        plan = {"items": [{"channel": "legends", "count": 1}], "day": "2026-11-25", "instructions": ""}
        for job in ("scheduled:legends:fixture", "agent:later"):
            core.submit({"id": job, "plan": plan, "releaseAt": "2026-11-25T08:00:00+03:00"})
        self.assertEqual(core.article_work("scheduled:legends:fixture/legends/1")[1], "plan")
        with self.assertRaisesRegex(CoreBlocked, "scheduled_job_not_due"):
            core.article_work("agent:later/legends/1")


if __name__ == "__main__":
    unittest.main()
