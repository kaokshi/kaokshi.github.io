"""Regression: local Qwen must not propose duplicated/nonexistent paragraph edits.

This is a deterministic fake-backend test. It does NOT claim the real local
model will improve a story; it proves the server receives a constrained schema.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_local_llm import complete, ProviderFailure

REQUEST = {
    "task": "revise",
    "title": "Ночной коридор",
    "channel": "legends",
    "text_sha256": "a" * 64,
    "instruction": "Исправь только подтверждённую смысловую проблему.",
    "paragraphs": [
        {"paragraph": 1, "text": "Первый абзац с событием."},
        {"paragraph": 2, "text": "Второй абзац с переходом."},
        {"paragraph": 3, "text": "Третий абзац — финал."},
    ],
    "issues": [
        {"id": "finding-1", "paragraph": 2, "category": "chronology",
         "quote": "переходом", "reason": "Нарушена хронология."},
        {"id": "finding-2", "paragraph": 2, "category": "logic",
         "quote": "переходом", "reason": "Необоснованный переход."},
    ],
}
ENV = {"ZEN_LLM_BACKEND": "ollama", "ZEN_LLM_MODEL": "local-qwen",
       "ZEN_LLM_URL": "http://127.0.0.1:11434", "ZEN_LLM_TIMEOUT": "30"}


class TestAddressedRevision(unittest.TestCase):
    def test_schema_restricts_to_one_existing_target(self):
        calls = []
        def send(endpoint, path, payload, timeout):
            calls.append((path, payload))
            return {"message": {"content": json.dumps({
                "edits": [{"issue_id": "finding-1", "paragraph": 2,
                           "after": "Второй абзац теперь логично продолжает событие.",
                           "reason": "Исправлена хронология."}]
            }, ensure_ascii=False)}, "done_reason": "stop"}
        result = complete(REQUEST, env=ENV, send=send)
        self.assertEqual(result["edits"][0]["before"], REQUEST["paragraphs"][1]["text"])
        self.assertEqual(len(calls), 1)
        path, payload = calls[0]
        self.assertEqual(path, "/api/chat")
        rule = payload["format"]["properties"]["edits"]
        self.assertEqual(rule["maxItems"], 1)
        fields = rule["items"]["properties"]
        self.assertEqual(fields["issue_id"]["enum"], ["finding-1"])
        self.assertEqual(fields["paragraph"]["minimum"], 2)
        self.assertEqual(fields["paragraph"]["maximum"], 2)
        self.assertEqual(json.loads(payload["messages"][1]["content"])["issues"],
                         REQUEST["issues"][:1])

    def test_wrong_paragraph_never_passes_schema(self):
        def send(endpoint, path, payload, timeout):
            return {"message": {"content": json.dumps({
                "edits": [{"issue_id": "finding-1", "paragraph": 99,
                           "after": "Неверная нумерация.", "reason": "wrong"}]
            })}, "done_reason": "stop"}
        with self.assertRaisesRegex(ProviderFailure, "schema"):
            complete(REQUEST, env=ENV, send=send)

    def test_duplicate_edits_fail(self):
        def send(endpoint, path, payload, timeout):
            return {"message": {"content": json.dumps({
                "edits": [{"issue_id": "finding-1", "paragraph": 2,
                           "after": "Изменение", "reason": "Правка"}] * 2
            })}, "done_reason": "stop"}
        with self.assertRaisesRegex(ProviderFailure, "schema"):
            complete(REQUEST, env=ENV, send=send)

    def test_empty_edit_is_safe_noop(self):
        def send(endpoint, path, payload, timeout):
            return {"message": {"content": '{"edits":[]}'}, "done_reason": "stop"}
        self.assertEqual(complete(REQUEST, env=ENV, send=send)["edits"], [])

    def test_missing_valid_target_fails_before_api(self):
        request = {**REQUEST, "issues": [{"id": "no-such", "paragraph": 999}]}
        with self.assertRaisesRegex(ProviderFailure, "No valid diagnosed"):
            complete(request, env=ENV, send=lambda *args: self.fail("network must not be called"))


if __name__ == "__main__":
    unittest.main()
