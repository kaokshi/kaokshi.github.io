"""Groq quota failures must never reroute to a retired secondary text service."""
import unittest
from unittest.mock import patch

from src import editorial_brain as brain
from src.editorial_providers import ProviderError


class MemoryStub:
    def __init__(self, failure):
        self.failure = failure
        self.calls = []

    def response(self, task):
        return None

    def time_left(self):
        return 3600

    def call_groq(self, task, article, stage, prompt, ceiling, callback, *, repair=False):
        self.calls.append(("groq", task))
        raise self.failure

    def call(self, task, article, provider, callback):
        raise AssertionError("Secondary provider must not be called")


class QuotaFailClosedTests(unittest.TestCase):
    def article(self, error):
        article = object.__new__(brain.Article)
        article.id = "science-01"
        article.memory = MemoryStub(error)
        article.task = lambda stage, provider, inputs, action, **kwargs: action(stage + ":" + provider)
        return article

    def test_groq_daily_quota_stops_without_fallback(self):
        article = self.article(ProviderError("http_429_groq_daily_quota", stop=True))
        with patch.object(brain, "text_call") as call:
            with self.assertRaisesRegex(ProviderError, "http_429_groq_daily_quota"):
                article.model("draft", "groq", "Напиши статью")
        self.assertEqual(article.memory.calls, [("groq", "draft:groq")])
        call.assert_not_called()

    def test_call_budget_stops_without_fallback(self):
        article = self.article(ProviderError("call_budget_exhausted", stop=True))
        with self.assertRaisesRegex(ProviderError, "call_budget_exhausted"):
            article.model("topics-1", "groq", "Предложи темы")

    def test_authentication_stops_without_fallback(self):
        article = self.article(ProviderError("http_401_authorization", stop=True))
        with self.assertRaisesRegex(ProviderError, "http_401_authorization"):
            article.model("draft", "groq", "Напиши")

    def test_audit_stops_without_fallback(self):
        article = self.article(ProviderError("http_429_groq_daily_quota", stop=True))
        with self.assertRaisesRegex(ProviderError, "http_429_groq_daily_quota"):
            article.model("editorial-audit", "groq", "Проверь")


if __name__ == "__main__":
    unittest.main()
