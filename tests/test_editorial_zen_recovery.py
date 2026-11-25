import json
import unittest
from datetime import datetime, timezone
from src.editorial_zen_core import Core, CoreBlocked
from src.editorial_zen_recovery import (recovery_status, confirmed_retry, claim_retry, retry_window,
                                        release_blocked_stage, COOLDOWN_MS)

class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.c=Core(":memory:")
        self.article="fixture/science/1"
        self.c.submit({"id":"fixture","plan":{"items":[{"channel":"science","count":1}],"day":None,"instructions":""}})
    def tearDown(self):
        self.c.db.close()
    def test_temporary_blocker_never_erases_checkpoint(self):
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"daily_budget_exhausted"})
        before=self.c.db.serialize()
        self.assertEqual(confirmed_retry(self.c,self.article,"draft",0,"daily_budget_exhausted")["status"],"recheck_required")
        self.assertEqual(self.c.db.serialize(),before)
        with self.assertRaises(CoreBlocked):
            confirmed_retry(self.c,self.article,"draft",1,"daily_budget_exhausted")
    def test_ambiguous_charge_stops_recovery(self):
        self.c.reserve_call("fixture/science/1/plan",self.article,"plan","new_article",1)
        self.assertEqual(recovery_status(self.c,self.article)["reason"],"financial_reconciliation_required")
    def test_quality_and_unknown_errors_never_auto_retry(self):
        for reason in ("quality_gate_incomplete","unknown_failure"):
            self.c.stage_result(self.article,"plan",{"status":"blocked","reason":reason})
            self.assertEqual(recovery_status(self.c,self.article)["status"],"blocked")
    def test_image_slots_preserved(self):
        for stage in ("plan","draft","edit","visual_plan"):
            self.c.stage_result(self.article,stage,{"status":"passed"})
        with self.c.db:
            self.c.db.execute("INSERT INTO images VALUES (?,?,?)",(self.article,2,"{}"))
        self.c.stage_result(self.article,"images",{"status":"blocked","reason":"routerai_balance_insufficient"})
        state=recovery_status(self.c,self.article)
        self.assertEqual(state["acceptedImageSlots"],[2])
        self.assertEqual(state["missingImageSlots"],[0,1,3,4,5,6])

    def test_retry_budget_and_cooldown_survive_restart(self):
        self.c.stage_result(self.article,"plan",{"status":"blocked","reason":"control_unavailable"})
        row=self.c.check_stage(self.article,"plan")["plan"]
        import json
        blocked=json.loads(row)["blockedAt"]
        self.assertFalse(retry_window(self.c,self.article,"plan","control_unavailable",blocked+COOLDOWN_MS-1))
        now=blocked+COOLDOWN_MS
        claim_retry(self.c,self.article,"plan",0,"control_unavailable",now)
        self.assertFalse(retry_window(self.c,self.article,"plan","control_unavailable",now+1))
        with self.assertRaises(CoreBlocked):
            claim_retry(self.c,self.article,"plan",0,"control_unavailable",now+1)
        clone=Core(":memory:",checkpoint=self.c.db.serialize())
        try:
            claim_retry(clone,self.article,"plan",0,"control_unavailable",now+COOLDOWN_MS)
            self.assertFalse(retry_window(clone,self.article,"plan","control_unavailable",now+2*COOLDOWN_MS))
            self.assertFalse(retry_window(clone,self.article,"plan","daily_budget_exhausted",now+2*COOLDOWN_MS))
            self.assertEqual(recovery_status(clone,self.article)["reason"],"control_unavailable")
        finally:
            clone.db.close()


