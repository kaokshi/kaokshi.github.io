import json
import unittest
from src.editorial_zen_cloud import CloudCore
from src.editorial_zen_core import CoreBlocked
from src.editorial_zen_finance_recovery import inspect, recover, recover_bounded, recover_owner_waived
from tests.test_editorial_zen_executor import Store

NOW = 1795536000000
OP = "fixture/science/1/plan"


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.cloud = CloudCore(Store())
        self.cloud.access(lambda c: c.submit({"id": "fixture", "plan": {"items": [
            {"channel": "science", "count": 1}], "day": None, "instructions": ""}}), change=True)
        self.hold = {"id": "fixture", "operation": OP, "startedAt": NOW - 10000}
        self.events = []

    def account(self, action, *args):
        if action == "status":
            return {"schema": "zen-core/account-status-v1", "observedAt": NOW,
                    "executionEnabled": False, "production": self.hold,
                    "chat": None}
        self.events.append((action, *args))
        return {"closed": True}

    def test_unknown_or_missing_reservation_never_releases(self):
        self.assertEqual(inspect(self.cloud, self.account, NOW)["productionRecovery"],
                         "missing_reservation_requires_evidence")
        self.cloud.access(lambda c: c.reserve_call(OP, "fixture/science/1", "plan", "new_article", "2"), change=True)
        self.assertEqual(inspect(self.cloud, self.account, NOW)["productionRecovery"],
                         "unsettled_requires_evidence")
        with self.assertRaises(CoreBlocked):
            recover(self.cloud, self.account, confirm=True, expected_operation=OP, now=NOW)
        self.assertEqual(self.events, [])

    def test_settled_hold_requires_confirmation_and_exact_operation(self):
        self.cloud.access(lambda c: c.reserve_call(OP, "fixture/science/1", "plan", "new_article", "2"), change=True)
        self.cloud.access(lambda c: c.settle(OP, "1.25"), change=True)
        before = dict(self.cloud.store.data)
        for confirm, operation in ((False, OP), (True, "different")):
            with self.assertRaises(CoreBlocked):
                recover(self.cloud, self.account, confirm=confirm, expected_operation=operation, now=NOW)
        report = recover(self.cloud, self.account, confirm=True, expected_operation=OP, now=NOW)
        self.assertEqual(report["confirmedRub"], "1.25")
        self.assertEqual(self.events, [("close", "fixture", OP, "settled")])
        self.assertEqual(before, self.cloud.store.data)

    def test_bounded_evidence_retains_reservation_without_confirming_or_resending(self):
        article = "fixture/science/1"
        self.cloud.access(lambda c: c.reserve_call(OP, article, "plan", "new_article", "2"), change=True)
        evidence = {"schema": "zen-core/billing-upper-bound-v1", "provider": "RouterAI",
                    "requestId": OP, "providerEvidence": "synthetic provider statement",
                    "maxFinalRub": "1.80", "billingFinal": True}
        encode = lambda data: json.dumps(data).encode()
        for data in ({**evidence, "billingFinal": False}, {**evidence, "maxFinalRub": "2.01"}):
            with self.assertRaises(CoreBlocked):
                recover_bounded(self.cloud, self.account, encode(data), confirm=True, now=NOW)
        self.assertEqual(self.events, [])
        report = recover_bounded(self.cloud, self.account, encode(evidence), confirm=True, now=NOW)
        self.assertIsNone(report["confirmedRub"])
        self.assertEqual(report["reservedRub"], "2")
        self.assertEqual(self.events, [("close", "fixture", OP, "bounded_uncertain")])
        status = inspect(self.cloud, self.account, NOW)
        self.assertEqual(status["budget"]["meteredRub"], "0")
        self.assertEqual(status["budget"]["boundedUncertainRub"], "2")
        self.assertFalse(status["budget"]["unsettledRequest"])
        self.assertEqual(status["budget"]["mainRemainingRub"], "298")
        with self.assertRaisesRegex(CoreBlocked, "request_already_seen"):
            self.cloud.access(lambda c: c.reserve_call(OP, article, "plan", "new_article", "2"), change=True)
        self.cloud.access(lambda c: c.reconcile(OP, "1.25", "a" * 64), change=True)
        status = inspect(self.cloud, self.account, NOW)
        self.assertEqual(status["budget"]["meteredRub"], "1.25")
        self.assertEqual(status["budget"]["boundedUncertainRub"], "0")
        self.assertEqual(status["budget"]["mainRemainingRub"], "298.75")

    def test_owner_waiver_retains_buffer_and_never_claims_billing_finality(self):
        article = "fixture/science/1"
        self.cloud.access(lambda c: c.reserve_call(OP, article, "plan", "new_article", "2"), change=True)
        for confirm, reserve in ((False, "2"), (True, "1")):
            with self.assertRaises(CoreBlocked):
                recover_owner_waived(self.cloud, self.account, confirm=confirm,
                                     expected_reserve=reserve, now=NOW)
        self.assertEqual(self.events, [])
        result = recover_owner_waived(self.cloud, self.account, confirm=True,
                                      expected_reserve="2", now=NOW)
        self.assertEqual(result["status"], "owner_waived")
        self.assertIsNone(result["confirmedRub"])
        self.assertFalse(result["billingFinal"])
        self.assertEqual(self.events, [("close", "fixture", OP, "bounded_uncertain")])
        budget = inspect(self.cloud, self.account, NOW)["budget"]
        self.assertFalse(budget["unsettledRequest"])
        self.assertEqual(budget["boundedUncertainRub"], "2")
        self.assertEqual(budget["meteredRub"], "0")
        with self.assertRaisesRegex(CoreBlocked, "request_already_seen"):
            self.cloud.access(lambda c: c.reserve_call(OP, article, "plan", "new_article", "2"), change=True)

    def test_missing_cloud_or_stale_account_blocks(self):
        for cloud, now in ((CloudCore(Store()), NOW), (self.cloud, NOW + 60000)):
            with self.assertRaises(CoreBlocked):
                inspect(cloud, self.account, now)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
