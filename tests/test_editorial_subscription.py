"""Link only the *verified* channel name in exported review files."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document

from src.editorial_memory import digest
from src.editorial_package import package
from src.editorial_review import canonical
from src.editorial_subscription import approved_url, split_cta


class SubscriptionTests(unittest.TestCase):
    def test_unverified_channel_url_is_not_invented(self):
        self.assertIsNone(approved_url("science"))
        self.assertIsNone(approved_url("legends"))
        self.assertIsNone(approved_url("cinema"))

    def test_untrusted_urls_are_rejected(self):
        cases = ("http://dzen.ru/id-example", "https://evil.example/channel",
                 "https://dzen.ru.evil.example/channel", "javascript:alert(1)",
                 "https://dzen.ru@evil.example/channel", "https://dzen.ru/path?redirect=x")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "urls.json"
            for url in cases:
                path.write_text(json.dumps({"science": url}), encoding="utf-8")
                with self.assertRaises(ValueError, msg=url):
                    approved_url("science", path)

    def test_name_only_is_linked_without_changing_prose(self):
        value = "Подписывайся на «За гранью очевидного» — будем разбираться вместе."
        result = split_cta(value, "science", "https://dzen.ru/id-test")
        self.assertEqual("".join(result), value)
        self.assertEqual(result[1], "За гранью очевидного")

    def test_real_clickable_anchor_in_docx_and_html_and_hash_unchanged(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "science-01"
            article = {"intro": [], "sections": [
                {"heading": "Первая часть", "paragraphs": ["Важная информация."]},
                {"heading": "Заключение", "paragraphs": [
                    "Подписывайся на «За гранью очевидного» — будем разбираться вместе."]},
            ]}
            report = {"article": article, "text_sha256": digest(canonical(article)),
                      "facts": {"evidence": []}}
            test_url = "https://dzen.ru/id-test"
            with patch("src.editorial_package.approved_url", return_value=test_url):
                package(folder, "Тестовая статья", [], report, [], root, {"status": "review_pending"})
            html = (folder / "Статья.html").read_text(encoding="utf-8")
            self.assertIn('<a href="' + test_url + '">За гранью очевидного</a>', html)
            document = Document(folder / "Статья.docx")
            cta = document.paragraphs[-1]
            self.assertEqual(cta.text, article["sections"][-1]["paragraphs"][-1])
            self.assertEqual(len(cta.hyperlinks), 1)
            self.assertEqual(cta.hyperlinks[0].address, test_url)
            with ZipFile(BytesIO((folder / "Статья.docx").read_bytes())) as archive:
                self.assertTrue(any(test_url.encode("utf-8") in archive.read(n)
                                    for n in archive.namelist() if n.endswith(".rels")))
            checks = json.loads((folder / "Статус и проверки.json").read_text(encoding="utf-8"))
            self.assertEqual(checks["text_sha256"], report["text_sha256"])
            self.assertEqual(checks["subscription_link_status"], "linked")
            self.assertIn("[За гранью очевидного](" + test_url + ")",
                          (folder / "Статья.md").read_text(encoding="utf-8"))

    def test_missing_url_keeps_text_and_flags_pending(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            article = {"intro": [], "sections": [
                {"heading": "Раздел", "paragraphs": ["Подписывайся на «За гранью очевидного»."]},
            ]}
            report = {"article": article, "text_sha256": digest(canonical(article)),
                      "facts": {"evidence": []}}
            folder = root / "science-01"
            package(folder, "Тест", [], report, [], root, {})
            document = Document(folder / "Статья.docx")
            self.assertFalse(document.paragraphs[-1].hyperlinks)
            checks = json.loads((folder / "Статус и проверки.json").read_text(encoding="utf-8"))
            self.assertEqual(checks["subscription_link_status"], "channel_url_or_name_missing")


if __name__ == "__main__":
    unittest.main()