class ReleaseTest(unittest.TestCase):
    """An owner release clears one transient block without touching money or artifacts."""
    def setUp(self):
        self.c = Core(":memory:")
        self.article = "fixture/science/1"
        self.c.submit({"id":"fixture","plan":{"items":[{"channel":"science","count":1}],"day":None,"instructions":""}})
    def tearDown(self):
        self.c.db.close()
    def blocked_draft(self, reason="daily_budget_exhausted"):
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":reason})
    def test_release_reopens_stage_and_keeps_charges_and_slots(self):
        self.blocked_draft()
        self.c.reserve_call("fixture/science/1/draft",self.article,"draft","new_article",5)
        self.c.settle("fixture/science/1/draft",0)
        with self.c.db:
            self.c.db.execute("INSERT INTO images VALUES (?,?,?)",(self.article,2,"{}"))
        with self.assertRaises(CoreBlocked):
            release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted")
        released=release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted",confirm=True)
        self.assertEqual(released["previousStatus"],"blocked")
        self.assertNotIn("draft",self.c.check_stage(self.article,"draft"))
        self.assertEqual(self.c.article_work(self.article)[1],"draft")
        spend=self.c.db.execute("SELECT actual FROM spend WHERE id=?",("fixture/science/1/draft",)).fetchone()
        self.assertEqual(spend["actual"],"0")
        slots=[r[0] for r in self.c.db.execute("SELECT slot FROM images WHERE article=?",(self.article,))]
        self.assertEqual(slots,[2])
    def test_release_refuses_non_transient_and_unsettled(self):
        self.blocked_draft("quality_gate_incomplete")
        with self.assertRaises(CoreBlocked):
            release_blocked_stage(self.c,self.article,"draft","quality_gate_incomplete",confirm=True)
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"daily_budget_exhausted"})
        self.c.reserve_call("fixture/science/1/draft",self.article,"draft","new_article",5)
        with self.assertRaises(CoreBlocked):
            release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted",confirm=True)
        self.assertIn("draft",self.c.check_stage(self.article,"draft"))
    def test_release_never_reopens_a_passed_stage(self):
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        with self.assertRaises(CoreBlocked):
            release_blocked_stage(self.c,self.article,"plan","daily_budget_exhausted",confirm=True)
        self.assertEqual(self.c.article_work(self.article)[1],"draft")


class ReleaseSafetyTest(unittest.TestCase):
    """A release must not re-arm paid slots or fake a lifted budget, and must
    not be over-restricted for blockers that only external state can prove."""
    def setUp(self):
        self.now = [datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)]
        self.c = Core(":memory:", clock=lambda: self.now[0])
        self.article = "fixture/science/1"
        self.c.submit({"id":"fixture","plan":{"items":[{"channel":"science","count":1}],"day":None,"instructions":""}})
    def tearDown(self):
        self.c.db.close()
    def test_release_preserves_passed_image_slots(self):
        for stage in ("plan","draft","edit","visual_plan"):
            self.c.stage_result(self.article,stage,{"status":"passed"})
        self.c.stage_result(self.article,"images",{"status":"blocked","reason":"daily_budget_exhausted",
                                                   "checks":{"slot_0":"passed","slot_1":"passed","slot_2":"failed"}})
        released=release_blocked_stage(self.c,self.article,"images","daily_budget_exhausted",confirm=True)
        self.assertEqual(released["previousStatus"],"blocked")
        row=json.loads(self.c.check_stage(self.article,"images")["images"])
        self.assertEqual(row["status"],"released")
        self.assertEqual(row["releasedReason"],"daily_budget_exhausted")
        self.assertEqual(row["checks"],{"slot_0":"passed","slot_1":"passed"})
        self.assertEqual(self.c.article_work(self.article)[1],"images")
        with self.assertRaises(CoreBlocked):
            self.c.reserve_call("fixture/science/1/images/0",self.article,"images","missing_image",1,slot=0)
        self.c.reserve_call("fixture/science/1/images/2",self.article,"images","missing_image",1,slot=2)
        self.c.release_unsent("fixture/science/1/images/2")
    def test_daily_budget_release_reports_the_unreset_day(self):
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"daily_budget_exhausted"})
        released=release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted",confirm=True)
        self.assertEqual(released["advisories"],["daily_budget_not_reset"])
        self.assertNotIn("draft",self.c.check_stage(self.article,"draft"))
        self.assertEqual(self.c.article_work(self.article)[1],"draft")
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"daily_budget_exhausted"})
        self.now[0] = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted",
                                               confirm=True)["advisories"],[])
    def test_external_blockers_carry_no_budget_advisory(self):
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"routerai_balance_insufficient"})
        released=release_blocked_stage(self.c,self.article,"draft","routerai_balance_insufficient",confirm=True)
        self.assertEqual(released["advisories"],[])
        self.assertNotIn("draft",self.c.check_stage(self.article,"draft"))
        self.assertEqual(self.c.article_work(self.article)[1],"draft")
