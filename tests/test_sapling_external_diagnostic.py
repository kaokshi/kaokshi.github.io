"""Offline safety and parsing tests. Does not contact Sapling."""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sapling_external_diagnostic as sapling

class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()

class SaplingDiagnosticTests(unittest.TestCase):
    def test_prepare_extracts_plain_text_and_truncates(self):
        article = {"body_html": "<h2>Тест</h2><p>" + "Слово " * 400 + "</p>"}
        text = sapling.prepare(article, max_chars=1900)
        self.assertEqual(len(text), 1900)
        self.assertNotIn("<p>", text)

    def test_missing_key_never_calls_network(self):
        def forbidden(*args, **kwargs):
            self.fail("Network should not be called")
        with self.assertRaises(ValueError):
            sapling.score_text("a" * 400, "", transport=forbidden)

    def test_sends_zero_retention_and_logs_no_text(self):
        def transport(req, timeout):
            self.assertEqual(timeout, 90)
            self.assertEqual(req.get_method(), "POST")
            body = json.loads(req.data)
            self.assertTrue(body["zero_retention"])
            self.assertFalse(body["create_report"])
            self.assertEqual(body["text"], "а" * 600)
            self.assertEqual(req.headers["Authorization"], "Bearer demo-key")
            return Response(json.dumps({
                "score": .91, "verdict": "ai", "version": "mock",
                "sentence_scores": [{"start": 0, "end": 30, "score": .8, "sentence": "secret"}],
                "text": "never store me", "tokens": ["private"]
            }).encode())
        result = sapling.score_text("а" * 600, "demo-key", transport=transport)
        saved = sapling.report({"slug": "test", "channel": "legends"}, "а" * 600, result)
        serialized = json.dumps(saved)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("never store me", serialized)
        self.assertNotIn("private", serialized)
        self.assertEqual(saved["characters_sent"], 600)
        self.assertEqual(saved["external"]["score_0_to_1"], .91)
        self.assertFalse(saved["publication_authorized"])

    def test_rejects_invalid_scores(self):
        def transport(*args, **kwargs):
            return Response(b'{"score": 101}')
        with self.assertRaises(ValueError):
            sapling.score_text("a" * 400, "key", transport=transport)

if __name__ == "__main__":
    unittest.main()
