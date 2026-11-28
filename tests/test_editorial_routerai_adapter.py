"""Test the current RouterAI wire adapter without network calls."""
from decimal import Decimal
import io
import json
import unittest
from unittest.mock import Mock, patch

from src import editorial_routerai as router


class RouterAIAdapterTests(unittest.TestCase):
    def test_retired_models_and_unknown_paths_never_reach_network(self):
        with patch.object(router, "build_opener") as opener:
            for path, payload in (("/chat/completions", {"model": "openai/gpt-oss-120b"}),
                                  ("/images", {"model": "unlisted"}),
                                  ("/images", {"model": router.MODELS["draft"]}),
                                  ("/chat/completions", {"model": router.MODELS["image"]}),
                                  ("/images", None), ("/credits", {}),
                                  ("/not-supported", None)):
                with self.assertRaisesRegex(router.UnsafeToTest, "not_allowlisted"):
                    router.call(path, "synthetic", payload)
            opener.assert_not_called()

    def test_current_models_preserve_model_and_exact_usage(self):
        for role, model in router.MODELS.items():
            with self.subTest(role=role):
                opener = Mock()
                opener.open.return_value = io.BytesIO(b'{"usage":{"cost":0.0000208247208}}')
                path = "/images" if role == "image" else "/chat/completions"
                with patch.object(router, "build_opener", return_value=opener) as factory:
                    outcome = router.call(path, "synthetic", {"model": model}, exact_numbers=True)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, router.BASE + path)
                self.assertEqual(json.loads(request.data)["model"], model)
                self.assertEqual(outcome["usage"]["cost"], Decimal("0.0000208247208"))
                self.assertIsInstance(factory.call_args.args[0], router.NoRedirect)


    def test_generation_id_is_captured_before_body_failure(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"X-Generation-Id": "rai-fixture_123"}
        seen = []

        def failed_read(*args):
            self.assertEqual(seen, ["rai-fixture_123"])
            raise TimeoutError()

        response.read.side_effect = failed_read
        with patch.object(router, "build_opener") as factory:
            factory.return_value.open.return_value = response
            with self.assertRaisesRegex(router.UnsafeToTest, "routerai_connection_failed"):
                router.call("/chat/completions", "synthetic", {"model": router.MODELS["brain"]},
                            on_generation_id=seen.append)
        self.assertEqual(seen, ["rai-fixture_123"])

    def test_generation_id_rejects_invalid_headers(self):
        for value in (None, "", "not-provider-id", "rai-bad\nheader", "rai-" + "x" * 181):
            self.assertIsNone(router.generation_id({"X-Generation-Id": value}))
        self.assertEqual(router.generation_id({"X-Generation-Id": "rai-good-123"}), "rai-good-123")
