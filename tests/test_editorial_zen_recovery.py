import unittest
from src.editorial_zen_core import Core, CoreBlocked
from src.editorial_zen_recovery import recovery_status, confirmed_retry

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
