"""Nine planned private drafts, including the newly active cinema channel."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_nine_draft_queue import validate_plan, execute
from editorial_writer import write_article
from editorial_agent_v1 import ACTIVE, route, article_digest


def plan():
    tasks=[]
    for channel in ("legends","science","cinema"):
        for n in range(3):
            item={"channel":channel,"topic":f"Совершенно уникальная тема {channel} номер {n}"}
            if channel=="science":
                item["research_query"]=f"scientific evidence topic {n}"
            tasks.append(item)
    return {"date":"2026-11-21","tasks":tasks}


class NineDraftTests(unittest.TestCase):
    def test_all_three_channels_are_active_for_drafting(self):
        self.assertIn("cinema",ACTIVE)
        def provider(task,request):
            return {"title":"Новый кинообзор",
                    "paragraphs":["Подробный кинообзор. "*75,
                                  "События развиваются. "*75,
                                  "Рецензия завершена. "*75],
                    "cta":"Подпишитесь на канал «Кадр за Кадром» ради новых киноисторий."}
        a=write_article("cinema","Кинолента, о которой не писали",provider)
        self.assertEqual(a["channel"],"cinema")
        self.assertFalse(a["publication_authorized"])
        self.assertTrue(a["release_hold"])

    def test_nine_slots_and_no_fake_generation(self):
        p=plan()
        tasks=validate_plan(p)
        self.assertEqual(len(tasks),9)
        with TemporaryDirectory() as td:
            result=execute(p,Path(td),status_only=True)
        self.assertEqual(result["requested"],9)
        self.assertEqual(result["per_channel"],{"legends":3,"science":3,"cinema":3})
        self.assertEqual(result["ready_for_owner"],0)
        self.assertEqual(result["completed_for_publication"],0)
        self.assertFalse(result["publication_authorized"])
        self.assertTrue(all(x["status"]=="not_created" for x in result["tasks"]))

    def test_bad_distribution_and_duplicates(self):
        p=plan()
        p["tasks"][-1]["channel"]="science"
        p["tasks"][-1]["research_query"]="scientific evidence new topic"
        with self.assertRaisesRegex(ValueError,"exactly three"):
            validate_plan(p)
        p=plan()
        p["tasks"][-1]["topic"]=p["tasks"][0]["topic"]
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            validate_plan(p)

    def test_deadline_prevents_new_work_but_preserves_all_nine_tasks(self):
        fixed=datetime(2026,11,21,7,50,tzinfo=timezone.utc)
        with TemporaryDirectory() as td:
            result=execute(plan(),Path(td),deadline=fixed+timedelta(minutes=10),
                           health_check=lambda url,backend:{"status":"ready"},
                           now=lambda:fixed)
        self.assertEqual(result["requested"],9)
        self.assertEqual(result["ready_for_owner"],0)
        self.assertEqual(len(result["tasks"]),9)
        self.assertTrue(all(row["execution"]["reason"]=="review_deadline_reached"
                            for row in result["tasks"]))
        self.assertFalse(result["publication_authorized"])

    def test_deadline_allows_run_when_sufficient_time(self):
        fixed=datetime(2026,11,21,7,0,tzinfo=timezone.utc)
        calls=[]
        def fake_runner(workspace, channel, topic, **kwargs):
            calls.append((channel,topic))
            return {"execution":{"exit_code":0,"pipeline_status":"exited"}}
        with TemporaryDirectory() as td:
            result=execute(plan(),Path(td),deadline=fixed+timedelta(hours=2),
                           health_check=lambda url,backend:{"status":"ready"},
                           runner=fake_runner,now=lambda:fixed)
        self.assertEqual(len(calls),9)
        self.assertEqual(result["ready_for_owner"],0)
        self.assertEqual(result["completed_for_publication"],0)

    def test_completed_owner_dossier_is_not_regenerated(self):
        from unittest.mock import patch
        p = plan()
        calls = []
        def existing_status(workspace, task):
            return {"channel": task["channel"], "article_ready_for_owner": task["channel"] == "legends"
                    and task["topic"].endswith("номер 0"), "status": "owner_review_pending"}
        def fake_runner(workspace, channel, topic, **kwargs):
            calls.append((channel, topic))
            return {"execution": {"exit_code": 0, "pipeline_status": "exited"}}
        with TemporaryDirectory() as td, patch(
            "editorial_nine_draft_queue.report_status", side_effect=existing_status
        ):
            result = execute(p, Path(td),
                             health_check=lambda url, backend: {"status": "ready"},
                             runner=fake_runner)
        self.assertEqual(len(calls), 8)
        self.assertEqual(result["ready_for_owner"], 1)
        self.assertEqual(result["tasks"][0]["execution"]["status"], "already_ready")
        self.assertFalse(result["publication_authorized"])

    def test_existing_complete_dossier_survives_unavailable_model(self):
        from unittest.mock import patch
        def existing_status(workspace, task):
            return {"channel": task["channel"], "article_ready_for_owner": task["channel"] == "cinema",
                    "status": "owner_review_pending" if task["channel"] == "cinema" else "not_created"}
        with TemporaryDirectory() as td, patch(
            "editorial_nine_draft_queue.report_status", side_effect=existing_status
        ):
            result = execute(plan(), Path(td),
                             health_check=lambda url, backend: {"status": "not_ready"})
        self.assertEqual(result["ready_for_owner"], 3)
        self.assertTrue(all(row["execution"]["status"] == "already_ready"
                            for row in result["tasks"] if row["channel"] == "cinema"))
        self.assertTrue(all(row["execution"]["reason"] == "model_not_ready"
                            for row in result["tasks"] if row["channel"] != "cinema"))

    def test_naive_deadline_rejected(self):
        with TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,"timezone"):
                execute(plan(),Path(td),status_only=True,
                        deadline=datetime(2026,11,21,11))

    def test_unavailable_model_never_creates_article(self):
        with TemporaryDirectory() as td:
            result=execute(plan(),Path(td),health_check=lambda url,backend:{"status":"not_ready"})
        self.assertEqual(result["ready_for_owner"],0)
        self.assertEqual(len(result["tasks"]),9)
        self.assertTrue(all(x["execution"]["reason"]=="model_not_ready" for x in result["tasks"]))


if __name__=="__main__":
    unittest.main()
