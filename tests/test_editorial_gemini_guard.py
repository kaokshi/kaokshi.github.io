"""Gemini free-editor and read-only metrics regression tests. No network calls."""
from datetime import datetime, timezone
from unittest.mock import patch
import json
import unittest

from src import editorial_gemini_guard as guard, editorial_providers as providers
from src.editorial_gemini_cloud_usage import metric_snapshot, pacific_day_start


class GeminiEditorGuardTests(unittest.TestCase):
    def config(self, **update):
        settings = {
            "automated_generation_enabled": True,
            "model": guard.MODEL,
            "exclusive_free_project_confirmed": True,
            "verified_free_daily_requests": 100,
            "verified_free_rpm": 2,
            "verified_free_tpm": 50000,
            "limits_confirmed_until": "2026-12-20",
            "max_calls_per_cycle": 4,
            "reserve_daily_requests": 5,
        }
        settings.update(update)
        return {"gemini_quota_guard": settings}

    def test_disabled_by_default(self):
        self.assertEqual(guard.safe_status(self.config(automated_generation_enabled=False),
                                          free_only=True, model_available=True),
                         "gemini_editor_not_activated")

    def test_missing_owner_project_attestation_stops(self):
        self.assertEqual(guard.safe_status(self.config(), free_only=False, model_available=True),
                         "gemini_free_project_not_confirmed")
        self.assertEqual(guard.safe_status(
            self.config(exclusive_free_project_confirmed=False), free_only=True,
            model_available=True), "gemini_free_project_not_confirmed")

    def test_unverified_rpd_stops(self):
        self.assertEqual(guard.safe_status(
            self.config(verified_free_daily_requests=None), free_only=True,
            model_available=True), "gemini_limits_not_verified")
        self.assertEqual(guard.safe_status(
            self.config(verified_free_daily_requests=4), free_only=True,
            model_available=True), "gemini_verified_rpd_insufficient")

    def test_verified_limits_and_available_model_ready(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        self.assertEqual(guard.safe_status(
            self.config(), free_only=True, model_available=True, now=now), "ready")
        self.assertEqual(guard.safe_status(
            self.config(), free_only=True, model_available=False, now=now),
            "gemini_model_availability_unverified")

    def test_expired_limits_fail_closed(self):
        self.assertEqual(guard.safe_status(
            self.config(limits_confirmed_until="2026-10-01"), free_only=True,
            model_available=True, now=datetime(2026, 11, 24, tzinfo=timezone.utc)),
            "gemini_limits_verification_expired")

    def test_pacific_reset_not_utc(self):
        before = datetime(2026, 11, 24, 7, 59, tzinfo=timezone.utc)
        after = datetime(2026, 11, 24, 8, 1, tzinfo=timezone.utc)
        self.assertEqual(guard.quota_day(before), "2026-11-23")
        self.assertEqual(guard.quota_day(after), "2026-11-24")

    def test_local_rpm_and_tpm_reject_overruns(self):
        guard._minute_events.clear()
        c = self.config()["gemini_quota_guard"]
        self.assertEqual(guard.ensure_minute_budget(c, "test", 100, clock=lambda: 100), "ready")
        self.assertEqual(guard.ensure_minute_budget(c, "test", 100, clock=lambda: 100), "ready")
        self.assertEqual(guard.ensure_minute_budget(c, "test", 100, clock=lambda: 100),
                         "gemini_local_rpm_headroom_exhausted")
        self.assertEqual(guard.ensure_minute_budget(c, "test", 100, clock=lambda: 161), "ready")
        guard._minute_events.clear()
        c["verified_free_tpm"] = 200
        self.assertEqual(guard.ensure_minute_budget(c, "test", 100, clock=lambda: 200),
                         "gemini_local_tpm_headroom_exhausted")
        guard._minute_events.clear()


class GeminiTextAdapterTests(unittest.TestCase):
    def test_never_generates_without_verified_free_quota(self):
        with patch.object(providers, "request") as network:
            with patch.dict("os.environ", {"EDITORIAL_GEMINI_EDITOR_ALLOWED": "false"}):
                with self.assertRaisesRegex(providers.ProviderError, "preflight"):
                    providers.text_call("gemini", "Sample", 100)
            network.assert_not_called()

    def test_extract_visible_response_and_usage(self):
        response = {"candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "Исправленная"}, {"text": " статья"}]},
        }], "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 8, "totalTokenCount": 38}}
        with patch.dict("os.environ", {
            "EDITORIAL_GEMINI_EDITOR_ALLOWED": "true",
            "EDITORIAL_GEMINI_FREE_ONLY": "true",
            "GEMINI_API_KEY": "fake-never-print",
        }), patch.object(providers, "request", return_value=json.dumps(response).encode()) as api:
            reply = providers.text_call("gemini", "Отредактируй этот текст", 450)
        self.assertEqual(reply["text"], "Исправленная статья")
        self.assertEqual(reply["model"], guard.MODEL)
        self.assertEqual(reply["usage"]["total_tokens"], 38)
        url = api.call_args.args[0]
        self.assertEqual(url, f"https://generativelanguage.googleapis.com/v1beta/models/{guard.MODEL}:generateContent")
        payload = json.loads(api.call_args.kwargs["data"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("grounding", payload)

    def test_truncation_is_a_hard_failure(self):
        data = json.dumps({"candidates": [{
            "finishReason": "MAX_TOKENS",
            "content": {"parts": [{"text": "Incomplete"}]},
        }]}).encode()
        with patch.dict("os.environ", {
            "EDITORIAL_GEMINI_EDITOR_ALLOWED": "true",
            "EDITORIAL_GEMINI_FREE_ONLY": "true",
            "GEMINI_API_KEY": "fake-key",
        }), patch.object(providers, "request", return_value=data):
            with self.assertRaisesRegex(providers.ProviderError, "truncated"):
                providers.text_call("gemini", "Full edit", 150)


class CloudMonitoringTests(unittest.TestCase):
    def test_missing_identity_does_not_invent_balance(self):
        reply = metric_snapshot("", "", guard.MODEL)
        self.assertEqual(reply["status"], "not_configured")
        self.assertIsNone(reply["remaining_rpd"])
        self.assertFalse(reply["authoritative_balance"])

    def test_metrics_start_at_pacific_midnight(self):
        t = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(pacific_day_start(t),
                         datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
