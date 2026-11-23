"""Offline verification that Gemini edits are metered and audited exactly once."""
import json
import os
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

from src.editorial_providers import ProviderError, gemini_text
from src.editorial_brain import Article


class GeminiReliabilityTests(unittest.TestCase):
    def pilot(self):
        return {"EDITORIAL_GROQ_GEMINI_KLEIN_PILOT": "true",
                "EDITORIAL_ALT_MODELS_TEST": "true",
                "EDITORIAL_ISOLATED_VALIDATION": "true",
                "EDITORIAL_GROQ_FREE_ONLY": "true",
                "GITHUB_ACTIONS": "true"}

    def sample(self):
        return json.dumps({
            "modelVersion": "gemini-3.6-flash",
            "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"article":"Тестовый текст","issues":[],"claims":[]}'}]}}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 32,
                              "totalTokenCount": 152}}, ensure_ascii=False).encode()

    def test_structured_edit_uses_one_json_http_call_not_sse(self):
        with patch.dict(os.environ, self.pilot()), \
             patch("src.editorial_alt_models.require_isolated"), \
             patch("src.editorial_alt_models.gemini_pilot", return_value=True), \
             patch("src.editorial_providers.gemini_headers", return_value={"x-goog-api-key": "fixture"}), \
             patch("src.editorial_providers.request", return_value=self.sample()) as send:
            result = gemini_text("fixture prompt", 1200, response_json=True)
        self.assertEqual(result["model"], "gemini-3.6-flash")
        self.assertEqual(result["usage"]["total_tokens"], 152)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.args[0].endswith(":generateContent"))
        self.assertNotIn("streamGenerateContent", send.call_args.args[0])
        self.assertEqual(json.loads(send.call_args.kwargs["data"])["generationConfig"]["responseMimeType"],
                         "application/json")

    def test_structured_503_does_not_hide_retry_inside_same_reservation(self):
        with patch.dict(os.environ, self.pilot()), \
             patch("src.editorial_alt_models.require_isolated"), \
             patch("src.editorial_alt_models.gemini_pilot", return_value=True), \
             patch("src.editorial_providers.gemini_headers", return_value={"x-goog-api-key": "fixture"}), \
             patch("src.editorial_providers.request",
                   side_effect=ProviderError("http_503_transient")) as send:
            with self.assertRaisesRegex(ProviderError, "http_503_transient"):
                gemini_text("fixture prompt", 1200, response_json=True)
        self.assertEqual(send.call_count, 1)

    def test_combined_edit_uses_one_gemini_operation_for_edit_and_qa(self):
        article = Article.__new__(Article)
        article.channel = "legends"
        article.title = "Испытательный рассказ"
        article.id = "legends-01"
        article.records = []
        article.film = None
        article.config = {"per_article_calls": {"gemini": 2}}
        article.memory = SimpleNamespace(article=Mock())
        old = {"raw": "mock original", "text_sha256": "a" * 64}
        new = {"raw": "mock revised", "text_sha256": "b" * 64}
        article.remember_version = Mock(side_effect=[old, new])
        article.current = None
        article.inspect = Mock(return_value={**new, "report": {
            "editorial": {"status": "completed"}}})
        article.attempts = []
        def model(stage, provider, prompt, **kwargs):
            if stage == "draft":
                return "mock original"
            self.assertEqual(stage, "full-edit-with-audit")
            self.assertEqual(provider, "gemini")
            self.assertIn("ДОКУМЕНТЫ ДЛЯ ПРОВЕРКИ", prompt)
            self.assertIsNotNone(kwargs.get("validate"))
            return {"article": "mock revised", "issues": [], "claims": []}
        article.model = Mock(side_effect=model)
        with patch.dict(os.environ, self.pilot()):
            article.prose({"angle": "A fictional premise"})
        self.assertEqual(article.model.call_count, 2)
        self.assertEqual([x.args[0] for x in article.model.call_args_list],
                         ["draft", "full-edit-with-audit"])
        self.assertEqual(article.inspect.call_args.kwargs["submitted_audit"]["issues"], [])


if __name__ == "__main__":
    unittest.main()
