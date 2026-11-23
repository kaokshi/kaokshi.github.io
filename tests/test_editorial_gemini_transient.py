"""Offline guards: Gemini 503/timeout fallback is bounded and metered."""
import json
import os
import unittest
from unittest.mock import patch

from src.editorial_providers import ProviderError, gemini_text


def environment():
    return {
        "EDITORIAL_ALT_MODELS_TEST": "true",
        "EDITORIAL_GROQ_GEMINI_KLEIN_PILOT": "true",
        "EDITORIAL_ISOLATED_VALIDATION": "true",
        "EDITORIAL_GROQ_FREE_ONLY": "true",
        "EDITORIAL_GROQ_FALLBACK_ONLY": "false",
        "EDITORIAL_OVH_ONE_ARTICLE": "false",
        "EDITORIAL_GROQ_OVH_KLEIN_PILOT": "false",
        "GITHUB_ACTIONS": "true",
        "GROQ_API_KEY": "fixture",
        "GEMINI_API_KEY": "fixture",
    }


def response():
    return b'data: ' + json.dumps({
        "modelVersion": "gemini-3.6-flash",
        "candidates": [{"finishReason": "STOP",
                        "content": {"parts": [{"text": "Проверенный текст"}]}}],
        "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 5,
                          "totalTokenCount": 20},
    }).encode() + b'\n\n'


class GeminiTransientTests(unittest.TestCase):
    def test_requested_model_is_initially_35_and_fallback_is_explicit_36(self):
        urls = []
        def fake_request(url, domains, **kwargs):
            urls.append(url)
            return response()
        with patch.dict(os.environ, environment()), \
             patch("src.editorial_providers.request", side_effect=fake_request), \
             patch("src.editorial_providers._gemini_last_request", float("-inf")):
            first = gemini_text("fixture", 512)
            fallback = gemini_text("fixture", 512, fallback_model=True)
        self.assertEqual(first["model"], "gemini-3.6-flash")
        self.assertEqual(fallback["model"], "gemini-3.6-flash")
        self.assertIn("/gemini-3.5-flash:", urls[0])
        self.assertIn("/gemini-3.6-flash:", urls[1])

    def test_transient_503_is_not_account_quota_exhaustion(self):
        class Fake503(ProviderError):
            pass
        with patch.dict(os.environ, environment()), \
             patch("src.editorial_providers.request",
                   side_effect=ProviderError("http_503_transient", retry_after=4)):
            with self.assertRaisesRegex(ProviderError, "http_503_transient") as err:
                gemini_text("fixture", 512)
        self.assertFalse(err.exception.stop)


if __name__ == "__main__":
    unittest.main()
