"""Nine planned private drafts, including the newly active cinema channel."""
import json
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
                    "paragraphs":["Подробный кинообзор. "*130,
                                  "События развиваются. "*130,
                                  "Рецензия завершена. "*130],
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
        with self.assertRaisesRegex(ValueError,"exactly three"):
            validate_plan(p)
        p=plan()
        p["tasks"][-1]["topic"]=p["tasks"][0]["topic"]
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            validate_plan(p)

    def test_unavailable_model_never_creates_article(self):
        with TemporaryDirectory() as td:
            result=execute(plan(),Path(td),health_check=lambda url,backend:{"status":"not_ready"})
        self.assertEqual(result["ready_for_owner"],0)
        self.assertEqual(len(result["tasks"]),9)
        self.assertTrue(all(x["execution"]["reason"]=="model_not_ready" for x in result["tasks"]))


if __name__=="__main__":
    unittest.main()
