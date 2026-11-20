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

    def test_automatic_release_is_separate_from_user_approval(self):
        result=release.prepare_release(self.article,self.evidence,
                    {"auto_release_enabled":True,"publication_enabled":False})
        self.assertEqual(result["decision"]["disposition"],"automatic_release_candidate")
        self.assertTrue(result["article"]["publication_authorized"])
        self.assertTrue(result["settings"]["publication_enabled"])
        self.assertEqual(result["article"]["release_mode"],"automatic_with_verified_evidence")
        self.assertEqual(result["article"]["auto_release_record"]["article_sha256"],
                         router.digest_article(result["article"]))
        self.assertEqual(self.article["status"],"draft")

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

    def test_article_mutation_stops_rss_output_even_after_decision(self):
        prepared=release.prepare_release(self.article,self.evidence,{"auto_release_enabled":True})
        post=prepared["article"]
        folder=self.root/"content/articles"
        folder.mkdir(parents=True,exist_ok=True)
        fp=folder/"legends-new-auto-test.json"
        fp.write_text(json.dumps(post,ensure_ascii=False),encoding="utf-8")
        with patch.object(build,"ARTICLES",folder),patch.object(build,"BASE",self.root),patch.object(build,"config",
                  return_value=({"publication_enabled":True},"https://kaokshi.github.io")):
            result=build.read_posts(datetime(2026,11,21,tzinfo=timezone.utc))
            self.assertEqual(len(result),1)
            post["body_html"]+="<p>Подменённый текст.</p>"
            fp.write_text(json.dumps(post,ensure_ascii=False),encoding="utf-8")
            self.assertEqual(build.read_posts(datetime(2026,11,21,tzinfo=timezone.utc)),[])

if __name__=="__main__":
    unittest.main()
