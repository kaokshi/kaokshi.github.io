"""Model A 85 advisory and bounded trend escalation without any live detector."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_agent_v1 import article_digest, MODEL_A
from editorial_revision import run_case
from editorial_ai_check import threshold_review


def draft():
    return {"slug":"legends-trend-test","channel":"legends","status":"draft",
            "body_html":"<p>Он вошёл в коридор.</p>",
            "publication_authorized":False,"release_hold":True}


def report(article, score):
    digest=article_digest(article)
    return {"text_sha256":digest,"automated_checks_completed":True,
      "ai":{"article_slug":article["slug"],"text_sha256":digest,"all_checks_completed":True,
            "checks":{k:{"status":"completed"} for k in (*MODEL_A,"ai_accent_detector")},
            "editorial_threshold":{"model_a_index_0_100":score}},
      "findings":[],"grammar":{"issues":[]},"vale":{"issues":[]},
      "semantic":{"status":"completed","issues":[{"id":"logic","paragraph":1,"quote":"Он вошёл",
                                               "reason":"Нужна мотивация"}]},
      "facts":{"status":"not_applicable"},
      "manual_checks_pending":["fiction_originality_and_logic"]}


class TrendTests(unittest.TestCase):
    def case(self):
        article=draft()
        return {"decision":"checks_pending","handoff":None,
                "versions":[{"article":article,"stage":"original",
                             "text_sha256":article_digest(article)}]}

    def edit(self, article, _):
        return ({**article,"body_html":article["body_html"].replace("коридор.",
                    "коридор, потому что услышал голос.")},
                ["Объяснён мотив действий"])

    def test_advisory_threshold_exact_and_strict(self):
        def results(p):
            return {k:{"status":"completed","data":{"score_percent":p}} for k in MODEL_A} | {
                "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":0.1}}}
        self.assertFalse(threshold_review(results(85))["exceeds_owner_review_threshold"])
        self.assertTrue(threshold_review(results(85.001))["exceeds_owner_review_threshold"])
        self.assertTrue(threshold_review(results(85.001))["advisory_only"])

    def test_increase_prepares_handoff_not_more_agent_edits(self):
        case=self.case()
        scores=iter([86,90])
        run_case(case,lambda a:report(a,next(scores)),lambda c:None,semantic_editor=self.edit)
        self.assertEqual(len(case["versions"]),2)
        self.assertEqual(case["versions"][-1]["model_a_trend"],"increased")
        self.assertEqual(case["decision"],"assistant_handoff_pending")
        self.assertEqual(case["handoff"]["transport_status"],"not_connected")
        self.assertIn("model_a_increased_after_agent_revision",case["handoff"]["reasons"])

    def test_plateau_stops_edits(self):
        case=self.case()
        scores=iter([87,87])
        run_case(case,lambda a:report(a,next(scores)),lambda c:None,semantic_editor=self.edit)
        self.assertEqual(len(case["versions"]),2)
        self.assertEqual(case["versions"][-1]["model_a_trend"],"plateau")
        self.assertEqual(case["decision"],"assistant_handoff_pending")

    def test_local_provider_problem_handoff(self):
        case=self.case()
        def failed(_, report):
            report["semantic_edit_status"]="provider_error"
            report["semantic_edit_error"]="local retries exhausted"
            return None
        run_case(case,lambda a:report(a,86),lambda c:None,semantic_editor=failed)
        self.assertEqual(case["decision"],"assistant_handoff_pending")
        self.assertIn("local_agent_provider_error",case["handoff"]["reasons"])
        self.assertEqual(len(case["versions"]),1)


if __name__=="__main__":
    unittest.main()
