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
            with self.assertRaisesRegex(Blocked, "ovh_anonymous_test_must_not_use_paid_api_key"):
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

    def test_kilo_failure_routes_to_ovh_and_never_groq_or_openrouter_writer(self):
        from src.editorial_providers import ProviderError
        cfg = {"EDITORIAL_ALT_MODELS_TEST":"true",
               "EDITORIAL_ISOLATED_VALIDATION":"true", "GITHUB_ACTIONS":"true",
               "GROQ_API_KEY":"", "EDITORIAL_GROQ_FALLBACK_ONLY":"false"}
        observed = []
        def fake_text_call(provider, prompt, max_tokens):
            observed.append(provider)
            if provider == "kilo":
                raise ProviderError("call_budget_exhausted")
            return {"text":"SYNTHETIC-FIXTURE-ONLY", "model":"synthetic",
                    "usage":{}, "telemetry":{}}
        with patch.dict(os.environ, cfg), patch("src.editorial_brain.text_call", fake_text_call):
            article = self.synthetic_article("legends")
            self.assertEqual(article.model("draft", "groq", "synthetic test"),
                             "SYNTHETIC-FIXTURE-ONLY")
        self.assertEqual(observed, ["kilo", "ovh"])

    def test_ovh_catalog_rejects_unavailable_model(self):
        from src.editorial_alt_models import _ovh_available, OVH_ID
        with self.assertRaisesRegex(Blocked, "ovh_model_unavailable"):
            _ovh_available([{"id":OVH_ID, "available":False}])
        self.assertTrue(_ovh_available([{"id":OVH_ID, "available":True}]))

    def test_openrouter_third_text_edit_is_blocked_but_audit_allowed(self):
        from threading import RLock
        from types import SimpleNamespace
        from src.editorial_brain import Article
        cfg = {"EDITORIAL_ALT_MODELS_TEST":"true",
               "EDITORIAL_ISOLATED_VALIDATION":"true", "GITHUB_ACTIONS":"true",
               "GROQ_API_KEY":"", "EDITORIAL_GROQ_FALLBACK_ONLY":"false"}
        article = self.synthetic_article("legends")
        article.memory.lock = RLock()
        article.memory.data = {
            "tasks": {
                "full-id":{"operation":"full-edit"},
                "repair-id":{"operation":"repair-1"}},
            "calls": [
                {"article":"legends-01","provider":"openrouter",
                 "task":"full-id","status":"responded"},
                {"article":"legends-01","provider":"openrouter",
                 "task":"repair-id","status":"failed"}]}
        with patch.dict(os.environ, cfg):
            with self.assertRaisesRegex(Blocked, "openrouter_two_edit_requests_per_article_exhausted"):
                article.model("repair-2", "openrouter", "SYNTHETIC-FIXTURE")
            with self.assertRaisesRegex(Blocked, "openrouter_two_edit_requests_per_article_exhausted"):
                article.model("full-edit-retry", "openrouter", "SYNTHETIC-FIXTURE")
            with patch("src.editorial_brain.text_call", return_value={
                "text":"SYNTHETIC-FIXTURE","model":"test","usage":{},"telemetry":{}}):
                result=article.model("editorial-audit", "openrouter", "SYNTHETIC-FIXTURE")
                self.assertEqual(result,"SYNTHETIC-FIXTURE")

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
