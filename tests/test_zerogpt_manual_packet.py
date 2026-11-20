import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import zerogpt_manual_packet as manual

class ZeroGPTManualPacketTests(unittest.TestCase):
    def test_extracts_paragraphs_without_markup(self):
        text = manual.clean_article({"body_html": "<p>Первый абзац.</p><h2>Заголовок</h2><p>Второй абзац.</p>"})
        self.assertIn("Первый абзац.", text)
        self.assertIn("Второй абзац.", text)
        self.assertNotIn("<p>", text)

    def test_writes_exact_hash_and_never_claims_external_check(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "test-article.json"
            src.write_text(json.dumps({"slug": "test-article", "channel": "science",
                "body_html": "<p>Это пробный текст.</p>"}), encoding="utf-8")
            with patch.object(manual, "selected_articles", return_value=[src]):
                manifest = manual.prepare("test-article", Path(temp) / "out")
            text = (Path(temp) / "out" / "test-article.txt").read_text(encoding="utf-8").rstrip("\n")
            self.assertEqual(manifest["text_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
            self.assertIsNone(manifest["external_score"])
            self.assertEqual(manifest["external_check"], "not_performed")
            self.assertFalse(manifest["publication_authorized"])

if __name__ == "__main__":
    unittest.main()
