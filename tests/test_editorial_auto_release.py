"""Guard release application and RSS handling; no real publishing."""
import json
from pathlib import Path
from datetime import datetime, timezone
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_auto_route as router
import editorial_auto_release as release
import build

class AutoReleaseTests(unittest.TestCase):
    def setUp(self):
        # These cases isolate routing; the layout suite exercises the real cover gate.
        self.enterContext(patch("editorial_auto_route.layout_findings", return_value=[]))
        self.enterContext(patch("build.layout_findings", return_value=[]))
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.files=[]
        self.sources=[]
        for i in range(7):
            name=f"content/assets/x{i}.jpg"
            p=self.root/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(b"valid-image")
            self.files.append(name)
            self.sources.append({"file":name,"license":"OWN_WORK","license_verified":True,
                                 "source_page":"https://example.org/image/"+str(i)})
        self.article={"channel":"legends","slug":"legends-new-auto-test","title":"Пример истории",
                      "body_html":"<p>"+"Ветер шумел в ночном дворе. "*150+"</p>",
                      "image_files":self.files,"image_sources":self.sources,"rights_basis":"Авторские фото",
                      "status":"draft","auto_release_candidate":True,"release_hold":False,
                      "publish_at":"2026-11-20T10:00:00+03:00"}
        self.root_patch=patch.object(router,"ROOT",self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        digest=router.digest_article(self.article)
        base={"status":"completed","article_sha256":digest,
              "report_ref":"https://github.com/kaokshi/kaokshi.github.io/actions/runs/900"}
        self.evidence={"ai":{**base,"checks":{p:{"status":"completed","score_percent":5}
                          for p in router.REQUIRED_AI}},
                       "quality":{**base,"checks":{p:{"status":"completed"}
                                  for p in router.REQUIRED_QUALITY}},
                       "factual":{**base,"original_fiction_confirmed":True,
                                  "fiction_not_presented_as_fact":True},
                       "style_warning_count":0,"image_warning_count":0,"source_warning_count":0}

    def test_release_refused_until_ai_threshold_validated(self):
        result=release.prepare_release(self.article,self.evidence,
                    {"auto_release_enabled":True,"publication_enabled":False})
        self.assertEqual(result["decision"]["disposition"],"send_for_owner_review")
        self.assertIn("ai_auto_release_threshold_not_validated",result["decision"]["blockers"])
        self.assertIsNone(result["article"])
        self.assertIsNone(result["settings"])

    def test_disabled_feature_leaves_everything_unchanged(self):
        result=release.prepare_release(self.article,self.evidence,
                    {"auto_release_enabled":False,"publication_enabled":False})
        self.assertIsNone(result["article"])
        self.assertIsNone(result["settings"])

    def test_unverified_image_never_publishes(self):
        self.article["image_sources"][0]["license_verified"]=False
        result=release.prepare_release(self.article,self.evidence,
                    {"auto_release_enabled":True,"publication_enabled":False})
        self.assertIsNone(result["article"])
        self.assertFalse(result["decision"]["publication_authorized"])

    def test_release_cannot_enable_rss_without_calibration(self):
        result=release.prepare_release(self.article,self.evidence,{"auto_release_enabled":True})
        self.assertIsNone(result["article"])
        self.assertIsNone(result["settings"])
        self.assertFalse(result["decision"]["publication_authorized"])


if __name__=="__main__":
    unittest.main()
