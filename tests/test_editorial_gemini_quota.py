"""Gemini read-only capability checks must never invent quota balances."""
import unittest
from unittest.mock import patch
from src.editorial_gemini_quota import GeminiQuotaError, inspect_models, read_gemini


class GeminiQuotaTests(unittest.TestCase):
    sample = {"models": [
        {"name": "models/gemini-safe-fixture", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-only-fixture", "supportedGenerationMethods": ["embedContent"]}
    ]}

    def test_models_are_not_interpreted_as_remaining_quota(self):
        report = inspect_models(self.sample, "gemini-safe-fixture")
        self.assertEqual(report["status"], "models_accessible")
        self.assertTrue(report["configured_model_available"])
        self.assertIsNone(report["remaining_requests"])
        self.assertFalse(report["live_quota_available"])

    def test_unsupported_model_cannot_be_marked_ready(self):
        report = inspect_models(self.sample, "model-not-present")
        self.assertEqual(report["status"], "model_not_available")
        self.assertFalse(report["configured_model_available"])

    def test_empty_or_invalid_result_is_unavailable(self):
        for reply in [{}, [], {"models": []}, {"models": [{"name": "models/x"}]}]:
            with self.subTest(reply=reply), self.assertRaises(GeminiQuotaError):
                inspect_models(reply)

    def test_missing_key_makes_no_request(self):
        with patch("src.editorial_gemini_quota.urlopen") as fetch:
            with self.assertRaisesRegex(GeminiQuotaError, "missing_gemini_api_key"):
                read_gemini("", fetch=fetch)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
