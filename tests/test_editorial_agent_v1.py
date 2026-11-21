import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import copy
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import editorial_agent_v1 as agent
import editorial_ai_check as ai
import editorial_revision as revision
import editorial_revision_checks as revision_checks

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
    def test_five_agent_then_two_assistant_attempts_then_owner(self):
        hashes = [hashlib.sha256(f"draft-{i}".encode()).hexdigest() for i in range(8)]
        a,r=data("Итог восьмой редакции")
        hashes[-1] = agent.article_digest(a)
        history = [attempt("agent", hashes[i], hashes[i+1]) for i in range(5)]
        for n in range(5):
            partial = history[:n]
            text = f"Глава редакции {n}"
            draft, report = data(text)
            if partial:
                partial[-1] = {**partial[-1], "after_text_sha256": agent.article_digest(draft)}
            route = agent.route(draft, report, partial, ["Нужна проверка факта"])
            self.assertEqual(route["decision"], "agent_targeted_revision")
            self.assertEqual(route["next_agent_attempt"], n + 1)
        # A full, correctly chained 5+2 history never offers an eighth edit.
        partial = history[:]
        result = agent.route({"slug":a["slug"],"channel":a["channel"],
                              "body_html":"<p>Черновик после пяти правок</p>"},
                             {**r,"text_sha256": hashes[5]}, partial,
                             ["Нужна проверка факта"])
        # A history pointing to a different text cannot be reused.
        self.assertEqual(result["decision"], "history_text_digest_mismatch")
        fifth, rep = data("Текст после пяти агентских исправлений")
        history[-1] = {**history[-1], "after_text_sha256": agent.article_digest(fifth)}
        self.assertEqual(agent.route(fifth, rep, history, ["Ошибка"])["decision"],
                         "chatgpt_revision_handoff")
        first_assistant, rep = data("Текст после первой правки ChatGPT")
        history.append(attempt("assistant", agent.article_digest(fifth),
                               agent.article_digest(first_assistant)))
        self.assertEqual(agent.route(first_assistant, rep, history, ["Ошибка"])["decision"],
                         "chatgpt_revision_handoff")
        second_assistant, rep = data("Текст после второй правки ChatGPT")
        history.append(attempt("assistant", agent.article_digest(first_assistant),
                               agent.article_digest(second_assistant)))
        self.assertEqual(agent.route(second_assistant, rep, history, ["Ошибка"])["decision"],
                         "owner_review_final")
        with self.assertRaises(ValueError):
            agent.route(second_assistant, rep, history +
                        [attempt("assistant", agent.article_digest(second_assistant),
                                 hashlib.sha256(b"extra").hexdigest())], ["Ошибка"])
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
    def test_cinema_active_for_review(self):
        a,r=data()
        a["channel"]="cinema"
        self.assertEqual(agent.route(a,r)["decision"],"inspect_for_substantive_issues")

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
    def test_above_85_is_not_rounded_back_to_threshold(self):
        results = {name: {"status": "completed", "data": {"score_percent": score}}
                   for name, score in zip(ai.MODEL_A, (80, 85, 90.0001))}
        results["ai_accent_detector"] = {"status": "completed", "data": {"ai_accent_score": 0.11}}
        review = ai.threshold_review(results)
        self.assertTrue(review["exceeds_owner_review_threshold"])
        self.assertGreater(review["model_a_index_0_100"], 85)

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

