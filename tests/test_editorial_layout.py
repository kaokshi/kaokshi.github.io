import base64
import hashlib
import json
import os
from io import BytesIO
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build
from editorial_cover import CRITERIA, cover_selection, verify_cover, vision
from editorial_layout import arrange, docx_body, layout_findings, parse_sections, repair_sections

PROSE = ["Ночью поднялся ветер.", "Ветер гнул берёзы.", "Утром вышло солнце.", "Снег растаял."]
PLAN = [{"start": 1, "heading": "Ночной ветер"}, {"start": 3, "heading": "Первый луч"}]


class SectionTests(unittest.TestCase):
    def test_repair_and_recheck(self):
        def provider(task, request):
            self.assertEqual(task, "section_plan")
            self.assertEqual(request["paragraphs"], PROSE)
            return {"sections": PLAN}
        html = arrange(PROSE, [PLAN[0], dict(PLAN[1], heading="Ночной ветер!")], provider, "Погода")
        for channel in build.CHANNELS:
            draft = {"channel": channel, "title": "Погода", "body_html": html}
            root = parse_sections(html)
            self.assertEqual([s.findtext("h2/strong") for s in root], [r["heading"] for r in PLAN])
            for broken in (html.replace("<strong>", "").replace("</strong>", ""),
                           html.replace("<h2><strong>Первый луч</strong></h2>", "")):
                with self.assertRaises(ValueError):
                    parse_sections(broken)
                self.assertEqual(repair_sections(dict(draft, body_html=broken), provider)["body_html"], html)


class CoverTests(unittest.TestCase):
    def test_vision_request_contains_article_full_view_and_crop(self):
        from PIL import Image
        article = {"channel": "cinema", "title": "Фильм о переправе",
                   "body_html": arrange(PROSE, PLAN, None, "")}
        def transport(endpoint, path, payload, timeout):
            msg = payload["messages"][0]
            self.assertEqual(path, "/api/chat")
            self.assertIn(article["title"], msg["content"])
            self.assertIn("кадр", msg["content"])
            views = [Image.open(BytesIO(base64.b64decode(v))) for v in msg["images"]]
            self.assertEqual([im.size for im in views], [(640, 800), (192, 108)])
            return {"message": {"content": json.dumps(dict.fromkeys(CRITERIA, 5))}}
        with patch.dict(os.environ, ZEN_VISION_MODEL="vision-test"), patch("editorial_cover.read_json_response", transport):
            self.assertEqual(vision(article, Image.new("RGB", (640, 800)))["topic"], 5)

    def test_selection_assembly_exports_and_replacement(self):
        from PIL import Image
        from docx import Document
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content/assets").mkdir(parents=True)
            manifest = []

            def write(index, shade):
                name = f"content/assets/{index}.jpg"
                Image.new("RGB", (800, 600), (shade, shade, shade)).save(root / name)
                return {"id": str(index), "file": name, "rights_verified": True, "source": "fixture",
                        "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}

            for i in range(7):
                manifest.append(write(i, 10*i))
            calls = []

            def assess(article, image):
                shade = image.getpixel((0, 0))[0]
                calls.append(shade)
                # Test oracle: the brightest candidate is visually attractive but off topic.
                return {**dict.fromkeys(CRITERIA, 4), "topic": 0 if shade == 60 else 5,
                        "appeal": 5 if shade == 50 else 2, "defect": "", "reason": article["title"]}

            for channel in build.CHANNELS:
                article = {"channel": channel, "title": "Первый луч", "body_html": arrange(PROSE, PLAN, None, "")}
                prepared = cover_selection(article, manifest, root, assess)
                self.assertEqual(layout_findings(prepared, root), [])
                rows = prepared["cover_selection"]["images"]
                self.assertEqual(rows[0]["id"], "5")
                self.assertTrue(rows[0]["is_cover"])
                self.assertFalse(rows[-1]["eligible"])
                self.assertEqual(sum(r["is_cover"] for r in rows), 1)
                images = [img.attrib["src"] for img in parse_sections(prepared["body_html"]).iter("img")]
                self.assertEqual(len(set(images)), 7)
                self.assertEqual(images[0], "/assets/5.jpg")
                with patch.object(build, "BASE", root):
                    feed = build.rss(channel, build.CHANNELS[channel],
                        [dict(prepared, date=datetime.now(timezone.utc), path="/test")], "https://example.org")
                item = ET.fromstring(feed).find("channel/item")
                body = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
                self.assertIn("<h2><strong>Первый луч</strong></h2>", body)
                self.assertEqual(ET.fromstring("<body>"+body+"</body>").find(".//img").get("src"),
                                 item.find("enclosure").get("url"))
                doc = Document()
                docx_body(doc, prepared, root)
                doc.save(root / "article.docx")
                doc = Document(root / "article.docx")
                heads = [p for p in doc.paragraphs if p.style.name == "Heading 2"]
                self.assertEqual([p.text for p in heads], [r["heading"] for r in PLAN])
                self.assertTrue(all(p.runs[0].bold for p in heads))
                self.assertEqual(len(doc.inline_shapes), 7)
            from editorial_export_docx_zip import export_article
            from editorial_export_manual_10 import make_one
            paragraphs = [p + " Снег блестел." * 85 for p in PROSE]
            article = dict(article, channel="legends", slug="test-article", status="draft",
                           release_hold=True, publication_authorized=False,
                           body_html=arrange(paragraphs, PLAN, None, ""))
            assembled = cover_selection(article, manifest, root, assess)
            articles = root / "content/articles"
            articles.mkdir()
            (articles / "test-article.json").write_text(json.dumps(assembled))
            for module, export in [("editorial_export_docx_zip", export_article), ("editorial_export_manual_10", make_one)]:
                output = root / (module + ".docx")
                with patch(module + ".ROOT", root):
                    export("legends", "test-article", output)
                doc = Document(output)
                heads = [p for p in doc.paragraphs if p.style.name == "Heading 2"]
                self.assertEqual([p.text for p in heads], [r["heading"] for r in PLAN])
                self.assertTrue(all(p.runs[0].bold for p in heads))
                self.assertEqual(len(doc.inline_shapes), 7)
            manifest[5] = write(5, 89)
            with self.assertRaisesRegex(ValueError, "changed"):
                verify_cover(prepared, root)
            manifest[2] = write(2, 50)
            calls.clear()
            replaced = cover_selection(prepared, manifest, root, assess)
            self.assertEqual(len(calls), 7)
            self.assertEqual(replaced["cover_selection"]["images"][0]["id"], "2")
            self.assertEqual(layout_findings(replaced, root), [])
