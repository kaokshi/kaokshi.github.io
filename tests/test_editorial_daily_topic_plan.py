"""Offline regression for nine daily topics, no pretend LLM or public release."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_daily_topic_plan import create_plan
from editorial_nine_draft_queue import validate_plan


def sample():
    rows=[]
    for channel in ("legends","science","cinema"):
        for n in range(3):
            rows.append({"channel":channel,
                         "topic":f"Совершенно новое явление {channel} исследование {n}",
                         "research_query":f"primary scientific papers study {n}" if channel=="science" else ""})
    return {"tasks":rows}


class TopicPlanTests(unittest.TestCase):
    def test_real_provider_interface_but_no_mock_claim_of_creation(self):
        with TemporaryDirectory() as t:
            seen=[]
            def provider(task,payload):
                seen.append((task,payload))
                return sample()
            p=create_plan("2026-11-21",Path(t),provider)
            self.assertEqual(seen[0][0],"plan_topics")
            self.assertEqual(len(validate_plan(p)),9)
            self.assertFalse(p["article_generation_completed"])
            self.assertFalse(p["sources_verified"])
            self.assertFalse(p["publication_authorized"])

    def test_refuses_repeat_from_previous_private_plan(self):
        with TemporaryDirectory() as t:
            path=Path(t)/"nine-topics-2026-11-20.json"
            path.write_text(json.dumps({"tasks":[sample()["tasks"][0]]}),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"repeats"):
                create_plan("2026-11-21",Path(t),lambda *_:sample())

    def test_invalid_first_plan_recovers_with_fresh_proposal(self):
        invalid = sample()
        invalid["tasks"].pop()
        seen = []
        def provider(task, payload):
            seen.append(payload)
            return invalid if len(seen) == 1 else sample()
        with TemporaryDirectory() as t:
            result = create_plan("2026-11-21", Path(t), provider)
        self.assertEqual(len(validate_plan(result)), 9)
        self.assertEqual(len(seen), 2)
        self.assertIn("ОТКЛОНЁН", seen[1]["instruction"])
        self.assertFalse(result["article_generation_completed"])

    def test_rejects_incomplete_three_by_three(self):
        data=sample()
        data["tasks"].pop()
        with TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                create_plan("2026-11-21",Path(t),lambda *_:data)

if __name__=="__main__":
    unittest.main()
