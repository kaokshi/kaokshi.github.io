import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import editorial_agent_v1 as agent
import editorial_ai_check as ai

def data(text="Текст статьи"):
    a={"slug":"legends-test","channel":"legends","body_html":"<p>"+text+"</p>"}
    score=91
    r={"article_slug":a["slug"],"text_sha256":agent.article_digest(a),
       "all_checks_completed":True,"checks":{m:{"status":"completed"} for m in (*agent.MODEL_A,"ai_accent_detector")},
       "editorial_threshold":{"model_a_index_0_100":score}}
    return a,r

def attempt(stage,before,after):
    return {"stage":stage,"before_text_sha256":before,"after_text_sha256":after,
            "ai_report_ref":"https://github.com/example/action/123","all_checks_rerun":True,
            "substantive_changes":["Исправлена конкретная логическая ошибка"]}

class AgentTests(unittest.TestCase):
    def test_first_attempt(self):
        a,r=data()
        x=agent.route(a,r,substantive_issues=["Нарушена логика истории"])
        self.assertEqual(x["decision"],"agent_targeted_revision")
        self.assertEqual(x["next_agent_attempt"],1)
        self.assertFalse(x["publication_authorized"])
    def test_no_invented_defect_from_high_score(self):
        a,r=data()
        self.assertEqual(agent.route(a,r,substantive_issues=[])["decision"],
                         "high_index_without_verified_defect_owner_review")
    def test_five_agents_then_two_chatgpt_attempts_then_owner(self):
        a, report = data("Текст после пяти исправлений")
        digests = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(5)]
        digests.append(agent.article_digest(a))
        history = [attempt("agent", before, after) for before, after in zip(digests, digests[1:])]
        x = agent.route(a, report, history, ["Факт расходится с источником"])
        self.assertEqual(x["decision"], "assistant_handoff_pending")
        self.assertEqual(x["next_assistant_attempt"], 1)
        for i in (1, 2):
            next_a, next_report = data(f"Текст после исправления ChatGPT {i}")
            history.append(attempt("assistant", agent.article_digest(a), agent.article_digest(next_a)))
            a, report = next_a, next_report
            x = agent.route(a, report, history, ["Факт всё ещё неверен"])
            self.assertEqual(x["decision"], "assistant_handoff_pending" if i == 1 else "owner_review_final")

    def test_unmodified_attempt_rejected(self):
        a,r=data()
        h=agent.article_digest(a)
        with self.assertRaises(ValueError):
            agent.route(a,r,[attempt("agent",h,h)],["Одна ошибка"])
    def test_stale_report_never_passes(self):
        a,r=data()
        r["text_sha256"]="f"*64
        self.assertEqual(agent.route(a,r)["decision"],"incomplete_or_stale_checks")
    def test_zero_and_eighty_not_trigger(self):
        a,r=data()
        r["editorial_threshold"]["model_a_index_0_100"]=80
        self.assertEqual(agent.route(a,r)["decision"],"other_editorial_checks_required")
    def test_paused_cinema(self):
        a,r=data()
        a["channel"]="cinema"
        self.assertEqual(agent.route(a,r)["decision"],"paused_channel")

class CheckerTests(unittest.TestCase):
    def test_model_a_mean_exact_80(self):
        def model(p):
            return {"status":"completed","data":{"score_percent":p}}
        results={"modernbert":model(70),"lightgbm":model(80),"transformer":model(90),
          "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":0.12}}}
        x=ai.threshold_review(results)
        self.assertEqual(x["model_a_index_0_100"],80)
        self.assertFalse(x["exceeds_owner_review_threshold"])
        self.assertEqual(x["checks"]["ai_accent_detector"]["index_0_100"],12)
    def test_failed_model_cannot_average(self):
        results={}
        self.assertIsNone(ai.threshold_review(results)["model_a_index_0_100"])
    def test_probability_field_is_scaled(self):
        with patch.object(ai,"tool_result",return_value={"status":"completed","data":{"probability":.8765,"skipped":False}}):
            x=ai.normalized_aiw_result(["cmd"],Path("/tmp/no-file"))
        self.assertEqual(x["data"]["score_percent"],87.65)
    def test_skipped_probability_is_invalid(self):
        with patch.object(ai,"tool_result",return_value={"status":"completed","data":{"probability":.10,"skipped":True}}):
            x=ai.normalized_aiw_result(["cmd"],Path("/tmp/no-file"))
        self.assertEqual(x["status"],"error")

if __name__=="__main__": unittest.main()
