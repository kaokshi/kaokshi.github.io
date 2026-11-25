"""Synthetic fixture: a paid plan response must never crash the executor.

No real article text, prompt or model output is stored here.
"""
import json
import unittest

from src.editorial_routerai_two_articles import json_bytes
from src.editorial_zen_executor import Session


class FakeCore:
    def __init__(self):
        self.registered = []

    def access(self, operation, change=False):
        return operation(self)

    def register_title(self, article, channel, title):
        self.registered.append((article, channel, title))


def session(channel="legends"):
    value = Session.__new__(Session)
    value.article = "job/legends/1"
    value.channel = channel
    value.core = FakeCore()
    value.saved = []
    value.save = lambda name, data: value.saved.append((name, data))
    return value


class PlanCheckpointTest(unittest.TestCase):
    def test_valid_title_is_registered(self):
        value = session()
        body = json.dumps({"title": "Синтетический заголовок", "assignment": "x"})
        value.evidence("legends/qwen-plan.json", json_bytes({"output": body, "sha256": "x"}))
        self.assertEqual(len(value.saved), 1)
        self.assertEqual(value.core.registered,
                         [("job/legends/1", "legends", "Синтетический заголовок")])

    def test_missing_title_does_not_crash_and_registers_nothing(self):
        value = session()
        body = json.dumps({"assignment": "x"})
        value.evidence("legends/qwen-plan.json", json_bytes({"output": body, "sha256": "x"}))
        self.assertEqual(value.core.registered, [])
        self.assertEqual(len(value.saved), 1)

    def test_non_json_plan_body_does_not_crash(self):
        value = session()
        value.evidence("legends/qwen-plan.json", json_bytes({"output": "not json", "sha256": "x"}))
        self.assertEqual(value.core.registered, [])

    def test_invalid_title_length_is_left_to_later_validation(self):
        value = session()
        body = json.dumps({"title": "abc", "assignment": "x"})
        value.evidence("legends/qwen-plan.json", json_bytes({"output": body, "sha256": "x"}))
        self.assertEqual(value.core.registered, [])

    def test_other_channels_are_not_registered_here(self):
        value = session("science")
        body = json.dumps({"title": "Синтетический заголовок", "assignment": "x"})
        value.evidence("science/qwen-plan.json", json_bytes({"output": body, "sha256": "x"}))
        self.assertEqual(value.core.registered, [])


if __name__ == "__main__":
    unittest.main()
