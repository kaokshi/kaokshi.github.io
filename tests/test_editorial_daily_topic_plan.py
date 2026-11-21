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
    topics = {
        "legends": [
            "Сторож услышал стук в пустом аквариуме музея",
            "Шофёр обнаружил в старом гараже письмо с будущей датой",
            "Кондитер нашёл потайную комнату за холодильником",
            "На вокзале появился человек с чужим билетом",
            "В подземном тоннеле отключился последний фонарь",
        ],
        "science": [
            "Как колибри экономят энергию во время холодных ночей",
            "Зачем глубоководным рыбам прозрачные ткани тела",
            "Каким образом грибы очищают загрязнённую почву",
            "Почему некоторые пауки способны планировать на ветру",
            "Как растения обнаруживают повреждение соседних листьев",
        ],
        "cinema": [
            "«Матрица» (1999) — пересказ со спойлерами",
            "«Контакт» (1997) — обзор без спойлеров",
            "«Марсианин» (2015) — обзор без спойлеров",
            "«Гравитация» (2013) — пересказ со спойлерами",
            "«Аватар» (2009) — обзор без спойлеров",
        ],
    }
    return {"tasks": [
        {"topic": topic, "research_query":
          f"primary peer reviewed research {i} animal physiology"
          if channel == "science" else ""}
        for i, topic in enumerate(topics[channel][start:start+count], start)
    ]}

def provider_proposals(task, channel, start=0, count=3):
    if task == "plan_cinema_titles":
        films = [("Матрица", 1999), ("Контакт", 1997),
                 ("Марсианин", 2015), ("Гравитация", 2013), ("Аватар", 2009)]
        return {"films": [{"title": title, "year": year}
                          for title, year in films[start:start+count]]}
    return proposals(channel, start=start, count=count)


class TopicPlanTests(unittest.TestCase):
    def test_one_per_channel_pilot_needs_only_three_topics(self):
        calls=[]
        def provider(task,payload):
            calls.append((payload["channel"],payload["needed"]))
            return provider_proposals(task,payload["channel"],count=1)
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider,per_channel=1)
        self.assertEqual(len(plan["tasks"]),3)
        self.assertEqual(plan["per_channel"],1)
        self.assertEqual(calls,[("legends",1),("science",1),("cinema",1)])
        self.assertEqual(len({row["channel"] for row in plan["tasks"]}),3)
        self.assertFalse(plan["publication_authorized"])

    def test_rejection_diagnostics_contain_counts_not_topics(self):
        diagnostics={}
        calls={}
        def provider(task,payload):
            channel=payload["channel"]
            calls[channel]=calls.get(channel,0)+1
            if channel=="legends" and calls[channel]==1:
                return {"tasks":[{"topic":"Коротко","research_query":""}]}
            return provider_proposals(task,channel,count=1)
        with TemporaryDirectory() as tmp:
            result=create_plan("2026-11-22",Path(tmp),provider,
                               per_channel=1,diagnostics=diagnostics)
        self.assertEqual(len(result["tasks"]),3)
        self.assertEqual(diagnostics["channels"]["legends"]["invalid_topic"],1)
        self.assertEqual(diagnostics["channels"]["legends"]["attempts"],2)
        self.assertEqual(diagnostics["channels"]["cinema"]["accepted"],1)
        self.assertNotIn("Сторож",str(diagnostics))

    def test_pilot_still_rejects_bad_science_query(self):
        def provider(task,payload):
            if payload["channel"]=="science":
                return {"tasks":[{"topic":"Научный эксперимент над живым океаном","research_query":""}]}
            return proposals(payload["channel"],count=1)
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"planner_channel_incomplete:science:0/1"):
                create_plan("2026-11-22",Path(tmp),provider,per_channel=1)

    def test_three_independent_provider_calls_no_owner_topics(self):
        calls=[]
        def provider(task,payload):
            calls.append((task,payload["channel"],payload["needed"]))
            return provider_proposals(task,payload["channel"])
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(calls,[
            ("plan_channel_topics","legends",3),
            ("plan_channel_topics","science",3),
            ("plan_cinema_titles","cinema",3)])
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
                return provider_proposals(task,ch,count=2)
            if ch=="science":
                self.assertEqual(payload["needed"],1)
                return provider_proposals(task,ch,start=2,count=1)
            return provider_proposals(task,ch)
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
                    {"topic":"Сторож услышал стук в пустом аквариуме музея",
                     "research_query":""},
                    {"topic":"Сторож услышал стук в пустом аквариуме музея",
                     "research_query":""},
                    {"topic":"Коротко","research_query":""}
                ]}
            return provider_proposals(task,"legends",start=1,count=2)
        with TemporaryDirectory() as tmp:
            plan=create_plan("2026-11-22",Path(tmp),provider)
        self.assertEqual(calls,2)
        self.assertEqual(len(validate_plan(plan)),9)

    def test_existing_archive_repeats_are_rejected(self):
        with TemporaryDirectory() as tmp:
            archive=Path(tmp)/"nine-topics-2026-11-21.json"
            archive.write_text(json.dumps({"tasks":[
                {"topic":"Сторож услышал стук в пустом аквариуме музея"}]}),
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

    def test_cinema_typed_model_titles_are_canonicalized(self):
        def provider(task, payload):
            return provider_proposals(task, payload["channel"], count=1)
        with TemporaryDirectory() as tmp:
            result = create_plan("2026-11-22", Path(tmp), provider, per_channel=1)
        self.assertEqual(result["tasks"][-1]["topic"],
                         "«Матрица» (1999) — обзор без спойлеров")

    def test_cinema_requires_year_and_quoted_title(self):
        with TemporaryDirectory() as tmp:
            def provider(task,payload):
                if payload["channel"]=="cinema":
                    return {"films":[{"title":"Обзор неизвестного фильма","year":0}]}
                return proposals(payload["channel"])
            with self.assertRaisesRegex(ValueError,"planner_channel_incomplete:cinema"):
                create_plan("2026-11-22",Path(tmp),provider)


if __name__=="__main__":
    unittest.main()
