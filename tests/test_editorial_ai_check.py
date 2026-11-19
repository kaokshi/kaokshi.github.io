"""Fast unit tests for safe, read-only article analysis (no models required)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import editorial_ai_check as checker


class EditorialAiChecksTest(unittest.TestCase):
    def test_html_to_text(self):
        text = checker.extract_text('<h2>Заголовок</h2><p>Текст <strong>статьи</strong> &amp; факт.</p>')
        self.assertIn("Заголовок", text)
        self.assertIn("Текст статьи & факт.", text)
        self.assertNotIn("<p>", text)

    def test_missing_tools_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "input.txt"
            doc.write_text("текст", encoding="utf-8")
            with patch.object(checker.subprocess, "run", side_effect=FileNotFoundError("missing")):
                result = checker.tool_result(["missing-command"], doc)
            self.assertEqual(result["status"], "error")

    def test_review_report_never_authorizes_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "article.json"
            path.write_text(json.dumps({"slug": "test", "channel": "science",
                                        "body_html": "<p>Тестовая статья.</p>",
                                        "status": "draft", "publication_authorized": False}, ensure_ascii=False), encoding="utf-8")
            mocked = {"status": "completed", "data": {"example": True}}
            with patch.object(checker, "ROOT", Path(tmp)), patch.object(checker, "tool_result", return_value=mocked):
                result = checker.evaluate(path)
            self.assertTrue(result["all_checks_completed"])
            self.assertIs(result["editorial_approved"], False)
            self.assertIs(result["publication_authorized"], False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "draft")
            self.assertFalse(saved["publication_authorized"])

    def test_rubert_only_threshold(self):
        def reports(rubert, modern=0.95, light=0.95, accent=0.95):
            return {
                "rubert_v4": {"status": "completed", "data": {"probability": rubert}},
                "modernbert": {"status": "completed", "data": {"probability": modern}},
                "lightgbm": {"status": "completed", "data": {"probability": light}},
                "ai_accent_detector": {"status": "completed", "data": {"ai_accent_score": accent}},
            }
        self.assertTrue(checker.threshold_review(reports(0.10))["passed"])
        self.assertTrue(checker.threshold_review(reports(0.01, 1, 1, 1))["passed"])
        self.assertFalse(checker.threshold_review(reports(0.10001, 0, 0, 0))["passed"])
        self.assertEqual(checker.threshold_review(reports(0.05))["primary_model"], "rubert_v4")
        self.assertEqual(checker.threshold_review(reports(0.05))["checks"]["rubert_v4"]["score_percent"], 5.0)

    def test_missing_primary_score_does_not_pass(self):
        self.assertFalse(checker.threshold_review({})["passed"])
        self.assertFalse(checker.threshold_review({"rubert_v4": {"status": "completed", "data": {"probability": True}}})["passed"])
        self.assertFalse(checker.threshold_review({"rubert_v4": {"status": "error"}})["passed"])

    def test_rubert_windows_cover_long_text(self):
        from rubert_v4_runner import windows
        words = [f"слово{i}" for i in range(340)]
        segments = windows(" ".join(words))
        self.assertGreater(len(segments), 2)
        self.assertEqual(segments[0].split()[0], "слово0")
        self.assertEqual(segments[-1].split()[-1], "слово339")
        self.assertTrue(all(len(x.split()) <= 85 for x in segments))

    def test_slug_validation(self):
        with self.assertRaises(ValueError):
            checker.selected_articles("../../bad")


if __name__ == "__main__":
    unittest.main()
