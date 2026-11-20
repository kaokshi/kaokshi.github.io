"""Safe owner-preview artifact tests; nothing enters GitHub Pages or RSS."""
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_auto_route as route
import editorial_stage_preview as preview

class PreviewTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.p=patch.object(route,"ROOT",self.root)
        self.p.start()
        self.addCleanup(self.p.stop)
        name="content/assets/art.svg"
        full=self.root/name
        full.parent.mkdir(parents=True,exist_ok=True)
        full.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>',encoding="utf-8")
        self.article={"slug":"legends-demo","channel":"legends",
                      "title":"<script>alert(1)</script>",
                      "body_html":"<p>Текст <script>alert(1)</script> и конец.</p>",
                      "image_files":[name]}
        self.verdict={"disposition":"ready_for_next_stage",
                      "publication_authorized":False,
                      "article_sha256":route.digest_article(self.article)}
    def test_approved_preview_builds_offline_files_with_escaped_title(self):
        out=self.root/"editor-preview"
        result=preview.make_preview(self.article,self.verdict,out,root=self.root)
        self.assertFalse(result["publication_authorized"])
        self.assertEqual(result["images"],1)
        html=(out/"index.html").read_text()
        self.assertIn("&lt;script&gt;",html)
        self.assertNotIn('<script>',html)
        self.assertTrue((out/"images/image-01.svg").is_file())
        self.assertFalse((self.root/"site").exists())
    def test_not_ready_never_generates_preview(self):
        self.verdict["disposition"]="needs_revision"
        with self.assertRaises(ValueError):
            preview.make_preview(self.article,self.verdict,self.root/"foo",root=self.root)
    def test_stale_digest_prevents_preview(self):
        self.article["body_html"]+="<p>Другая редакция</p>"
        with self.assertRaises(ValueError):
            preview.make_preview(self.article,self.verdict,self.root/"foo",root=self.root)
    def test_outside_asset_path_rejected(self):
        self.article["image_files"]=["../../etc/passwd"]
        self.verdict["article_sha256"]=route.digest_article(self.article)
        with self.assertRaises(ValueError):
            preview.make_preview(self.article,self.verdict,self.root/"foo",root=self.root)
    def test_paused_cinema_cannot_be_previewed(self):
        self.article["channel"]="cinema"
        self.verdict["article_sha256"]=route.digest_article(self.article)
        with self.assertRaises(ValueError):
            preview.make_preview(self.article,self.verdict,self.root/"foo",root=self.root)
if __name__=="__main__": unittest.main()
