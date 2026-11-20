"""Nine-article genuine-model rehearsal contract, zero-network CI tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
import real_nine_qwen_e2e as proof
from editorial_nine_draft_queue import validate_plan, execute


class RealNineRehearsalContract(unittest.TestCase):
    def test_nine_distinct_topics_all_channels_have_three(self):
        rows=validate_plan(proof.PLAN)
        self.assertEqual(len(rows),9)
        self.assertEqual({c:sum(r["channel"]==c for r in rows)
                          for c in ("legends","science","cinema")},
                         {"legends":3,"science":3,"cinema":3})
        self.assertTrue(all(x["research_query"] for x in rows if x["channel"]=="science"))

    def test_safety_status_without_a_real_model(self):
        with TemporaryDirectory() as td:
            out=execute(proof.PLAN,Path(td),status_only=True)
        self.assertEqual(out["ready_for_owner"],0)
        self.assertFalse(out["publication_authorized"])
        self.assertFalse(out["rss_authorized"])
        self.assertEqual(len(out["tasks"]),9)
        rows=[proof.per_task_status(row) for row in out["tasks"]]
        self.assertEqual(sum(x["generated"] for x in rows),0)
        self.assertEqual(sum(x["automated_checks_completed"] for x in rows),0)
        self.assertTrue(all(x["pipeline_status"]=="not_started" for x in rows))

    def test_reports_omit_full_generated_article(self):
        row={"channel":"legends","status":"owner_review_pending","versions":1,
             "draft_sha256":"1"*64,"draft_chars_without_whitespace":3999,
             "article_ready_for_owner":False,
             "execution":{"exit_code":2,"stdout_excerpt":"PRIVATE ARTICLE TEXT"}}
        safe=proof.per_task_status(row)
        self.assertNotIn("stdout_excerpt",safe)
        self.assertNotIn("PRIVATE ARTICLE TEXT",str(safe))
        self.assertFalse(safe["publication_authorized"])
        self.assertEqual(safe["exit_code"],2)


if __name__=="__main__":
    unittest.main()