class RevisionTests(unittest.TestCase):
    def setUp(self):
        repeated = "<p>В полночь открылась дверь, и сторож услышал шаги в коридоре.</p>"
        body = ("<h2>Глава</h2><p>Сторож,, остался у окна.</p>" + repeated + "\n" + repeated
                + '<p><a href="https://example.org">Запись,, сохранилась</a>.</p>'
                + "<p>" + "Часы пробили полночь. " * 200 + "</p>")
        self.draft = {"slug": "legends-revision-test", "channel": "legends", "status": "draft",
                      "body_html": body, "release_hold": True, "publication_authorized": False}
        self.case = {"decision": "checks_pending", "versions": [
            {"stage": "original", "article": copy.deepcopy(self.draft),
             "text_sha256": agent.article_digest(self.draft)}]}

    def report(self, article, score=91, complete=True, issues=()):
        _, ai_report = data()
        ai_report["article_slug"] = article["slug"]
        ai_report["text_sha256"] = agent.article_digest(article)
        ai_report["editorial_threshold"]["model_a_index_0_100"] = score
        return {"ai": ai_report, "text_sha256": agent.article_digest(article),
                "automated_checks_completed": complete, "findings": [],
                "grammar": {"issues": list(issues)}, "vale": {"issues": []},
                "manual_checks_pending": ["fiction_originality_and_logic", "image_rights"]}

    def test_two_real_edits_then_assistant_on_residual_issue_even_after_index_drops(self):
        checked = []
        snapshots = []

        def check(article):
            checked.append(article["body_html"])
            return self.report(article, score=91 if len(checked) == 1 else 70,
                               issues=["Проверить мотивацию персонажа."])

        def save(case):
            snapshots.append(copy.deepcopy(case))

        revision.run_case(self.case, check, save)
        self.assertEqual(len(checked), 3)
        self.assertEqual(checked[1].count("дверь, и сторож"), 1)
        self.assertIn("Сторож,, остался", checked[1])
        self.assertIn("Сторож, остался", checked[2])
        self.assertIn('<a href="https://example.org">Запись,, сохранилась</a>', checked[2])
        self.assertEqual(self.case["decision"], "assistant_handoff_pending")
        self.assertEqual(self.case["handoff"]["transport_status"], "not_connected")
        self.assertTrue(self.case["versions"][0]["model_a_review_triggered"])
        self.assertFalse(self.case["versions"][-1]["model_a_review_triggered"])
        self.assertNotIn("report", snapshots[0]["versions"][-1])
        response = {"base_text_sha256": self.case["handoff"]["text_sha256"], "edits": [
            {"paragraph": 1, "before": "Сторож, остался у окна.",
             "after": "Сторож остался у окна, чтобы разглядеть ключ в руке незнакомца.",
             "reason": "Объяснил решение сторожа."}]}
        revision.run_case(self.case, check, save, response)
        self.assertEqual(self.case["decision"], "assistant_handoff_pending")
        self.assertEqual(len(checked), 4)
        second = {"base_text_sha256": self.case["handoff"]["text_sha256"], "edits": [
            {"paragraph": 1, "before": "Сторож остался у окна, чтобы разглядеть ключ в руке незнакомца.",
             "after": "Сторож подошёл к окну, потому что увидел в руке незнакомца ключ.",
             "reason": "Уточнил мотивацию персонажа."}]}
        revision.run_case(self.case, check, save, second)
        self.assertEqual(self.case["decision"], "owner_review_pending")
        self.assertIn("rework_requested_after_exhausted_revisions", self.case["handoff"]["reasons"])
        self.assertEqual(len(checked), 5)
        self.assertEqual([v["stage"] for v in self.case["versions"]],
                         ["original", "agent", "agent", "assistant", "assistant"])
        for before, after in zip(self.case["versions"], self.case["versions"][1:]):
            self.assertEqual(before["text_sha256"], after["before_text_sha256"])
            self.assertEqual(after["report"]["text_sha256"], after["text_sha256"])
            self.assertTrue(after["article"]["release_hold"])
            self.assertFalse(after["article"]["publication_authorized"])
        self.assertEqual(self.case["versions"][0]["article"], self.draft)
        with self.assertRaises(ValueError):
            revision.run_case(self.case, check, save, response)

    def test_incomplete_candidate_check_is_retried_without_another_edit(self):
        checked = []

        def interrupted(article):
            checked.append(agent.article_digest(article))
            return self.report(article, complete=len(checked) == 1)

        revision.run_case(self.case, interrupted, lambda _: None)
        self.assertEqual(self.case["decision"], "checks_pending")
        self.assertEqual(len(self.case["versions"]), 2)
        self.assertNotIn("index", self.case["versions"][-1])
        revision.run_case(self.case, self.report, lambda _: None)
        self.assertEqual(len(self.case["versions"]), 3)
        self.assertEqual(self.case["versions"][1]["text_sha256"], checked[1])
        self.assertEqual(self.case["decision"], "owner_review_pending")

    def test_stale_and_unchanged_assistant_responses(self):
        draft = {**self.draft, "body_html": "<p>Сторож вышел.</p>"}
        edit = {"paragraph": 1, "before": "Сторож вышел.", "after": "Сторож вышел.  ",
                "reason": "Пример."}
        with self.assertRaises(ValueError):
            revision.assistant_edit(draft, {"base_text_sha256": "stale", "edits": [edit]})
        with self.assertRaises(ValueError):
            revision.assistant_edit(draft, {"base_text_sha256": agent.article_digest(draft), "edits": [edit]})

    def test_high_index_without_defect_does_not_rewrite(self):
        self.case["versions"] = [{"stage": "original",
                                 "article": {**self.draft, "body_html": "<p>Сторож вышел.</p>"}}]
        version = self.case["versions"][0]
        version["text_sha256"] = agent.article_digest(version["article"])
        revision.run_case(self.case, self.report, lambda _: None)
        self.assertEqual(len(self.case["versions"]), 1)
        self.assertEqual(self.case["decision"], "owner_review_pending")
        self.assertIn("model_a_editorial_review_advisory", self.case["handoff"]["reasons"])

    def test_cli_skips_unknown_channel_without_creating_case(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "cinema.json"
            source.write_text(json.dumps({**self.draft, "channel": "unlisted"}), encoding="utf-8")
            output = Path(temp) / "case"
            with patch.object(sys, "argv", ["revision", "--article", str(source), "--case-dir", str(output)]):
                revision.main()
            self.assertFalse(output.exists())

    def test_real_quality_adapter_marks_missing_grammar_and_model(self):
        with (patch.object(revision_checks, "command_report", return_value={"status": "error"}),
              patch.object(revision_checks, "evaluate_article", return_value={}),
              patch.object(revision_checks, "CONTENT", Path("/nonexistent"))):
            def unavailable(_):
                raise OSError("LanguageTool not installed")
            result = revision_checks.check_draft(self.draft, unavailable)
        self.assertEqual(result["text_sha256"], agent.article_digest(self.draft))
        self.assertEqual(result["grammar"]["status"], "error")
        self.assertFalse(result["automated_checks_completed"])
        self.assertIn("image_rights", result["manual_checks_pending"])


if __name__=="__main__": unittest.main()
