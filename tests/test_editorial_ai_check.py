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

    def test_each_model_must_be_at_most_ten_percent(self):
        def reports(modern, light, accent):
            return {
                "modernbert": {"status": "completed", "data": {"probability": modern}},
                "lightgbm": {"status": "completed", "data": {"probability": light}},
                "ai_accent_detector": {"status": "completed", "data": {"ai_accent_score": accent}},
            }
        self.assertTrue(checker.threshold_review(reports(0.10, 0.10, 0.10))["passed"])
        self.assertFalse(checker.threshold_review(reports(0.11, 0.01, 0.01))["passed"])
        self.assertFalse(checker.threshold_review(reports(0.01, 0.11, 0.01))["passed"])
        self.assertFalse(checker.threshold_review(reports(0.01, 0.01, 0.11))["passed"])
        result = checker.threshold_review(reports(0.0045, 0.6932, 0.0991))
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"]["lightgbm"]["score_percent"], 69.32)

    def test_missing_score_does_not_pass(self):
        checks = {k: {"status": "completed", "data": {}} for k in
                  ("modernbert", "lightgbm", "ai_accent_detector")}
        self.assertFalse(checker.threshold_review(checks)["passed"])

    def test_slug_validation(self):
        with self.assertRaises(ValueError):
            checker.selected_articles("../../bad")


if __name__ == "__main__":
    unittest.main()
