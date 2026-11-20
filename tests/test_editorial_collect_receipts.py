"""Tests of honest evidence collection, no synthetic verification is published."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_auto_route as source
import editorial_collect_receipts as collector
class Receipts(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.ps=patch.object(source,"ROOT",self.root)
        self.ps.start()
        self.addCleanup(self.ps.stop)
        path="content/assets/a.jpg"
        (self.root/path).parent.mkdir(parents=True,exist_ok=True)
        (self.root/path).write_bytes(b"image")
        self.article={"slug":"legends-test","channel":"legends","body_html":"<p>Текст</p>",
                      "image_files":[path],"image_sources":[{"file":path}]}
        self.quality={"results":[{"slug":"legends-test","style":{"cliche_occurrences":{},"long_sentences":[]},
                       "images":{"count":7}}],
                      "image_duplicates":{"near_duplicate_status":"completed",
                                          "scanned_images":7,"duplicates":[]},
                      "external_links_verified":False,"image_rights_verified_by_this_check":False}
        self.grammar={"slug":"legends-test","status":"completed","issues":[],"issue_count":0}
        self.rss={x:{"valid":True,"item_count":0} for x in ("legends","science","cinema")}
    def receipt(self,quality=None):
        return collector.collect(self.article,self.quality if quality is None else quality,
            self.grammar,self.rss,"https://github.com/a/b/actions/runs/123")
    def test_only_actual_completed_tools_are_passed(self):
        result=self.receipt()
        self.assertEqual(result["checks"]["grammar"]["status"],"completed")
        self.assertEqual(result["checks"]["rss_dry_run"]["status"],"completed")
        self.assertEqual(result["checks"]["sources"]["status"],"needs_review")
        self.assertEqual(result["checks"]["fact_check"]["status"],"needs_review")
        self.assertEqual(result["checks"]["image_rights"]["status"],"needs_review")
        self.assertFalse(result["publication_authorized"])
    def test_missing_quality_report_is_not_claimed_complete(self):
        r=self.receipt({})
        self.assertEqual(r["status"],"incomplete")
        self.assertEqual(r["checks"]["style"]["status"],"needs_review")
    def test_suggestions_go_to_editor_not_silent_text_rewrite(self):
        self.grammar["issues"]=[{"message":"Проверьте слово"}]
        self.grammar["issue_count"]=1
        self.assertEqual(self.receipt()["checks"]["grammar"]["status"],"needs_review")
    def test_multiple_cliches_trigger_actionable_content_revision(self):
        self.quality["results"][0]["style"]["cliche_occurrences"]={"таким образом":6}
        r=self.receipt()
        self.assertEqual(r["checks"]["style"]["status"],"failed")
        self.assertTrue(r["checks"]["style"]["findings"])
    def test_missing_real_run_url_cannot_be_verification(self):
        r=collector.collect(self.article,self.quality,self.grammar,self.rss,"not-a-run")
        self.assertEqual(r["status"],"incomplete")
    def test_invalid_rss_dry_run_remains_unverified(self):
        self.rss["science"]["valid"]=False
        self.assertEqual(self.receipt()["checks"]["rss_dry_run"]["status"],"needs_review")
if __name__=="__main__":unittest.main()
