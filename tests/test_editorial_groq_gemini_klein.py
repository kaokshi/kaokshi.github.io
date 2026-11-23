"""No-network regression checks for the owner-selected experimental provider route."""
import os
import unittest
from unittest.mock import patch

from src.editorial_providers import ProviderError, gemini_model, text_call
from src.editorial_alt_models import generate, preflight, require_isolated
from src.editorial_brain import cycle_settings


class PilotRouteTests(unittest.TestCase):
    def env(self):
        return {
            "EDITORIAL_ALT_MODELS_TEST": "true",
            "EDITORIAL_ISOLATED_VALIDATION": "true",
            "EDITORIAL_RELEASE_TRIO": "false",
            "EDITORIAL_GROQ_GEMINI_KLEIN_PILOT": "true",
            "EDITORIAL_GROQ_FREE_ONLY": "true",
            "EDITORIAL_GROQ_FALLBACK_ONLY": "false",
            "EDITORIAL_OVH_ONE_ARTICLE": "false",
            "EDITORIAL_GROQ_OVH_KLEIN_PILOT": "false",
            "EDITORIAL_TEST_WITHOUT_OPENROUTER": "false",
            "EDITORIAL_TEST_FREE_ONLY": "true",
            "GITHUB_ACTIONS": "true",
            "GROQ_API_KEY": "fixture-only",
            "OVH_API_KEY": "",
            "OVH_AI_ENDPOINTS_ACCESS_TOKEN": "",
        }

    def test_target_gemini_identifier_is_explicit(self):
        with patch.dict(os.environ, self.env()):
            self.assertEqual(gemini_model(), "gemini-3.5-flash")

    def test_request_budgets_for_two_channels(self):
        with patch.dict(os.environ, self.env()):
            self.assertIsNone(require_isolated())
            cfg = cycle_settings("daily", 120, True, validate_cinema=True)
        self.assertEqual(cfg["articles_per_channel"], {"legends": 1, "cinema": 1})
        for p in ("ovh", "ovh-gptoss", "openrouter", "kilo"):
            self.assertEqual(cfg["daily_calls"][p], 0)
        self.assertEqual(cfg["daily_calls"]["groq"], 6)
        self.assertEqual(cfg["daily_calls"]["gemini"], 4)
        self.assertEqual(cfg["per_article_calls"]["groq"], 3)
        self.assertEqual(cfg["per_article_calls"]["gemini"], 2)
        self.assertEqual(cfg["daily_calls"]["klein"], 16)

    def test_release_trio_keeps_daily_budget_bounded(self):
        with patch.dict(os.environ, {**self.env(), "EDITORIAL_RELEASE_TRIO": "true"}):
            cfg = cycle_settings("daily", 120, True, validate_cinema=True)
        self.assertEqual(cfg["articles_per_channel"], {"legends": 1, "science": 1, "cinema": 1})
        self.assertEqual(cfg["daily_calls"]["klein"], 18)
        self.assertEqual(cfg["daily_calls"]["gemini"], 6)

    def test_ovh_route_is_blocked_before_any_network_access(self):
        with patch.dict(os.environ, self.env()), \
             patch("src.editorial_alt_models.request") as req:
            with self.assertRaisesRegex(ProviderError, "ovh_removed_from_project"):
                text_call("ovh", "test")
            with self.assertRaisesRegex(ProviderError, "ovh_removed_from_project"):
                text_call("ovh-gptoss", "test")
            with self.assertRaisesRegex(Exception, "ovh_removed_from_project"):
                generate("ovh", "test")
            req.assert_not_called()

    def test_preflight_checks_gemini_not_old_ovh_catalog(self):
        with patch.dict(os.environ, self.env()), \
             patch("src.editorial_providers.gemini_preflight",
                   return_value={"status": "preflight_passed", "gemini_model": "gemini-3.5-flash"}) as check, \
             patch("src.editorial_alt_models.request") as request:
            result = preflight()
        self.assertEqual(result["gemini_model"], "gemini-3.5-flash")
        check.assert_called_once_with()
        request.assert_not_called()

    def test_gemini_503_enforces_cooldown_without_silent_retry(self):
        from time import monotonic
        import src.editorial_providers as providers
        old = providers._gemini_last_request
        now = monotonic()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "offline-fixture"}), \
             patch("src.editorial_providers.request",
                   side_effect=ProviderError("http_503_transient")) as request:
            try:
                with self.assertRaisesRegex(ProviderError, "http_503_transient"):
                    providers.gemini_packet("https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
                                            {"contents": []})
                self.assertEqual(request.call_count, 1)
                self.assertGreaterEqual(providers._gemini_last_request, now + 30)
            finally:
                providers._gemini_last_request = old

    def test_groq_requires_verified_free_account(self):
        env = self.env()
        env["EDITORIAL_GROQ_FREE_ONLY"] = "false"
        with patch.dict(os.environ, env), self.assertRaisesRegex(
                Exception, "groq_free_account_unconfirmed"):
            require_isolated()


if __name__ == "__main__":
    unittest.main()
