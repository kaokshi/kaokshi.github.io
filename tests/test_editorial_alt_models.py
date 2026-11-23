"""Safety regressions for isolated Kilo writer + OVH anonymous backup and OpenRouter editor.

No real text or image content; no network or provider calls in these tests.
"""
import os
import unittest
from unittest.mock import patch

from src.editorial_alt_models import require_isolated, preflight, generate
from src.editorial_providers import Blocked, ProviderError, text_call


class AlternativeProviderSafetyTests(unittest.TestCase):
    def config(self, **extra):
        data={"EDITORIAL_ALT_MODELS_TEST":"true",
              "EDITORIAL_ISOLATED_VALIDATION":"true",
              "GITHUB_ACTIONS":"true",
              "EDITORIAL_GROQ_FALLBACK_ONLY":"false",
              "GROQ_API_KEY":"",
              "OVH_AI_ENDPOINTS_ACCESS_TOKEN":""}
        return {**data, **extra}

    def test_groq_is_hard_blocked_even_when_called_directly(self):
        with patch.dict(os.environ, self.config(), clear=False):
            with self.assertRaisesRegex(ProviderError, "groq_forbidden"):
                text_call("groq", "SYNTHETIC TEST FIXTURE")

    def test_deprecated_ovh_gemini_pilot_cannot_start(self):
        from src.editorial_brain import cycle_settings
        cfg = self.config(EDITORIAL_OVH_ONE_ARTICLE="true",
                          EDITORIAL_TEST_WITHOUT_OPENROUTER="true",
                          EDITORIAL_GROQ_FREE_ONLY="true",
                          EDITORIAL_GROQ_OVH_KLEIN_PILOT="true",
                          EDITORIAL_GEMINI_EDITOR="true",
                          GEMINI_API_KEY="fixture-key")
        with patch.dict(os.environ, cfg), self.assertRaisesRegex(
                Blocked, "ovh_removed_from_project"):
            cycle_settings("retry", 120, True)
    def test_standalone_ovh_trial_is_retired_before_network_calls(self):
        from src.editorial_brain import cycle_settings
        cfg = self.config(EDITORIAL_OVH_ONE_ARTICLE="true",
                          EDITORIAL_TEST_WITHOUT_OPENROUTER="true")
        with patch.dict(os.environ, cfg), patch("src.editorial_alt_models.request") as request:
            with self.assertRaisesRegex(Blocked, "ovh_removed_from_project"):
                cycle_settings("daily", 120, True)
            request.assert_not_called()
    def test_preflight_verifies_separate_free_kilo_reviewer(self):
        import json
        from src.editorial_alt_models import KILO_ID, KILO_REVIEW, OVH_ID, OVH_GPTOSS_ID

        free = {"prompt": "0", "completion": "0"}
        catalog = {"data": [{"id": model, "pricing": free} for model in (KILO_ID, KILO_REVIEW)]}
        ovh = [{"id": model, "available": True} for model in (OVH_ID, OVH_GPTOSS_ID)]
        responses = [json.dumps(catalog).encode(), json.dumps(ovh).encode()]
        with patch.dict(os.environ, self.config(EDITORIAL_TEST_WITHOUT_OPENROUTER="true")), \
                patch("src.editorial_alt_models.request", side_effect=responses):
            self.assertEqual(preflight()["status"], "preflight_passed")

    def test_openrouter_disabled_before_any_network_call(self):
        with patch.dict(os.environ, self.config(EDITORIAL_TEST_WITHOUT_OPENROUTER="true")), \
                patch("src.editorial_providers.request") as request:
            with self.assertRaisesRegex(ProviderError, "openrouter_disabled_for_isolated_test"):
                text_call("openrouter", "fixture")
            request.assert_not_called()

    def test_standalone_not_permitted(self):
        with patch.dict(os.environ, self.config(GITHUB_ACTIONS="false"), clear=False):
            with self.assertRaisesRegex(Blocked, "isolated_validation"):
                require_isolated()

    def test_groq_key_must_not_be_present(self):
        with patch.dict(os.environ, self.config(GROQ_API_KEY="TEST-NOT-REAL"), clear=False):
            with self.assertRaisesRegex(Blocked, "groq_secret_must_be_absent"):
                require_isolated()

    def test_ovh_authentication_is_forbidden_in_free_test(self):
        with patch.dict(os.environ, self.config(OVH_AI_ENDPOINTS_ACCESS_TOKEN="TEST-NOT-REAL"), clear=False):
            with self.assertRaisesRegex(Blocked, "ovh_removed_from_project"):
                require_isolated()

    def test_bad_provider_never_routes_to_paid_model(self):
        with patch.dict(os.environ, self.config(), clear=False):
            with self.assertRaisesRegex(Blocked, "invalid_alt_provider"):
                generate("unknown", "SYNTHETIC TEST FIXTURE")

    def test_no_generation_after_preflight_unavailable(self):
        # Kilo catalog says the named free tier is unavailable: fail before
        # any actual model requests or creative content can be produced.
        fake={"data":[{"id":"paid/model","pricing":{"prompt":"1","completion":"1"}}]}
        with patch.dict(os.environ, self.config(), clear=False), \
             patch("src.editorial_alt_models.request", return_value=b'{"data":[{"id":"paid/model"}]}'):
            with self.assertRaisesRegex(Blocked, "kilo_free_model_not_in_catalog"):
                preflight()


    def test_both_ovh_models_are_hard_blocked_before_network(self):
        from src import editorial_alt_models as models
        with patch.dict(os.environ, self.config()), patch.object(models, "request") as request:
            for provider in ("ovh", "ovh-gptoss"):
                with self.subTest(provider=provider), self.assertRaisesRegex(
                        Blocked, "ovh_removed_from_project"):
                    generate(provider, "synthetic fixture")
            request.assert_not_called()
    def test_visible_content_parts_exclude_reasoning(self):
        from src.editorial_providers import visible_text

        choice = {"finish_reason": "stop", "message": {"content": [
            {"type": "reasoning", "text": "synthetic hidden text"},
            {"type": "text", "text": "SYNTHETIC FIXTURE"},
        ]}}
        self.assertEqual(visible_text(choice, {}), "SYNTHETIC FIXTURE")
        choice["finish_reason"] = "length"
        with self.assertRaisesRegex(ProviderError, "truncated_response"):
            visible_text(choice, {})

    def test_retry_after_is_bounded_for_isolated_text_requests(self):
        from urllib.error import HTTPError
        from src.editorial_providers import request

        url = "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions"
        with patch("src.editorial_providers.build_opener") as opener:
            for status, header, delay, stop in ((429, "42", 42, False),
                                                (429, "120", None, True),
                                                (503, None, 4, False)):
                with self.subTest(status=status, header=header):
                    opener.return_value.open.side_effect = HTTPError(
                        url, status, "", {"Retry-After": header}, None)
                    with self.assertRaises(ProviderError) as caught:
                        request(url, ("oai.endpoints.kepler.ai.cloud.ovh.net",), retry_transient=True)
                    self.assertEqual(caught.exception.retry_after, delay)
                    self.assertEqual(caught.exception.stop, stop)



