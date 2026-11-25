import unittest
from src.editorial_zen_core import Core, CoreBlocked
from src.editorial_zen_dispatch import select_stage, dispatch_one
from src.editorial_zen_recovery import release_blocked_stage
from src.editorial_zen_cloud import CloudCore
from tests.test_editorial_zen_executor import Store

class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.c = Core(":memory:")
        self.id = "fixture"
        self.article = "fixture/science/1"
        self.c.submit({"id":self.id,"mode":"manual","plan":{"items":[{"channel":"science","count":1}],"day":None,"instructions":""}})
    def tearDown(self):
        self.c.db.close()
    def command(self, stage, mode="manual", revision=0):
        self.c.accept_stage_intent(self.id,{"id":f"stage:{revision}:{self.article}:{stage}","article":self.article,"stage":stage,"mode":mode,"modeRevision":revision,"createdAt":1})
    def test_live_config_never_invokes_executor(self):
        cloud=CloudCore(Store())
        cloud.access(lambda c:c.submit({"id":self.id,"plan":{"items":[{"channel":"science","count":1}],"day":None,"instructions":""}}),change=True)
        def forbidden(*a,**kw):
            self.fail("unexpected execution")
        self.assertEqual(dispatch_one(cloud,self.article,None,guard=forbidden,execute=forbidden)["reason"],"execution_disabled")
    def test_manual_and_recovery_gate(self):
        self.c.policy["execution_enabled"]=True
        self.assertEqual(select_stage(self.c,self.article)["reason"],"owner_stage_intent_required")
        self.command("plan")
        self.assertEqual(select_stage(self.c,self.article)["stage"],"plan")
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.assertEqual(select_stage(self.c,self.article)["reason"],"owner_stage_intent_required")
        self.command("draft")
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"uncertain"})
        self.assertEqual(select_stage(self.c,self.article)["reason"],"recovery_review_required")
    def test_released_budget_block_returns_the_same_stage(self):
        self.c.policy["execution_enabled"]=True
        self.command("plan")
        self.c.stage_result(self.article,"plan",{"status":"passed"})
        self.command("draft")
        self.c.stage_result(self.article,"draft",{"status":"blocked","reason":"daily_budget_exhausted"})
        self.assertEqual(select_stage(self.c,self.article)["reason"],"recovery_review_required")
        release_blocked_stage(self.c,self.article,"draft","daily_budget_exhausted",confirm=True)
        self.assertEqual(select_stage(self.c,self.article)["stage"],"draft")
        self.assertEqual(select_stage(self.c,self.article)["modeRevision"],0)
    def test_mode_transition_requires_new_boundary_command(self):
        self.c.policy["execution_enabled"]=True
        self.command("plan")
        self.c.apply_mode(self.id,"semi_autonomous",1)
        for stage in ("plan","draft","edit","visual_plan"):
            self.assertEqual(select_stage(self.c,self.article)["stage"],stage)
            self.c.stage_result(self.article,stage,{"status":"passed"})
        self.assertEqual(select_stage(self.c,self.article)["reason"],"owner_stage_intent_required")
        self.command("images","semi_autonomous",1)
        self.assertTrue(select_stage(self.c,self.article)["approvalRequired"])
        self.c.apply_mode(self.id,"autonomous",2)
        self.assertFalse(select_stage(self.c,self.article)["approvalRequired"])
        self.c.apply_mode(self.id,"manual",3)
        self.assertEqual(select_stage(self.c,self.article)["reason"],"owner_stage_intent_required")
