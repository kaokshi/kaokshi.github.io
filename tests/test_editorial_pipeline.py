"""Offline tests for the private author, evidence and semantic revision pipeline."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from editorial_agent_v1 import article_digest, REQUIRED_CHECKS
from editorial_factcheck import fact_check
from editorial_learning import evaluate_holdout, reviewed_observations
from editorial_pipeline import execute, new_case
from editorial_research import europe_pmc
from editorial_semantic import diagnose, revise
from editorial_writer import Pending, command_provider, write_article

URL = "https://example.org/study"
SOURCE = "The measurements showed that the particles reached two metres per second during the test."


def draft(channel="science"):
    return {"slug": f"{channel}-pilot", "channel": channel, "title": "Движение частиц", "status": "draft",
            "body_html": "<p>Частицы достигли пяти метров в секунду.</p><p>Опишем опыт внимательнее.</p>",
            "release_hold": True, "publication_authorized": False}


def diagnosis(article):
    return {"text_sha256": article_digest(article),
            "issues": [{"id": "speed", "category": "unsupported_claim", "paragraph": 1,
                        "quote": "пяти метров", "reason": "В данных опыта указана другая скорость."}],
            "edits": [{"issue_id": "speed", "paragraph": 1, "before": "Частицы достигли пяти метров в секунду.",
                       "after": "В опыте частицы достигли двух метров в секунду.", "reason": "Исправлена скорость."}]}


def quality(article, urls):
    digest = article_digest(article)
    return {"text_sha256": digest, "automated_checks_completed": True,
            "findings": [], "grammar": {"issues": []}, "vale": {"issues": []}, "manual_checks_pending": [],
            "ai": {"article_slug": article["slug"], "text_sha256": digest,
                   "all_checks_completed": True, "checks": {k: {"status": "completed"} for k in REQUIRED_CHECKS},
                   "editorial_threshold": {"model_a_index_0_100": 81}}}


class WriterTests(unittest.TestCase):
    def test_no_configured_provider_is_explicit(self):
        with self.assertRaises(Pending) as error:
            write_article("legends", "Сумерки", command_provider(None))
        self.assertEqual(error.exception.status, "provider_not_configured")

    def test_writer_contract_escapes_model_output_and_checks_duplicate_titles(self):
        paragraphs = ["Первый эпизод. " + "Дверь открылась. " * 85,
                      "Второй эпизод. " + "Анна осталась. " * 85,
                      "Третий эпизод. " + "Ветер стих. " * 85 + " <script>"]
        reply = {"title": "Сигнал", "paragraphs": paragraphs,
                 "cta": "Подпишитесь на «Легенды тёмных улиц», чтобы встретить другие странные ночи."}

        def provider(task, request):
            self.assertEqual(task, "write")
            self.assertIn("мотивацию", request["instruction"])
            return reply

        reply["paragraphs"].append(reply["cta"])
        article = write_article("legends", "Сумерки", provider)
        self.assertEqual(article["body_html"].count(reply["cta"]), 1)
        self.assertIn("&lt;script&gt;", article["body_html"])
        self.assertTrue(article["release_hold"])
        self.assertFalse(article["publication_authorized"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            write_article("legends", "Сумерки", provider, ["СИГНАЛ!"])

    def test_local_executable_protocol(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "mock-lm"
            path.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                            "print(json.dumps({'echo': json.load(sys.stdin)['task']}))\n")
            path.chmod(0o700)
            self.assertEqual(command_provider(path)("diagnose", {}), {"echo": "diagnose"})


class SemanticTests(unittest.TestCase):
    def test_issue_quote_and_change_are_bound_to_original_paragraph(self):
        article = draft()
        review = diagnosis(article)
        provider = command_provider(None)
        findings = diagnose(article, provider, review)["issues"]
        corrected, _ = revise(article, findings, provider, review)
        self.assertIn("двух метров", corrected["body_html"])
        self.assertNotEqual(article_digest(corrected), article_digest(article))
        edited = deepcopy(review)
        edited["edits"][0]["paragraph"] = 2
        with self.assertRaisesRegex(ValueError, "diagnosed issue"):
            revise(article, findings, provider, edited)
        edited = deepcopy(review)
        edited["issues"][0]["quote"] = "девяти метров"
        self.assertEqual(diagnose(article, provider, edited)["status"], "invalid_diagnosis")
        self.assertEqual(diagnose(corrected, provider, review)["status"], "provider_not_configured")

    def test_staged_execution_rereads_facts_and_reuses_count_across_resume(self):
        article = draft("legends")
        review = diagnosis(article)
        corrected, _ = revise(article, review["issues"], None, review)
        final = {"text_sha256": article_digest(corrected), "issues": []}
        configs = {r["text_sha256"]: r for r in (review, final)}
        checked = []

        def checker(item, links):
            checked.append(article_digest(item))
            result = quality(item, links)
            result["automated_checks_completed"] = len(checked) != 2
            return result

        with TemporaryDirectory() as temp:
            path = Path(temp)
            case = new_case(path, path, article, "legends", None, None)
            decision = execute(case, path, command_provider(None), None, None, configs, None, checker)
            self.assertEqual(decision, "checks_pending")
            case = json.loads((path / "case.json").read_text())
            self.assertEqual(len(case["versions"]), 2)
            decision = execute(case, path, command_provider(None), None, None, configs, None, checker)
            self.assertEqual(decision, "owner_review_pending")
            self.assertEqual(checked, [article_digest(article), article_digest(corrected), article_digest(corrected)])
            self.assertEqual(len(case["versions"]), 2)
            self.assertEqual(case["versions"][1]["stages"]["fact_check"]["status"], "not_applicable")
            feedback = {"reviewer": "owner", "slug": article["slug"], "channel": "legends",
                        "source_text_sha256": article_digest(article), "reviewed_text_sha256": article_digest(corrected),
                        "findings": {"story_logic": "resolved"}}
            self.assertEqual(reviewed_observations(case, feedback)[0]["rule"], "semantic")

    def test_two_semantic_revisions_then_single_assistant(self):
        article = draft("legends")
        a = diagnosis(article)
        b_article, _ = revise(article, a["issues"], None, a)
        b = diagnosis(b_article)
        b["issues"][0]["quote"] = "двух метров"
        b["edits"][0]["before"] = a["edits"][0]["after"]
        b["edits"][0]["after"] = "В полевом опыте частицы достигли двух метров в секунду."
        c_article, _ = revise(b_article, b["issues"], None, b)
        c = diagnosis(c_article)
        c["issues"][0]["quote"] = "В полевом опыте"
        reviews = {r["text_sha256"]: r for r in (a, b, c)}
        with TemporaryDirectory() as temp:
            path = Path(temp)
            case = new_case(path, path, article, "legends", None, None)
            self.assertEqual(execute(case, path, command_provider(None), None, None, reviews, None, quality),
                             "assistant_handoff_pending")
            self.assertEqual(len(case["versions"]), 3)
            reply = {"base_text_sha256": article_digest(c_article), "edits": [
                {"paragraph": 1, "before": b["edits"][0]["after"],
                 "after": "По результатам опыта скорость достигла двух метров в секунду.",
                 "reason": "Уточнён источник измерений."}]}
            self.assertEqual(execute(case, path, command_provider(None), None, None, reviews, reply, quality),
                             "assistant_handoff_pending")
            self.assertEqual([r["stage"] for r in case["versions"]], ["original", "agent", "agent", "assistant"])
            assistant_two = {"base_text_sha256": case["handoff"]["text_sha256"], "edits": [
                {"paragraph": 1, "before": "По результатам опыта скорость достигла двух метров в секунду.",
                 "after": "По данным опыта скорость составила два метра в секунду.",
                 "reason": "Уточнена формулировка опыта."}]}
            self.assertEqual(execute(case, path, command_provider(None), None, None, reviews, assistant_two, quality),
                             "owner_review_pending")
            self.assertEqual([r["stage"] for r in case["versions"]],
                             ["original", "agent", "agent", "assistant", "assistant"])
            with self.assertRaises(ValueError):
                execute(case, path, command_provider(None), None, None, reviews, assistant_two, quality)


class FactTests(unittest.TestCase):
    def test_real_quotes_and_primary_document_required(self):
        article = draft()
        report = {"text_sha256": article_digest(article), "claims": [
            {"id": "a", "paragraph": 1, "quote": "пяти метров в секунду"}], "verdicts": [
            {"id": "a", "verdict": "disputed", "reason": "Скорости расходятся.",
             "evidence": [{"url": URL, "quote": "two metres per second"}]}]}
        provider = command_provider(None)
        doc = {"url": URL, "title": "Field report", "text": SOURCE, "kind": "primary"}
        self.assertEqual(fact_check(article, provider, documents=[doc], reviewed=report)["claims"][0]["verdict"], "disputed")
        self.assertEqual(fact_check(article, provider, documents=[{**doc, "kind": "secondary"}],
                                    reviewed=report)["status"], "fact_check_pending")
        self.assertEqual(fact_check(article, provider, documents=[{**doc, "text": SOURCE.replace("two", "one")}],
                                    reviewed=report)["status"], "fact_check_pending")
        self.assertEqual(fact_check(article, provider, reviewed=report)["status"], "fact_check_pending")

    def test_europe_pmc_reads_abstract_and_skips_retracted_entries(self):
        row = {"pmcid": "PMC123", "title": "Measurement", "abstractText": f"<p>{SOURCE}</p>",
               "pubTypeList": {"pubType": ["research-article"]}}
        removed = {**row, "pmcid": "PMC124", "pubTypeList": {"pubType": ["Retracted Publication"]}}

        def open_url(url, timeout):
            self.assertIn("query=particle+velocity", url)
            return BytesIO(json.dumps({"resultList": {"result": [row, removed]}}).encode())

        result = europe_pmc("discover", {"topic": "Частицы", "search_query": "particle velocity"},
                            open_url=open_url)
        self.assertEqual(result["documents"], [{"url": "https://europepmc.org/articles/PMC123",
                                                "title": "Measurement", "kind": "primary",
                                                "scope": "abstract", "text": SOURCE}])


class HoldoutTests(unittest.TestCase):
    def test_paired_qualitative_metrics_and_no_overlap(self):
        a = {"facts": 1, "language": 2, "originality": 0, "story_logic": 0}
        b = {**a, "facts": 0, "language": 3}
        manifest = {"schema": "zen-editorial-holdout-v1", "reviewer": "owner", "blinded": True,
                    "experiment": "prompt-v2", "samples": [
                        {"slug": "science-holdout", "channel": "science",
                         "baseline": {"text_sha256": "a" * 64, "defects": a},
                         "candidate": {"text_sha256": "b" * 64, "defects": b}}]}
        result = evaluate_holdout(manifest, [])
        self.assertTrue(result["regression_detected"])
        self.assertEqual(result["genres"]["science"]["baseline_facts"], 1)
        with self.assertRaisesRegex(ValueError, "overlaps"):
            evaluate_holdout(manifest, [{"slug": "other", "source_sha256": "a" * 64,
                                         "reviewed_sha256": "c" * 64}])


class LengthStandardTests(unittest.TestCase):
    def test_visible_body_and_both_inclusive_bounds(self):
        from editorial_length import count_characters, length_finding
        self.assertEqual(count_characters("<head><title>meta</title></head><p data-note='source'>А &amp; Б&nbsp;\t\n"
                                          "<img alt='not rendered'> В</p><!--note-->"
                                          "<style>.css</style><script>secret</script><template>sample</template>"), 4)
        for count, status in ((2999, "short_article"), (3000, None),
                              (6000, None), (6001, "long_article")):
            self.assertEqual(length_finding(count_characters(f"<p>{'Я' * count}</p>")), status)

    def test_volume_repairs_expand_or_trim_and_preserve_the_ending(self):
        from editorial_length import count_characters, length_finding
        from editorial_semantic import resize_article
        ending = "<p>Подпишитесь на канал.</p>"
        for start, direction, reply in (
            (2700, "expand", {"paragraphs": ["Новая сцена. " * 31]}),
            (6200, "trim", {"replacements": [{"paragraph": 1, "after": "Б" * 5650}]}),
        ):
            article = {**draft(), "body_html": f"<p>{'Я'*start}</p>{ending}"}

            def provider(task, request):
                self.assertEqual((task, request["mode"], request["direction"]), ("revise", "resize", direction))
                return reply

            result, changes = resize_article(article, provider)
            self.assertTrue(result["body_html"].endswith(ending))
            self.assertIsNone(length_finding(count_characters(result["body_html"])))
            self.assertIn(str(count_characters(article["body_html"])), changes[0])

    def test_repair_uses_the_same_budget_and_each_edit_is_rechecked(self):
        from editorial_length import count_characters, length_finding
        article = {**draft("legends"), "body_html": "<p>" + "Я" * 2760 + "</p><p>Концовка.</p>"}
        actions = []

        def provider(task, payload):
            if task == "diagnose":
                return {"issues": []}
            actions.append(task)
            return {"paragraphs": ["Новая сцена. " * 31]}

        def checker(version, links):
            report = quality(version, links)
            finding = length_finding(count_characters(version["body_html"]))
            report["findings"] = [finding] if finding else []
            return report

        with TemporaryDirectory() as temp:
            path = Path(temp)
            case = new_case(path, path, article, "legends", None, None)
            self.assertEqual(execute(case, path, provider, None, None, {}, None, checker),
                             "owner_review_pending")
            self.assertEqual(actions, ["revise"])
            self.assertEqual(len(case["versions"]), 2)
            self.assertEqual(case["versions"][1]["rule"], "article_length")
            self.assertEqual(case["versions"][1]["report"]["findings"], [])
            self.assertEqual(execute(case, path, provider, None, None, {}, None, checker),
                             "owner_review_pending")
            self.assertEqual(actions, ["revise"])


class LocalLlmTests(unittest.TestCase):
    def test_loopback_only_and_validated_retries(self):
        from editorial_local_llm import ProviderFailure, complete
        request = {"task": "write", "instruction": "Write a draft"}
        calls = []

        def send(endpoint, path, payload, timeout):
            calls.append(path)
            self.assertEqual(endpoint.hostname, "127.0.0.1")
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            return {"choices": [{"message": {"content": json.dumps(
                {} if len(calls) == 1 else {"title": "Тест", "paragraphs": ["Абзац"], "cta": "Подписаться"}
            )}, "finish_reason": "stop"}]}

        result = complete(request, env={"ZEN_LLM_MODEL": "test"}, send=send)
        self.assertEqual(result["title"], "Тест")
        self.assertEqual(calls, ["/v1/chat/completions"] * 2)
        with self.assertRaisesRegex(ProviderFailure, "loopback"):
            complete(request, env={"ZEN_LLM_MODEL": "test", "ZEN_LLM_URL": "http://example.org:8093"}, send=send)

    def test_ollama_returns_exact_claim_and_source_fragments(self):
        from editorial_local_llm import complete
        llm_answer = {"claims": [{"id": "c1", "sentence": 2}], "search_query": "velocity"}

        def send(endpoint, path, payload, timeout):
            self.assertEqual(path, "/api/chat")
            self.assertIn("format", payload)
            return {"message": {"content": json.dumps(llm_answer)}, "done_reason": "stop"}

        environment = {"ZEN_LLM_BACKEND": "ollama", "ZEN_LLM_MODEL": "test"}
        result = complete({"task": "extract_claims", "instruction": "Find claims", "paragraphs": [
            {"paragraph": 1, "text": "Вступление. Скорость равнялась двум метрам в секунду."},
        ]}, env=environment, send=send)
        self.assertEqual(result["claims"][0]["quote"], "Скорость равнялась двум метрам в секунду.")

        llm_answer = {"verdicts": [{"id": "c1", "verdict": "confirmed", "reason": "Совпадает",
                                    "evidence_ids": [1]}]}
        result = complete({"task": "compare_claims", "instruction": "Compare evidence",
                           "documents": [{"title": "Field report", "url": URL, "text": SOURCE,
                                          "kind": "primary"}], "claims": result["claims"]},
                          env=environment, send=send)
        self.assertEqual(result["verdicts"][0]["evidence"], [{"url": URL, "quote": SOURCE}])
