"""Offline validation of bounded, autonomous per-channel Qwen topic planning."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_daily_topic_plan import create_plan
from editorial_nine_draft_queue import validate_plan


def proposals(channel, start=0, count=3):
    if channel == "cinema":
        rows=[{"topic": f"«Тестовая картина номер {i}» (2020) — обзор без спойлеров",
               "research_query": ""} for i in range(start,start+count)]
    elif channel == "science":
        rows=[{"topic": f"Необычный научный эксперимент об океане {i}",
               "research_query": f"ocean experimental primary study {i}"}
              for i in range(start,start+count)]
    else:
        rows=[{"topic": f"Тайна городской станции с фонарём номер {i}",
               "research_query": ""} for i in range(start,start+count)]
    return {"tasks": rows}


class TopicPlanTests(unittest.TestCase):
    def test_three_independent_provider_calls_no_owner_topics(self):
        calls=[]
        def provider(task,payload):
            calls.append((task,payload["channel"],payload["needed"]))
            return proposals(payload["channel"])
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(calls,[
            ("plan_channel_topics","legends",3),
            ("plan_channel_topics","science",3),
            ("plan_channel_topics","cinema",3)])
        self.assertEqual(len(validate_plan(plan)),9)
        self.assertEqual({c:sum(t["channel"]==c for t in plan["tasks"])
                          for c in ("legends","science","cinema")},
                         {"legends":3,"science":3,"cinema":3})
        self.assertFalse(plan["article_generation_completed"])
        self.assertFalse(plan["publication_authorized"])

    def test_retries_only_missing_topics_and_preserves_valid(self):
        calls={}
        def provider(task,payload):
            ch=payload["channel"]
            calls[ch]=calls.get(ch,0)+1
            if ch=="science" and calls[ch]==1:
                return proposals(ch,count=2)
            if ch=="science":
                self.assertEqual(payload["needed"],1)
                return proposals(ch,start=2,count=1)
            return proposals(ch)
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(calls,{"legends":1,"science":2,"cinema":1})
        self.assertEqual(len(validate_plan(plan)),9)

    def test_malformed_items_dont_erase_valid_slots(self):
        calls=0
        def provider(task,payload):
            nonlocal calls
            if payload["channel"]!="legends":
                return proposals(payload["channel"])
            calls+=1
            if calls==1:
                return {"tasks":[
                    {"topic":"Тайна городской станции с фонарём номер 0",
                     "research_query":""},
                    {"topic":"Тайна городской станции с фонарём номер 0",
                     "research_query":""},
                    {"topic":"Коротко","research_query":""}
                ]}
            return proposals("legends",start=1,count=2)
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(calls,2)
        self.assertEqual(len(validate_plan(plan)),9)

    def test_existing_archive_repeats_are_rejected(self):
        with TemporaryDirectory() as tmp:
            archive=Path(tmp)/"nine-topics-2026-11-21.json"
            archive.write_text(json.dumps({"tasks":[
                {"topic":"Тайна городской станции с фонарём номер 0"}]}),
                encoding="utf-8")
            def provider(task,payload):
                if payload["channel"]=="legends":
                    return proposals("legends",start=0,count=3)
                return proposals(payload["channel"])
            with self.assertRaisesRegex(ValueError,"planner_channel_incomplete:legends"):
                create_plan("2026-11-22",Path(tmp),provider)

    def test_failure_is_fail_closed_without_synthetic_topics(self):
        with TemporaryDirectory() as tmp:
            calls=[]
            def provider(task,payload):
                calls.append(task)
                return {"tasks":[]}
            with self.assertRaisesRegex(ValueError,"planner_channel_incomplete:legends:0/3"):
                create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(len(calls),4)

    def test_cinema_requires_year_and_quoted_title(self):
        with TemporaryDirectory() as tmp:
            def provider(task,payload):
                if payload["channel"]=="cinema":
                    return {"tasks":[{"topic":"Обзор неизвестного фильма","research_query":""}]}
                return proposals(payload["channel"])
            with self.assertRaisesRegex(ValueError,"planner_channel_incomplete:cinema"):
                create_plan("2026-11-22",Path(tmp),provider)


if __name__=="__main__":
    unittest.main()
