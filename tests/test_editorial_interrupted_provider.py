"""Interrupted provider calls must be idempotent or fail closed on restart."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.editorial_memory import Memory, digest, json_bytes
from src.editorial_providers import ProviderError, Blocked


class InterruptedProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.memory = Memory(root, "2026-11-23",
                             datetime.now(timezone.utc) + timedelta(hours=1),
                             {"daily_calls": {"groq": 5, "secondary_fixture": 5, "klein": 5},
                              "per_article_calls": {"groq": 3, "secondary_fixture": 3, "klein": 3}})

    def test_completed_task_checkpoint_integrity(self):
        inputs = {"prompt": "контроль"}
        task_id = "science-01.draft." + digest(json_bytes(inputs))[:16]
        answer = {"text": "исходный результат"}
        self.memory.data["tasks"][task_id] = {
            "id": task_id, "article": "science-01", "operation": "draft",
            "executor": "secondary_fixture", "attempts": 1,
            "status": "completed", "result": answer,
            "result_sha256": digest(json_bytes(answer)),
        }
        invoked = []
        self.assertEqual(self.memory.task("science-01", "draft", "secondary_fixture", inputs,
                                           lambda _: invoked.append(1)), answer)
        self.assertEqual(invoked, [])
        self.memory.data["tasks"][task_id]["result"] = {"text": "поврежденный результат"}
        with self.assertRaisesRegex(Blocked, "completed_task_result_integrity_failed"):
            self.memory.task("science-01", "draft", "secondary_fixture", inputs,
                             lambda _: invoked.append(1))
        self.assertEqual(invoked, [])

    def test_unknown_secondary_fixture_request_never_repeated(self):
        self.memory.data["calls"].append({
            "task": "science-01.draft.abc", "article": "science-01",
            "provider": "secondary_fixture", "status": "reserved", "at": "2026-11-23T00:00:00+00:00"})
        self.memory.save()
        invoked = []
        with self.assertRaisesRegex(ProviderError, "previous_provider_request_outcome_unknown"):
            self.memory.call("science-01.draft.abc", "science-01",
                             "secondary_fixture", lambda: invoked.append(1))
        self.assertEqual(invoked, [])
        self.assertEqual(len(self.memory.data["calls"]), 1)

    def test_unknown_klein_request_never_regenerated(self):
        self.memory.data["calls"].append({
            "task": "legends-01.image-1.abc", "article": "legends-01",
            "provider": "klein", "status": "reserved", "at": "2026-11-23T00:00:00+00:00"})
        self.memory.save()
        with self.assertRaisesRegex(ProviderError, "previous_provider_request_outcome_unknown"):
            self.memory.call("legends-01.image-1.abc", "legends-01",
                             "klein", lambda: b"unexpected")
        self.assertEqual(len(self.memory.data["calls"]), 1)

    def test_completed_response_is_reusable_without_second_provider_call(self):
        answer = {"text": "проверенный результат"}
        calls = []
        first = self.memory.call("science-01.draft.abc", "science-01",
                                 "secondary_fixture", lambda: (calls.append(1), answer)[1])
        self.assertEqual(first, answer)
        self.assertEqual(self.memory.response("science-01.draft.abc"), answer)
        # The response() checkpoint is consumed before call(), so no second
        # external request is necessary during a resumed model task.
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
