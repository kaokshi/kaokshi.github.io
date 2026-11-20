import sys
import unittest
from pathlib import Path
from datetime import datetime,timezone
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import build

class WebsiteTest(unittest.TestCase):
    def test_three_channels(self):
        self.assertEqual(set(build.CHANNELS),{"cinema","legends","science"})
    def test_date_requires_timezone(self):
        with self.assertRaises(ValueError):
            build.parse_date("2026-12-01T20:00:00")
    def test_build_produces_three_valid_rss(self):
        build.build(datetime(2026,11,19,tzinfo=timezone.utc))
        for key in build.CHANNELS:
            rss=build.OUT/"feeds"/(key+".xml")
            root=ET.fromstring(rss.read_text("utf-8"))
            self.assertEqual(root.tag,"rss")
            self.assertEqual(root.attrib["version"],"2.0")
    def test_publication_globally_disabled(self):
        settings, _ = build.config()
        self.assertIs(settings.get("auto_release_enabled"), True)
        if settings.get("publication_enabled") is False:
            self.assertEqual(build.read_posts(datetime(2026,12,10,tzinfo=timezone.utc)), [])
        else:
            self.assertTrue(all(p["publication_authorized"] for p in build.read_posts(datetime(2026,12,10,tzinfo=timezone.utc))))
    def test_approved_article_requires_release_authorization(self):
        import unittest.mock as mock
        now = datetime(2100,1,1,tzinfo=timezone.utc)
        with mock.patch.object(build, "config", return_value=({"publication_enabled":True},"https://kaokshi.github.io")):
            self.assertTrue(all(p.get("publication_authorized") is True for p in build.read_posts(now)))

    def test_multi_provider_review_gate(self):
        import json
        import tempfile
        import unittest.mock as mock
        from pathlib import Path
        now = datetime(2100,1,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            article = {
                "channel":"science", "slug":"check-example",
                "title":"Тестовое название",
                "body_html":"<p>" + "а" * 3100 + "</p>",
                "status":"approved",
                "publication_authorized":True, "release_hold":False,
                "editorial_reviewed":True, "rights_reviewed":True,
                "publish_at":"2099-01-01T00:00:00+03:00",
                "image_files":["asset"] * 7,
                "ai_score":0, "ai_check_provider":"ZeroGPT",
                "ai_reviewed":False, "ai_checks":[],
            }
            (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
            with mock.patch.object(build,"ARTICLES",folder), mock.patch.object(build,"BASE",folder), mock.patch.object(build,"config",return_value=({"publication_enabled":True},"https://kaokshi.github.io")), mock.patch.object(Path,"is_file",return_value=True):
                self.assertEqual(build.read_posts(now),[], "Legacy ai_score must never allow release")
                article["ai_reviewed"]=True
                article["ai_checks"]=[{"provider":"Copyleaks","status":"completed"}]
                (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
                self.assertEqual(build.read_posts(now),[], "One provider must not be enough")
                article["ai_checks"].append({"provider":"GPTZero","status":"completed"})
                (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
                self.assertEqual(build.read_posts(now), [], "Legacy two-provider checks cannot bypass ruBERT gate")
                article["ai_checks"] = [
                    {"provider": "transformer", "status": "completed",
                     "report_ref": "https://github.com/example/transformer"},
                    {"provider": "modernbert", "status": "completed", "report_ref": "https://github.com/example"},
                    {"provider": "lightgbm", "status": "completed", "report_ref": "https://github.com/example"},
                    {"provider": "ai_accent_detector", "status": "completed", "report_ref": "https://github.com/example"},
                ]
                (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
                self.assertEqual(build.read_posts(now), [], "A missing layout must block release")
                self.enterContext(mock.patch.object(build, "layout_findings", return_value=[]))
                self.assertEqual(len(build.read_posts(now)), 1)
                article["ai_checks"][0]["provider"] = "rubert_v4"
                (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
                self.assertEqual(build.read_posts(now), [], "Legacy ruBERT cannot replace the new model")
                article["ai_checks"][0]["provider"] = "transformer"
                article["ai_checks"][1]["report_ref"] = ""
                (folder/"test.json").write_text(json.dumps(article,ensure_ascii=False),encoding="utf-8")
                self.assertEqual(build.read_posts(now), [], "A missing comparison report must block release")

    def test_draft_not_published(self):
        self.assertTrue(all(p["status"] == "approved" for p in build.read_posts(datetime(2026,11,19,tzinfo=timezone.utc))))
if __name__=="__main__":
    unittest.main()
