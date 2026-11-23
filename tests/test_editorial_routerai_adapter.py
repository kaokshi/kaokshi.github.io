"""RouterAI Sonnet adapter: no real spending or network access."""
import json
import os
import unittest
from unittest.mock import patch

from src import editorial_providers as providers


class RouterAIAdapterTests(unittest.TestCase):
    def test_calls_are_denied_until_spending_guard_confirms(self):
        with patch.dict(os.environ, {"ROUTERAI_API_KEY": "private", "EDITORIAL_ROUTERAI_EDITOR_ALLOWED": "false"}), \
                patch.object(providers, "request") as network:
            with self.assertRaisesRegex(providers.ProviderError, "spend_preflight_required"):
                providers.text_call("routerai", "Редактируй", 120)
        network.assert_not_called()

    def test_claude_model_selected_and_usage_parsed(self):
        data = {"model": "anthropic/claude-sonnet-5.5", "choices": [
            {"finish_reason": "stop", "message": {"content": "Исправленный материал"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180}}
        env = {"ROUTERAI_API_KEY": "private-not-for-logs", "EDITORIAL_ROUTERAI_EDITOR_ALLOWED": "true"}
        with patch.dict(os.environ, env), \
                patch.object(providers, "request", return_value=json.dumps(data).encode()) as http:
            result = providers.text_call("routerai", "Исправь черновик", 650)
        self.assertEqual(result["text"], "Исправленный материал")
        self.assertEqual(result["model"], "anthropic/claude-sonnet-5.5")
        self.assertEqual(result["usage"]["total_tokens"], 180)
        self.assertEqual(http.call_args.args[0], "https://routerai.ru/api/v1/chat/completions")
        self.assertEqual(http.call_args.args[1], ("routerai.ru",))
        request_body = json.loads(http.call_args.kwargs["data"].decode())
        self.assertEqual(request_body["model"], "anthropic/claude-sonnet-5.5")
        self.assertEqual(request_body["max_tokens"], 650)
        self.assertNotIn("private-not-for-logs", json.dumps(result))

    def test_incomplete_response_never_counts_as_complete(self):
        data = {"choices": [
            {"finish_reason": "length", "message": {"content": "Неполный текст"}}]}
        with patch.dict(os.environ, {
            "ROUTERAI_API_KEY": "fixture", "EDITORIAL_ROUTERAI_EDITOR_ALLOWED": "true"
        }), patch.object(providers, "request", return_value=json.dumps(data).encode()):
            with self.assertRaisesRegex(providers.ProviderError, "truncated"):
                providers.text_call("routerai", "Исправь", 100)

    def test_invalid_token_budget_stops_before_network(self):
        with patch.dict(os.environ, {
            "EDITORIAL_ROUTERAI_EDITOR_ALLOWED": "true"
        }), patch.object(providers, "request") as network:
            with self.assertRaisesRegex(providers.ProviderError, "token_limit_invalid"):
                providers.text_call("routerai", "Исправь", 7000)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
