import unittest
from scripts.zen_developer_preview import validate_manifest

class DeveloperPreviewTests(unittest.TestCase):
    def fixture(self):
        ident="12345678-1234-4234-8234-123456789abc"
        return {"schema":"zen-developer/handoff-v1","id":ident,"baseSha":"a"*40,"repository":"kaokshi/kaokshi.github.io","branch":"assistant/opus-task-"+ident,"text":"Test","reasoning":"high","limits":{"taskKop":1000,"dailyKop":5000,"monthlyKop":150000,"policyVersion":0},"executionEnabled":False,"paidDispatchAuthorized":False,"writeAuthorized":False,"publicationAuthorized":False}
    def test_preview(self):
        result=validate_manifest(self.fixture())
        self.assertEqual(result["paidRequests"],0)
        self.assertEqual(result["gitWrites"],0)
    def test_rejects_authority_and_main(self):
        for key,value in [("executionEnabled",True),("paidDispatchAuthorized",True),("writeAuthorized",True),("publicationAuthorized",True),("branch","main"),("baseSha","main"),("repository","other/repo")]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_manifest({**self.fixture(),key:value})
    def test_limits(self):
        value=self.fixture()
        value["limits"]["taskKop"]=6000
        with self.assertRaises(ValueError):
            validate_manifest(value)
