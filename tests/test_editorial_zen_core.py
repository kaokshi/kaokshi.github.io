from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from src.editorial_zen_core import Core, CoreBlocked


class CoreTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "private.sqlite"
        self.now = datetime(2026, 11, 24, 20, 59, tzinfo=timezone.utc)
        self.core = Core(self.path, clock=lambda: self.now)
        self.addCleanup(self.core.db.close)
        self.intent = {"id": "test", "plan": {"items": [{"channel": "legends", "count": 2}],
                                           "day": None, "instructions": "private"}}
        self.core.submit(self.intent)

    def started(self):
        article = "test/legends/1"
        for stage in ("plan", "draft", "edit", "visual_plan"):
            self.core.stage_result(article, stage, {"status": "passed"})
        return article

    def test_rejects_new_work_on_extra_reserve_and_accounts_correction(self):
        article = self.started()
        self.core.reserve_call("image", article, "images", "missing_image", "300", slot=4)
        self.core.settle("image", "300")
        with self.assertRaisesRegex(CoreBlocked, "reserve_only"):
            self.core.reserve_call("new", "test/legends/2", "plan", "new_article", "1")
        self.core.reserve_call("correction", article, "images", "image_correction", "2.5", slot=4)
        self.core.settle("correction", "1.92228192")
        budget = self.core.snapshot()["budget"]
        self.assertEqual(budget["reserveRemainingRub"], "48.07771808")
        self.assertEqual(budget["reserveUses"][0]["reason"], "image_correction")

    def test_pending_reservation_is_shared_and_blocks_at_midnight(self):
        article = self.started()
        self.core.reserve_call("x", article, "images", "missing_image", "3", slot=4)
        other = Core(self.path, clock=lambda: self.now)
        self.addCleanup(other.db.close)
        self.now = datetime(2026, 11, 24, 21, tzinfo=timezone.utc)
        self.assertEqual(other.snapshot()["budget"]["day"], "2026-11-25")
        with self.assertRaisesRegex(CoreBlocked, "unsettled"):
            other.reserve_call("y", article, "images", "missing_image", "3", slot=6)
        with self.assertRaisesRegex(CoreBlocked, "exceeds_reservation"):
            other.settle("x", "3.01")
        self.assertTrue(self.core.snapshot()["budget"]["unsettledRequest"])

    def test_retry_and_already_approved_slot(self):
        article = self.started()
        for i in range(3):
            self.core.reserve_call(str(i), article, "images", "image_correction" if i else "missing_image",
                                   "2", slot=4)
            self.core.settle(str(i), "1.92228192")
        with self.assertRaisesRegex(CoreBlocked, "image_retry_limit"):
            self.core.reserve_call("fourth", article, "images", "image_correction", "2", slot=4)
        self.core.stage_result(article, "images", {"status": "blocked", "checks": {"slot_5": "passed"}})
        with self.assertRaisesRegex(CoreBlocked, "already_passed"):
            self.core.reserve_call("approved", article, "images", "missing_image", "2", slot=5)

    def test_caps_and_safe_intake(self):
        article = self.started()
        self.core.reserve_call("big", article, "images", "missing_image", "350", slot=4)
        self.core.settle("big", "350")
        with self.assertRaisesRegex(CoreBlocked, "daily_budget_exhausted"):
            self.core.reserve_call("overflow", article, "images", "missing_image", "0.01", slot=6)
        self.core.submit(self.intent)
        with self.assertRaisesRegex(CoreBlocked, "job_id_conflict"):
            self.core.submit({**self.intent, "releaseAt": "2026-11-25T14:00:00+03:00"})
        snapshot = self.core.snapshot()
        self.assertNotIn("instructions", snapshot["jobs"][0])
        self.assertFalse(snapshot["publicationAuthorized"])
        self.assertFalse(snapshot["rssEnabled"])


if __name__ == "__main__":
    unittest.main()