class EditorialAlternativeRoutingTests(unittest.TestCase):
    def synthetic_article(self, channel):
        from src.editorial_brain import Article
        from types import SimpleNamespace
        article = Article.__new__(Article)
        article.channel = channel
        article.id = channel + "-01"
        article.memory = SimpleNamespace(call=lambda task, aid, provider, callback: callback(),
                                         response=lambda task: None)
        article.task = lambda stage, provider, inputs, action, **kwargs: action("synthetic-task")
        return article

    def test_kilo_failure_never_triggers_retired_ovh_fallback(self):
        cfg = {"EDITORIAL_ALT_MODELS_TEST": "true",
               "EDITORIAL_ISOLATED_VALIDATION": "true", "GITHUB_ACTIONS": "true",
               "GROQ_API_KEY": "", "EDITORIAL_GROQ_FALLBACK_ONLY": "false"}
        observed = []
        def fake_text_call(provider, prompt, max_tokens):
            observed.append(provider)
            raise ProviderError("call_budget_exhausted")
        with patch.dict(os.environ, cfg), patch("src.editorial_brain.text_call", fake_text_call):
            article = self.synthetic_article("legends")
            with self.assertRaisesRegex(ProviderError, "call_budget_exhausted"):
                article.model("draft", "groq", "synthetic test")
        self.assertEqual(observed, ["kilo"])
    def test_old_no_openrouter_audit_does_not_contact_ovh(self):
        cfg = {"EDITORIAL_ALT_MODELS_TEST": "true", "EDITORIAL_ISOLATED_VALIDATION": "true",
               "GITHUB_ACTIONS": "true", "EDITORIAL_TEST_WITHOUT_OPENROUTER": "true",
               "GROQ_API_KEY": "", "EDITORIAL_GROQ_FALLBACK_ONLY": "false"}
        with patch.dict(os.environ, cfg), patch("src.editorial_brain.text_call",
                                              side_effect=ProviderError("empty_visible_content")) as send:
            with self.assertRaisesRegex(Blocked, "empty_visible_content"):
                self.synthetic_article("science").model("editorial-audit", "groq", "fixture")
        self.assertNotIn("ovh", [call.args[0] for call in send.call_args_list])
        self.assertNotIn("ovh-gptoss", [call.args[0] for call in send.call_args_list])
    def test_ovh_catalog_rejects_unavailable_model(self):
        from src.editorial_alt_models import _ovh_available, OVH_ID
        with self.assertRaisesRegex(Blocked, "ovh_model_unavailable"):
            _ovh_available([{"id":OVH_ID, "available":False}])
        self.assertTrue(_ovh_available([{"id":OVH_ID, "available":True}]))

    def test_openrouter_third_text_edit_is_blocked_but_audit_allowed(self):
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from src.editorial_brain import Article
        from src.editorial_memory import Memory
        cfg = {"EDITORIAL_ALT_MODELS_TEST":"true",
               "EDITORIAL_ISOLATED_VALIDATION":"true", "GITHUB_ACTIONS":"true",
               "GROQ_API_KEY":"", "EDITORIAL_GROQ_FALLBACK_ONLY":"false"}
        with TemporaryDirectory() as directory, patch.dict(os.environ, cfg):
            article = Article.__new__(Article)
            article.id = "legends-01"
            article.memory = Memory(Path(directory), "2026-11-23",
                                    datetime.now(timezone.utc) + timedelta(minutes=10),
                                    {"daily_calls": {"openrouter": 10},
                                     "per_article_calls": {"openrouter": 6}})
            answer = {"text": "SYNTHETIC-FIXTURE", "model": "test", "usage": {}, "telemetry": {}}
            with patch("src.editorial_brain.text_call",
                       side_effect=[answer, ProviderError("empty_visible_content"), answer]) as call:
                self.assertEqual(article.model("full-edit", "openrouter", "fixture"), answer["text"])
                with self.assertRaisesRegex(Blocked, "empty_visible_content"):
                    article.model("repair-1", "openrouter", "fixture")
                with self.assertRaisesRegex(Blocked, "openrouter_two_edit_requests_per_article_exhausted"):
                    article.model("repair-2", "openrouter", "fixture")
                self.assertEqual(article.model("full-edit", "openrouter", "fixture"), answer["text"])
                self.assertEqual(article.model("editorial-audit", "openrouter", "fixture"), answer["text"])
                self.assertEqual(call.call_count, 3)

    def test_writers_and_editor_are_separated_without_groq(self):
        from src.editorial_brain import Article
        cfg = {"EDITORIAL_ALT_MODELS_TEST": "true",
               "EDITORIAL_ISOLATED_VALIDATION": "true",
               "GITHUB_ACTIONS": "true",
               "GROQ_API_KEY": "", "EDITORIAL_GROQ_FALLBACK_ONLY": "false"}
        observed = []
        def fake_text_call(provider, prompt, max_tokens):
            observed.append(provider)
            return {"text": "SYNTHETIC-FIXTURE-ONLY",
                    "model": "test-model", "usage": {}, "telemetry": {}}
        with patch.dict(os.environ, cfg), patch("src.editorial_brain.text_call", fake_text_call):
            mystery = self.synthetic_article("legends")
            science = self.synthetic_article("science")
            cinema = self.synthetic_article("cinema")
            self.assertEqual(mystery.model("draft", "groq", "test"),
                             "SYNTHETIC-FIXTURE-ONLY")
            self.assertEqual(science.model("draft", "groq", "test"),
                             "SYNTHETIC-FIXTURE-ONLY")
            self.assertEqual(cinema.model("draft", "groq", "test"),
                             "SYNTHETIC-FIXTURE-ONLY")
            self.assertEqual(mystery.model("editorial-audit", "groq", "test"),
                             "SYNTHETIC-FIXTURE-ONLY")
        self.assertEqual(observed, ["kilo", "kilo", "kilo", "openrouter"])
        self.assertNotIn("groq", observed)

if __name__ == "__main__":
    unittest.main()
