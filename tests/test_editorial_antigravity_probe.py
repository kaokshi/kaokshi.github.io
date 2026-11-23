"""No-network tests for isolated Antigravity prompt and billing safeguards."""
import json
import unittest
from unittest.mock import patch

from src import editorial_antigravity_probe as probe


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, *_):
        return self.payload


class AntigravitySafetyTests(unittest.TestCase):
    def test_payload_is_one_bounded_free_candidate_without_tools(self):
        p = probe.payload()
        self.assertEqual(p["agent"], "antigravity-preview-09-2026")
        self.assertEqual(p["agent_config"]["model"], "gemini-3.6-flash")
        self.assertLessEqual(p["agent_config"]["max_total_tokens"], 2400)
        self.assertEqual(p["tools"], [])
        self.assertNotIn("environment", p)
        self.assertNotIn("background", p)
        self.assertNotIn("triggers", p)

    def test_no_post_without_explicit_unbilled_free_confirmation(self):
        with patch.object(probe, "urlopen") as remote:
            with self.assertRaisesRegex(probe.ProbeError, "unbilled_free_project_not_confirmed"):
                probe.one_test("fixture-secret", free_project_confirmed=False)
        remote.assert_not_called()

    def test_read_only_catalog_with_a_valid_model(self):
        calls = []
        def fake(req, timeout):
            calls.append(req)
            return FakeResponse({
                "name": "models/gemini-3.6-flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            })
        report = probe.model_catalog("fixture-secret", opener=fake)
        self.assertTrue(report["model_available"])
        self.assertTrue(report["generate_content_supported"])
        self.assertFalse(report["agent_request_sent"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_method(), "GET")
        self.assertNotIn("fixture-secret", json.dumps(report))

    def test_isolated_single_post_keeps_outputs_synthetic(self):
        calls = []
        def fake(req, timeout):
            calls.append(req)
            return FakeResponse({
                "status": "completed",
                "output_text": "Противоречие во времени работы и количестве ламп.",
                "usage": {"total_tokens": 980},
            })
        report = probe.one_test("fixture-secret", free_project_confirmed=True, opener=fake)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["agent_request_sent"])
        self.assertEqual(report["reported_total_tokens"], 980)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_method(), "POST")
        body = json.loads(calls[0].data)
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["agent_config"]["max_total_tokens"], 2400)
        self.assertNotIn("fixture-secret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
