"""Offline regression tests for the fifteen-minute production cycle."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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

    def test_settings_only_synchronizes_without_touching_orders(self):
        env = {"ZEN_CORE_INTAKE_ENDPOINT": runner.ENDPOINT, "ZEN_CORE_INTAKE_SECRET": "a" * 64,
               "EDITORIAL_PRIVATE_TOKEN": "fixture", "ROUTERAI_API_KEY": "fixture",
               "ZEN_CORE_SETTINGS_ONLY": "true"}
        with (patch.dict(os.environ, env, clear=True),
              patch.object(runner, "GitHubStore", return_value=self.store),
              patch.object(runner, "publish_catalogue") as catalogue,
              patch.object(runner, "synchronize") as synchronize,
              patch.object(runner, "accept_cycle") as intake,
              patch.object(runner, "dispatch_cycle") as dispatch,
              patch.object(runner, "deliver_documents") as delivery):
            runner.main()
            catalogue.assert_called_once()
            synchronize.assert_called_once()
            intake.assert_not_called()
            dispatch.assert_not_called()
            delivery.assert_not_called()
            os.environ["ZEN_CORE_PILOT_JOB"] = self.job
            with self.assertRaisesRegex(CoreBlocked, "settings_only_requires_execution_disabled"):
                runner.main()
            synchronize.assert_called_once()

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

    def test_pilot_passes_stop_on_unsettled_spend_and_attempt_delivery_before_exit(self):
        from unittest.mock import Mock, patch

        self.cloud.access(lambda c: c.apply_mode(self.job, "autonomous", 1), change=True)
        seen = []
        def run(*args, **kwargs):
            self.assertEqual(kwargs["job_scope"], self.job)
            self.cloud.access(lambda c: c.reserve_call(
                self.article + "/plan", self.article, "plan", "new_article", 2), change=True)
            return {"attempted": 1, "receipts": 1}

        cycle = Mock(side_effect=run)
        with patch.object(runner, "dispatch_cycle", cycle):
            result = runner.dispatch_passes(
                self.cloud, "fixture", "fixture", "fixture", guard=None, account=None,
                job_scope=self.job, count=3, deliver=lambda snapshot: seen.append(snapshot["budget"]))
        cycle.assert_called_once()
        self.assertEqual(result, {"attempted": 1, "receipts": 1})
        self.assertTrue(seen[0]["unsettledRequest"])

    def test_midnight_preparation_is_only_for_scheduled_jobs(self):
        core = Core(":memory:", clock=lambda: datetime(2026, 11, 24, 20, tzinfo=timezone.utc))
        self.addCleanup(core.db.close)
        plan = {"items": [{"channel": "legends", "count": 1}], "day": "2026-11-25", "instructions": ""}
        for job in ("scheduled:legends:fixture", "agent:later"):
            core.submit({"id": job, "plan": plan, "releaseAt": "2026-11-25T08:00:00+03:00"})
        self.assertEqual(core.article_work("scheduled:legends:fixture/legends/1")[1], "plan")
        with self.assertRaisesRegex(CoreBlocked, "scheduled_job_not_due"):
            core.article_work("agent:later/legends/1")


class DocumentDeliveryTest(unittest.TestCase):
    def setUp(self):
        from src.editorial_zen_core import STAGES

        self.store = Store()
        self.job = "agent:10000000-0000-0000-0000-000000000000"
        self.article = {"id": self.job + "/cinema/1",
                        "stages": [{"id": stage, "status": "passed"} for stage in STAGES]}
        self.prefix = "articles/" + digest(self.article["id"]) + "/delivery/"
        self.token = "123:fixture"
        self.owner = "456"
        self.store.immutable(self.prefix + "article.docx", b"synthetic delivery payload")
        self.store.immutable(self.prefix + "manifest.json", json.dumps({
            "article": self.article["id"], "sha256": digest(b"synthetic delivery payload"),
            "images": 7, "publication_authorized": False}).encode())

    def test_send_is_scoped_and_already_delivered_documents_are_not_resent(self):
        calls = []
        def send(*args):
            calls.append(args)
            return 100
        blocked = {**self.article, "id": self.job + "/cinema/2", "stages": []}
        articles = [self.article, blocked, {**self.article, "id": "agent:other/cinema/1"}]
        self.assertEqual(runner.deliver_documents(self.store, articles, self.job, self.token,
                                                 self.owner, send=send), {"sent": 1, "unconfirmed": 0})
        self.assertEqual(calls, [(self.token, self.owner, "cinema-1.docx", b"synthetic delivery payload")])
        runner.deliver_documents(self.store, articles, self.job, self.token, self.owner, send=send)
        self.assertEqual(len(calls), 1)

    def test_unknown_delivery_outcome_is_not_resent(self):
        from unittest.mock import Mock

        send = Mock(side_effect=TimeoutError)
        for _ in range(2):
            self.assertEqual(runner.deliver_documents(self.store, [self.article], self.job, self.token,
                                                     self.owner, send=send), {"sent": 0, "unconfirmed": 1})
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
