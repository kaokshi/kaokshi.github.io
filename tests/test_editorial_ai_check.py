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

    def test_owner_model_a_threshold_and_style_separation(self):
        def checked(p):
            return {"status": "completed", "data": {"score_percent": p}}
        r={"modernbert":checked(80),"lightgbm":checked(90),"transformer":checked(70),
           "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":.10}}}
        result=checker.threshold_review(r)
        self.assertEqual(result["model_a_index_0_100"],80)
        self.assertFalse(result["exceeds_owner_review_threshold"])
        self.assertIsNone(result["calibrated_ai_probability"])
        r["transformer"]["data"]["score_percent"]=90
        self.assertTrue(checker.threshold_review(r)["exceeds_owner_review_threshold"])
        self.assertIsNone(checker.threshold_review({})["model_a_index_0_100"])

    def test_normalized_aiw_probability(self):
        from unittest.mock import patch
        with patch.object(checker,"tool_result",return_value={"status":"completed","data":{"probability":.8,"skipped":False}}):
            result=checker.normalized_aiw_result(["aiw-ru"],Path("test.txt"))
        self.assertEqual(result["data"]["score_percent"],80)

    def test_slug_validation(self):
        with self.assertRaises(ValueError):
            checker.selected_articles("../../bad")


if __name__ == "__main__":
    unittest.main()
