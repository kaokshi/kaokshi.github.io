"""Exercise accounting and restart behavior with no requests to Groq."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import sleep
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src.editorial_brain import settings
from src.editorial_groq_budget import assessment, estimate_tokens, forecast, rate_headers, snapshot
from src.editorial_groq_ledger import archive_document, collection
from src.editorial_memory import Memory
from src.editorial_providers import Blocked, ProviderError, groq_throttle, text_call

NOW = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)


class GroqBudgetTests(unittest.TestCase):
    def setUp(self):
        self.config = settings()
        self.policy = self.config["groq_budget"]

    def row(self, tokens, *, lane="production", repair=False, elapsed=90, **extra):
        return {"at": (NOW - timedelta(seconds=elapsed)).isoformat(), "status": "responded",
                "lane": lane, "repair": repair, "total_tokens": tokens, **extra}

    def test_headers_separate_daily_requests_from_minute_tokens(self):
        raw = {"x-ratelimit-limit-requests": "51", "x-ratelimit-remaining-requests": "12",
               "x-ratelimit-reset-requests": "2h3m14.3s", "x-ratelimit-limit-tokens": "12000",
               "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "3.2s",
               "retry-after": "Sun, 22 Nov 2026 10:00:05 GMT", "authorization": "secret"}
        headers = rate_headers(raw, NOW)
        self.assertEqual(len(headers), 7)
        self.assertEqual(headers["retry-after"], "2026-11-22T10:00:05+00:00")
        rows = [self.row(720, rate_limits=headers)]
        error, delay = assessment(rows, self.policy, "production", NOW, (1100, 900))
        self.assertIsNone(error)
        self.assertAlmostEqual(delay, 3.3)
        self.assertEqual(snapshot(rows, NOW)["known_tokens"], 720)

    def test_minute_scheduler_guards_rpm_itpm_and_otpm(self):
        self.policy["organization_limits"] = {"rpm": 1, "itpm": 3000, "otpm": 1900}
        rows = [self.row(2000, elapsed=45, input_tokens=1200, output_tokens=800)]
        self.assertGreater(assessment(rows, self.policy, "production", NOW, (1700, 1150))[1], 14)
        self.assertEqual(assessment(rows, self.policy, "production", NOW, (800, 1901))[0],
                         "groq_request_exceeds_window")

    def test_daily_organization_and_internal_repair_reserve(self):
        rows = [self.row(34500)]
        self.assertEqual(assessment(rows, self.policy, "production", NOW, (400, 300))[0],
                         "groq_editorial_budget_exhausted")
        self.assertEqual(assessment(rows, self.policy, "production", NOW, (400, 300), reserve=True),
                         (None, 0))
        self.assertEqual(assessment(rows, self.policy, "experiment", NOW, (400, 300)), (None, 0))
        self.policy["organization_limits"] = {"tpd": 34600, "rpd": 500}
        self.assertEqual(assessment(rows, self.policy, "production", NOW, (400, 300), reserve=True)[0],
                         "groq_daily_budget_exhausted")
        self.policy["organization_limits"]["tpd"] = 160000
        rows += [self.row(15400, repair=True)]
        self.assertEqual(assessment(rows, self.policy, "production", NOW, (400, 300), reserve=True)[0],
                         "groq_editorial_budget_exhausted")

    def test_429_classification_and_retry_after(self):
        def error(detail, **values):
            headers = Message()
            for k, v in values.items():
                headers[k.replace("_", "-")] = v
            payload = ('{"error":{"message":"' + detail + '"}}').encode()
            return HTTPError("https://api.groq.com/test", 429, "limited", headers, BytesIO(payload))

        minute = groq_throttle(error("Tokens per minute (TPM): Limit 9999", retry_after="7"), NOW)
        self.assertAlmostEqual(minute.retry_after, 7.1)
        self.assertEqual(minute.telemetry["reported_limits"], {"tpm": 9999})
        self.assertFalse(minute.stop)
        day = groq_throttle(error("tokens per day (TPD): Limit 12,000", retry_after="3600"), NOW)
        self.assertTrue(day.stop)
        self.assertEqual(day.telemetry["resets_at"], "2026-11-22T11:00:00+00:00")
        unknown = groq_throttle(error("provider rate limit", x_ratelimit_remaining_tokens="0"), NOW)
        self.assertEqual(str(unknown), "rate_limit_type_unknown")
        self.assertTrue(unknown.stop)

    def test_accounting_and_prediction_with_partial_telemetry(self):
        rows = [self.row(None, estimated_total_tokens=3100, operation="draft"),
                self.row(2800, operation="draft", input_tokens=1400, output_tokens=1400, prompt_bytes=3000)]
        result = snapshot(rows, NOW)
        self.assertEqual((result["accounted_tokens"], result["known_tokens"], result["unknown_token_requests"]),
                         (5900, 2800, 1))
        self.assertFalse(result["organization_total_exact"])
        self.assertGreater(estimate_tokens("a" * 2690, 1000, rows, "draft")[0], 1400)
        report = forecast(rows, self.policy, "production", NOW, 5410, 2, {"draft": 2, "repair": 1})
        self.assertEqual(report["estimated_base_tokens"], 5600)
        self.assertEqual(report["operations"]["repair"]["basis"], "estimate")

    def test_cross_runner_reservation_settlement_and_no_private_data(self):
        base = {"request_id": "a" * 32, "at": NOW.isoformat(), "status": "run_reservation",
                "lane": "production", "estimated_total_tokens": 50000, "estimated_requests": 34}
        measured = {"request_id": "b" * 32, "at": NOW.isoformat(), "status": "responded", "lane": "production",
                    "total_tokens": 955, "article": "private-article", "task": "private-task", "answer": "private"}
        reservations = archive_document("reservation", "12.1", [base])
        final = archive_document("usage", "12.1", [measured])
        self.assertEqual(snapshot(collection([reservations]), NOW)["accounted_tokens"], 50000)
        result = collection([reservations, final, final])
        self.assertEqual(snapshot(result, NOW)["accounted_tokens"], 955)
        self.assertNotIn("private", str(final))

    def test_two_channels_share_access_and_resume_without_replaying(self):
        active, calls = 0, 0
        lock = Lock()

        def send():
            nonlocal active, calls
            with lock:
                calls += 1
                active += 1
                self.assertEqual(active, 1)
            sleep(.02)
            with lock:
                active -= 1
            return {"text": "finished", "usage": {"prompt_tokens": 300, "completion_tokens": 500}}

        with TemporaryDirectory() as temp:
            now = datetime.now(timezone.utc)
            day = now.date().isoformat()
            root = Path(temp)
            cutoff = now + timedelta(hours=1)
            memory = Memory(root, day, cutoff, self.config)
            with ThreadPoolExecutor(2) as pool:
                jobs = [pool.submit(memory.call_groq, f"{ch}.draft", ch, "draft", ch, 1000, send)
                        for ch in ("legends", "science")]
                for job in jobs:
                    self.assertEqual(job.result()["text"], "finished")
            memory.data["calls"].append({"provider": "groq", "task": "unfinished", "status": "reserved",
                                         "at": now.isoformat(), "estimated_total_tokens": 1800})
            memory.save()
            restored = Memory(root, day, cutoff, self.config)
            with self.assertRaisesRegex(Blocked, "previous_request_outcome_unknown"):
                restored.call_groq("unfinished", "science", "draft", "content", 1000, send)
            self.assertEqual(restored.response("legends.draft")["text"], "finished")
            self.assertEqual(snapshot(restored.groq_rows(), now)["accounted_tokens"], 3400)
            self.assertEqual(calls, 2)

    def test_throttle_pauses_same_runner_without_marking_daily_exhaustion(self):
        with TemporaryDirectory() as temp:
            now = datetime.now(timezone.utc)
            memory = Memory(Path(temp), now.date().isoformat(), now + timedelta(hours=1), self.config)
            def blocked():
                raise ProviderError("http_429_groq_minute_rate_limit", retry_after=12, telemetry={
                    "http_status": 429, "limit_code": "otpm", "resets_at": (now + timedelta(seconds=12)).isoformat()})
            with self.assertRaises(ProviderError):
                memory.call_groq("t", "legends", "draft", "prompt", 500, blocked)
            self.assertNotIn("groq", memory.data["provider_stops"])
            self.assertEqual(memory.groq_rows()[0]["http_status"], 429)
            self.assertGreater(assessment(memory.groq_rows(), self.policy, "production", now, (200, 500))[1], 11)


    def test_adapter_keeps_measured_tokens_on_unusable_http_200(self):
        headers = Message()
        headers["x-ratelimit-remaining-requests"] = "42"
        reply = b'{"usage":{"prompt_tokens":150,"completion_tokens":60},"choices":[]}'
        with patch.dict("os.environ", {"EDITORIAL_GROQ_FREE_ONLY": "true", "GROQ_API_KEY": "test-value"}), \
                patch("src.editorial_providers.request", return_value=(reply, headers, 200)):
            with self.assertRaisesRegex(ProviderError, "empty_visible_content") as failure:
                text_call("groq", "synthetic")
        self.assertEqual(failure.exception.telemetry["http_status"], 200)
        self.assertEqual(failure.exception.telemetry["total_tokens"], 210)
        self.assertEqual(failure.exception.telemetry["rate_limits"]["x-ratelimit-remaining-requests"], 42)
