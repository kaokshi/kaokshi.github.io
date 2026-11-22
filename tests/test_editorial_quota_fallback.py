"""Quota-only Groq failover must preserve attribution and never bypass free-model checks."""
import unittest
from unittest.mock import patch

from src import editorial_brain as brain
from src.editorial_providers import Blocked, ProviderError


class MemoryStub:
    def __init__(self, groq_error):
        self.groq_error = groq_error
        self.calls = []

    def response(self, task):
        return None

    def time_left(self):
        return 3600

    def call_groq(self, task, article, stage, prompt, ceiling, callback, *, repair=False):
        self.calls.append(("groq", task))
        raise self.groq_error

    def call(self, task, article, provider, callback):
        self.calls.append((provider, task))
        return callback()


class QuotaFallbackTests(unittest.TestCase):
    def article(self, error):
        value = object.__new__(brain.Article)
        value.id = "science-01"
        value.memory = MemoryStub(error)
        value.task = lambda stage, provider, inputs, action, **kwargs: action(
            stage + ":" + provider)
        return value

    def test_daily_quota_falls_back_without_reusing_groq_task(self):
        article = self.article(ProviderError("http_429_groq_daily_quota", stop=True))
        with patch.object(brain, "text_call", return_value={
                "text": "Готовый проверяемый черновик",
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "usage": {}}) as call:
            text = article.model("draft", "groq", "Напиши статью")
        self.assertEqual(text, "Готовый проверяемый черновик")
        self.assertEqual(article.memory.calls, [("groq", "draft:groq"),
                                                 ("openrouter", "draft:openrouter")])
        self.assertEqual(call.call_args.args[0], "openrouter")
        self.assertGreaterEqual(call.call_args.args[2], 7800)

    def test_quota_fallback_preserves_json_validation(self):
        article = self.article(ProviderError("call_budget_exhausted", stop=True))
        with patch.object(brain, "text_call", return_value={
                "text": '{"topics":[]}', "model": "free-model", "usage": {}}):
            result = article.model("topics-1", "groq", "Выбери темы",
                                   validate=lambda data: self.assertIn("topics", data))
        self.assertEqual(result, {"topics": []})

    def test_authentication_error_never_falls_back(self):
        article = self.article(ProviderError("http_401_authorization", stop=True))
        with patch.object(brain, "text_call") as call:
            with self.assertRaisesRegex(Blocked, "http_401_authorization"):
                article.model("draft", "groq", "Напиши статью")
        self.assertEqual(article.memory.calls, [("groq", "draft:groq")])
        call.assert_not_called()

    def test_audit_keeps_its_existing_fallback_policy(self):
        article = self.article(ProviderError("http_429_groq_daily_quota", stop=True))
        with self.assertRaisesRegex(Blocked, "http_429_groq_daily_quota"):
            article.model("editorial-audit", "groq", "Проверь статью")
        self.assertEqual(article.memory.calls, [("groq", "editorial-audit:groq")])


if __name__ == "__main__":
    unittest.main()
