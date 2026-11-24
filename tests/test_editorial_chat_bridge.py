"""Safety and release guards for private ChatGPT-readable status diagnostics."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.editorial_chat_status import code, from_state, public_report
from src.editorial_chat_bridge import publish_status


class ChatStatusTests(unittest.TestCase):
    def state(self):
        return {
            "articles": {
                "science-01": {"status": "partial_review_pending",
                               "title": "SENSITIVE-PROSE-NO-EXPORT",
                               "blockers": ["editorial_checks_incomplete",
                                            "independent_edit_unavailable: SECRET"],
                               "sources": ["https://secret.example/path"]},
                "legends-01": {"status": "blocked", "blockers": ["unique_topic_or_verifiable_sources_missing"]},
                "cinema-01": {"status": "blocked", "blockers": ["film_independent_source_unavailable"]},
            },
            "tasks": {
                "one": {"article": "science-01", "status": "blocked",
                        "operation": "editorial-audit", "error": "http_429_quota_or_rate_limit",
                        "inputs": {"prompt": "SENSITIVE-PROSE-NO-EXPORT"}},
                "two": {"article": "cinema-01", "status": "blocked",
                        "operation": "film-evidence", "error": "PRIVATE-URL-WITH-TEXT"},
            },
            "calls": [{"provider": "groq", "answer": {"text": "SENSITIVE-PROSE-NO-EXPORT"}}],
            "private_key": "SENSITIVE-KEY-NO-EXPORT",
        }

    def test_public_report_has_only_allowed_metadata(self):
        report = public_report(self.state(), "2026-11-23")
        text = json.dumps(report)
        for sensitive in ("SENSITIVE", "secret.example", "PRIVATE", "SECRET"):
            self.assertNotIn(sensitive, text)
        self.assertEqual(report["articles"]["science-01"]["failed_tasks"][0]["stage"],
                         "editorial-audit")
        self.assertEqual(report["articles"]["cinema-01"]["failed_tasks"][0]["code"],
                         "unclassified_block")
        self.assertEqual(report["provider_calls"]["groq"], 1)
        self.assertEqual(report["ready_count"], 0)
        self.assertFalse(report["contents_included"])

    def test_untrusted_error_text_is_redacted(self):
        self.assertEqual(code("film_independent_source_unavailable"),
                         "film_independent_source_unavailable")
        self.assertEqual(code("Film source API unavailable: TimeoutError"),
                         "film_source_api_unavailable")
        self.assertEqual(code("PRIVATE-TEXT/secret"), "unclassified_block")

    def test_reads_only_private_state_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-11-23"
            path.mkdir()
            (path / "state.json").write_text(json.dumps(self.state()), encoding="utf-8")
            result = from_state(tmp, "2026-11-23")
            self.assertEqual(result["day"], "2026-11-23")

    def test_posts_only_to_verified_private_issue(self):
        calls = []
        def fake(token, path, method="GET", data=None):
            calls.append((path, method, data))
            if path.endswith("/issues/1/comments"):
                return {"html_url": "https://github.com/kaokshi/zen-editorial-review/issues/1#issuecomment-123"}
            if path.endswith("/issues/1"):
                return {"number": 1, "state": "open"}
            return {"full_name": "kaokshi/zen-editorial-review", "visibility": "private"}
        report = public_report(self.state(), "2026-11-23")
        result = publish_status(report, "local-fake-token", run_id="45126126260", call=fake)
        self.assertEqual(result["status"], "private_status_sent")
        sent = json.dumps(calls)
        self.assertNotIn("SENSITIVE", sent)
        self.assertNotIn("local-fake-token", calls[-1][2]["body"])
        self.assertEqual(calls[-1][1], "POST")

    def test_rejects_public_repo(self):
        def fake(token, path, method="GET", data=None):
            return {"full_name": "kaokshi/zen-editorial-review", "visibility": "public"}
        report = public_report(self.state(), "2026-11-23")
        with self.assertRaisesRegex(RuntimeError, "visibility"):
            publish_status(report, "local-fake-token", run_id="45126126260", call=fake)


class AutonomousRetryTests(unittest.TestCase):
    def test_transient_film_source_api_retry_keeps_same_source(self):
        from io import BytesIO
        from unittest.mock import MagicMock
        from src.movie_autonomous_research import fetch_json

        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"ok"}'
        calls = []
        def mock_open(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise OSError("temporary offline")
            return response
        with patch("src.movie_autonomous_research.urlopen", side_effect=mock_open), \
             patch("time.sleep", return_value=None):
            result = fetch_json("https://www.wikidata.org/w/api.php",
                                {"action": "query", "format": "json"})
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

if __name__ == "__main__":
    unittest.main()
