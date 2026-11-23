"""Final dense Dzen layout: TOC, independent cover, section image, immediate prose."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from docx import Document
from PIL import Image

from src.editorial_images import validate_plan
from src.editorial_layout import arrange, layout_issues
from src.editorial_memory import digest
from src.editorial_package import final_visual_gate, package
from src.editorial_providers import Blocked
from tests.editorial_scene_fixtures import certify
from src.editorial_scene_qa import section_text
from src.editorial_review import canonical, parse_article, structural_checks


def article_six():
    return {"intro": ["Вступление, которое следует сразу после обложки."],
            "sections": [
                {"heading": "Раздел " + str(i),
                 "paragraphs": ["Текст раздела " + str(i) + "."]}
                for i in range(1, 7)]}


def image_fixture(root):
    images = []
    folder = root / "source-images"
    folder.mkdir()
    for i in range(1, 8):
        output = folder / f"picture-{i}.jpg"
        Image.new("RGB", (80, 60), (12 * i, 15 * i, 22 * i)).save(output)
        images.append({"file": output.name, "sha256": digest(output.read_bytes()),
                       "section": 1 if i <= 2 else i - 1,
                       "cover_score": 10 if i == 2 else 0,
                       "scene": "Сцена " + str(i), "kind": "illustration"})
    return folder, images


class LayoutTests(unittest.TestCase):
    def test_seven_distinct_images_for_six_sections(self):
        with TemporaryDirectory() as temp:
            folder, images = image_fixture(Path(temp))
            cover, sections = arrange(article_six(), images)
            self.assertEqual(cover["file"], "picture-2.jpg")
            self.assertEqual(sections[1]["file"], "picture-1.jpg")
            self.assertEqual(sections[6]["file"], "picture-7.jpg")

    def test_missing_first_section_or_duplicate_cover_is_blocking(self):
        with TemporaryDirectory() as temp:
            _, images = image_fixture(Path(temp))
            article = article_six()
            self.assertTrue(layout_issues(article, images[:-1]))
            duplicate = [dict(row) for row in images]
            duplicate[0]["sha256"] = duplicate[1]["sha256"]
            self.assertIn("layout_duplicate_image_bytes", layout_issues(article, duplicate))
            misplaced = [dict(row) for row in images]
            misplaced[0]["section"] = 2
            self.assertIn("layout_image_distribution_invalid", layout_issues(article, misplaced))

    def test_image_plan_enforces_exact_distribution(self):
        settings = {"minimum_images": 7, "maximum_images": 8}
        rows = [{"section": 1 if i <= 2 else i - 1,
                 "kind": "illustration", "subject": "Реальная сцена номер " + str(i),
                 "brief": "Кинематографический эпизод с деталями номер " + str(i)}
                for i in range(1, 8)]
        validate_plan({"subject": "Тема городской истории", "images": rows},
                      6, "legends", settings)
        rows[0]["section"] = 3
        with self.assertRaisesRegex(Blocked, "image_plan_layout_distribution"):
            validate_plan({"subject": "Тема городской истории", "images": rows},
                          6, "legends", settings)

    def test_html_docx_and_markdown_have_correct_order_without_empty_blocks(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, images = image_fixture(root)
            article = article_six()
            report = {"article": article, "text_sha256": digest(canonical(article)),
                      "facts": {"evidence": []}}
            dest = root / "legends-01"
            for row in images:
                certify(row, dest.name, row["file"] == "picture-2.jpg", report["text_sha256"],
                        section_text(article["sections"][row["section"] - 1]))
            package(dest, "Окно на чердаке", [], report, images, source,
                    {"status": "rights_review_pending"})

            html = (dest / "Статья.html").read_text(encoding="utf-8")
            self.assertLess(html.index("<h1>"), html.index('<nav aria-label="Оглавление">'))
            self.assertLess(html.index("</nav>"), html.index('src="images/picture-2.jpg"'))
            self.assertLess(html.index('src="images/picture-2.jpg"'), html.index("Вступление, которое"))
            self.assertLess(html.index("Вступление, которое"), html.index('id="section-1"'))
            self.assertEqual(html.count("<figure>"), 7)
            self.assertEqual(html.count('<h2 id="section-'), 6)
            self.assertNotIn("<p></p>", html)
            for i in range(1, 7):
                heading = f'<h2 id="section-{i}">Раздел {i}</h2>'
                image = "picture-1.jpg" if i == 1 else f"picture-{i+1}.jpg"
                self.assertIn(heading + f'\n<figure><img src="images/{image}"', html)

            text = (dest / "Текст для SHA-256.txt").read_text(encoding="utf-8")
            self.assertEqual(digest(text), report["text_sha256"])
            doc = Document(dest / "Статья.docx")
            self.assertEqual(len(doc.inline_shapes), 7)
            self.assertEqual(doc.paragraphs[1].text, "Оглавление")
            self.assertNotIn("", [p.text for p in doc.paragraphs
                                   if p.style.name != "Normal" and not p._p.xpath(".//w:drawing")])
            xml = doc._element.body
            blocks = []
            for para in xml.xpath("./w:p"):
                if para.xpath(".//w:drawing"):
                    blocks.append("image")
                else:
                    blocks.append("".join(para.xpath(".//w:t/text()")))
            self.assertLess(blocks.index("Оглавление"), blocks.index("image"))
            self.assertLess(blocks.index("image"), blocks.index("Вступление, которое следует сразу после обложки."))
            for i in range(1, 7):
                self.assertEqual(blocks[blocks.index("Раздел " + str(i), 8) + 1], "image")
            checks = json.loads((dest / "Статус и проверки.json").read_text(encoding="utf-8"))
            self.assertEqual(checks["layout_status"], "verified")
            md = (dest / "Статья.md").read_text(encoding="utf-8")
            self.assertLess(md.index("**Оглавление**"), md.index("![Сцена 2]"))
            self.assertIn("**Раздел 1**\n\n![Сцена 1]", md)

    def test_photo_license_does_not_overwrite_subscription_url(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, images = image_fixture(root)
            first = images[0]
            first.update({
                "kind": "photo",
                "source": "Wikimedia Commons",
                "creator": "Research photographer",
                "license": "CC-BY-4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Research photographer",
                "source_page": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            })
            article = article_six()
            article["sections"][-1]["paragraphs"].append(
                "Подписывайтесь на «За гранью очевидного», чтобы читать новые статьи.")
            report = {"article": article, "text_sha256": digest(canonical(article)),
                      "facts": {"evidence": []}}
            folder = root / "science-01"
            for row in images:
                certify(row, folder.name, row["file"] == "picture-2.jpg", report["text_sha256"],
                        section_text(article["sections"][row["section"] - 1]))
            package(folder, "Тест фото", [], report, images, source, {})
            checks = json.loads((folder / "Статус и проверки.json").read_text(encoding="utf-8"))
            self.assertEqual(checks["subscription_link_status"], "linked")
            self.assertEqual(checks["subscription_url"], "https://dzen.ru/sciencebox")
            html = (folder / "Статья.html").read_text(encoding="utf-8")
            self.assertIn("https://creativecommons.org/licenses/by/4.0/", html)
            self.assertIn('href="https://dzen.ru/sciencebox"', html)

    def test_final_gate_rechecks_current_sections_local_files_and_html(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, images = image_fixture(root)
            article = article_six()
            text_sha = digest(canonical(article))
            for row in images:
                certify(row, "legends-01", row["file"] == "picture-2.jpg", text_sha,
                        section_text(article["sections"][row["section"] - 1]))
            report = {"article": article, "text_sha256": text_sha, "facts": {"evidence": []}}
            folder = root / "legends-01"
            ready = {"status": "rights_review_pending"}
            for _ in range(2):
                package(folder, "Synthetic article", [], report, images, source, ready)
            files = lambda name: (source / name).read_bytes()
            html, docx = ((folder / name).read_bytes() for name in ("Статья.html", "Статья.docx"))
            final_visual_gate(article, images, text_sha, folder.name, files, encoded=(html, docx))
            html = html.replace(b'images/picture-2.jpg', b'https://old.example/expired.jpg')
            with self.assertRaisesRegex(Blocked, "image_placement_mismatch"):
                final_visual_gate(article, images, text_sha, folder.name, files, encoded=(html, docx))
            asset = source / "picture-2.jpg"
            asset.unlink()
            with self.assertRaisesRegex(Blocked, "file_missing_or_invalid"):
                final_visual_gate(article, images, text_sha, folder.name, files)
            article["sections"][0]["paragraphs"].append("Изменение текста раздела.")
            with self.assertRaisesRegex(Blocked, "stale_image_needs_recheck"):
                final_visual_gate(article, images, digest(canonical(article)), folder.name, files)

    def test_intro_is_valid_before_first_heading(self):
        raw = ("Вступительный абзац.\n\n" +
               "\n\n".join(f"**Раздел {i}**\n\nТекст раздела {i}."
                           for i in range(1, 7)) +
               "\n\nПодписывайтесь на канал.")
        parsed = parse_article(raw)
        checks = structural_checks(parsed, [])
        self.assertNotIn("section_structure", checks["issues"])


if __name__ == "__main__":
    unittest.main()
