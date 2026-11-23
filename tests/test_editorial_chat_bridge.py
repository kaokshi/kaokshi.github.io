"""Safety and release guards for private ChatGPT-readable status diagnostics."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.editorial_chat_status import code, from_state, public_report
from src.editorial_chat_bridge import publish_status
from src.editorial_free_fallback import free_remaining, require_free_envelope
from src.editorial_brain import cycle_settings
from src.editorial_schedule import plan
from src.editorial_providers import Blocked


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
            "calls": [{"provider": "openrouter", "answer": {"text": "SENSITIVE-PROSE-NO-EXPORT"}}],
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
        self.assertEqual(report["provider_calls"]["openrouter"], 1)
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


class FreeQuotaSafetyTests(unittest.TestCase):
    def test_account_headroom_does_not_make_model_paid(self):
        def fake(url, domains, headers):
            self.assertEqual(url, "https://openrouter.ai/api/v1/key")
            return json.dumps({"data": {"free_model_daily_requests":
                                       {"remaining": 40, "limit": 50, "used": 10}}}).encode()
        self.assertEqual(free_remaining(fetch=fake), 40)
        self.assertEqual(require_free_envelope(36, reader=lambda: 40), 40)
        with self.assertRaisesRegex(Blocked, "insufficient"):
            require_free_envelope(36, reader=lambda: 30)

    def test_three_channel_budget_raised_only_in_isolated_free_test(self):
        with patch.dict(os.environ, {"EDITORIAL_ISOLATED_VALIDATION": "true",
                                     "EDITORIAL_TEST_FREE_ONLY": "true"}):
            cfg = cycle_settings("daily", 90, True, True)
        self.assertEqual(cfg["articles_per_channel"]["cinema"], 1)
        self.assertEqual(cfg["daily_calls"]["openrouter"], 36)
        self.assertEqual(cfg["per_article_calls"]["openrouter"], 12)
        regular = cycle_settings("daily")
        self.assertEqual(regular["articles_per_channel"]["cinema"], 0)
        self.assertEqual(regular["daily_calls"]["openrouter"], 10)

    def test_isolated_main_does_not_activate_production(self):
        cfg = cycle_settings("daily")
        self.assertFalse(cfg["schedule"]["enabled"])
        result = plan(cfg, command="daily", branch="refs/heads/main",
                      isolated=True, test_free=True)
        self.assertEqual(result["command"], "daily")
        self.assertEqual(result["public_key"], cfg["schedule"]["owner_public_key"])
        self.assertEqual(result["repository"], "")
        self.assertEqual(plan(cfg, command="daily", branch="refs/heads/main")["command"],
                         "skip")


if __name__ == "__main__":
    unittest.main()
