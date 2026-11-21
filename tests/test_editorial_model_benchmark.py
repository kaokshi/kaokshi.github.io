"""Offline checks for privacy, comparability and missing-key handling."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from compare_editorial_models import TASKS, MODEL_IDS, metrics, run, generate
from editorial_reader_quality import text_stats, quality_flags, review_checklist

class BenchmarkTests(unittest.TestCase):
    def test_all_models_receive_same_three_channel_briefs(self):
        self.assertEqual(set(TASKS),{"legends","science","cinema"})
        self.assertEqual(set(MODEL_IDS),{"gemini","groq","qwen","hybrid"})
        self.assertEqual(len(set(t["title"] for t in TASKS.values())),3)

    def test_cloud_without_secret_is_blocked_not_fake_result(self):
        with TemporaryDirectory() as tmp:
            for backend in ("gemini","groq"):
                result=run(backend,Path(tmp)/backend,env={})
                self.assertEqual(result["generated"],0)
                self.assertEqual(result["status"],"blocked_missing_secret")
                self.assertFalse(result["publication_authorized"])

    def test_mock_generation_is_not_called_real_and_never_marks_factchecked(self):
        mock_body=("Заголовок\n"+
                  "\n".join("**Раздел текста**\n"+("Это проверочный абзац. "*80) for _ in range(5))+
                  "\nПодписывайтесь на канал.")
        calls=[]
        def generator(backend,prompt,env):
            calls.append((backend,prompt))
            return mock_body,{"total_tokens":100}
        with TemporaryDirectory() as tmp:
            result=run("qwen",Path(tmp),generator=generator,env={})
            self.assertEqual(result["generated"],3)
            self.assertTrue(all(not row["fact_check_completed"]
                                for row in result["channels"]))
            self.assertTrue(all(not row["image_rights_verified"]
                                for row in result["channels"]))
            self.assertTrue(all(not row["human_quality_review_completed"]
                                for row in result["channels"]))
        self.assertEqual(len(calls),3)

    def test_hybrid_requires_both_secrets_before_any_generation(self):
        with TemporaryDirectory() as tmp:
            for credentials in ({}, {"GEMINI_API_KEY":"test"},
                                {"GROQ_API_KEY":"test"}):
                def never_run(*args, **kwargs):
                    raise AssertionError("no generation without both keys")
                result=run("hybrid",Path(tmp)/str(len(credentials)),
                           generator=never_run,env=credentials)
                self.assertEqual(result["status"],"blocked_missing_secret")
                self.assertEqual(result["generated"],0)

    def test_hybrid_calls_gemini_first_then_groq_editor_for_each_channel(self):
        mock_body=("Заголовок\\n"+
                  "\\n".join("**Раздел текста**\\n"+("Это проверочный абзац. "*80)
                            for _ in range(5))+
                  "\\nПодписывайтесь на канал.")
        order=[]
        def generator(backend,prompt,env):
            order.append(backend)
            if backend=="groq":
                self.assertTrue("Черновик Gemini" in prompt or "ТЕКУЩИЙ ТЕКСТ:" in prompt)
            return mock_body,{"total_tokens":45}
        with TemporaryDirectory() as tmp:
            result=run("hybrid",Path(tmp),generator=generator,
                       env={"GEMINI_API_KEY":"test","GROQ_API_KEY":"test"})
            self.assertEqual(result["generated"],3)
            self.assertEqual(order[:2],["gemini","groq"])
            self.assertEqual(order.count("gemini"),3)
            self.assertTrue(3<=order.count("groq")<=9)
            self.assertTrue(all(x["workflow"]=="gemini_draft_then_groq_editor"
                                for x in result["channels"]))
            self.assertFalse(result["publication_authorized"])

    def test_repetition_and_structure_flags_are_distinct_from_real_retention(self):
        repeated=("Одинаковая фраза без изменений повторяется снова и снова. "*100)
        article="Название\\n\\n**Первый раздел**\\n"+repeated
        stats=text_stats(article,"legends")
        flags=quality_flags(stats)
        self.assertIn("section_count_outside_4_8",flags)
        self.assertTrue(stats["repeated_fivegrams"]>0)
        self.assertIsNone(stats["observed_reader_retention"])
        self.assertFalse(stats["human_interest_verified"])
        self.assertTrue(len(review_checklist("legends"))>=8)

    def test_hybrid_repairs_failing_draft_until_format_passes(self):
        short="Название\n\n**Часть**\nСлишком короткий текст."
        def unique_word(index):
            return "событие"+chr(1072+index%32)+chr(1072+(index//32)%32)
        sections=[
            "**Раздел "+str(i)+"**\n"+
            " ".join("Разные наблюдения открывали "+unique_word(i*25+j)+"."
                     for j in range(25))
            for i in range(1,6)]
        expanded="Название\n\n"+"\n\n".join(sections)+"\n\nПодписывайтесь на канал."
        # Ensure test fixture itself meets the format gate instead of relying
        # on a model's subjective response.
        self.assertFalse(quality_flags(text_stats(expanded,"legends")),
                         quality_flags(text_stats(expanded,"legends")))
        calls=[]
        def generator(backend,prompt,env):
            calls.append(backend)
            return (short if len(calls)==1 else expanded),{"total_tokens":20}
        with TemporaryDirectory() as tmp:
            result=run("hybrid",Path(tmp),generator=generator,
                env={"GEMINI_API_KEY":"test","GROQ_API_KEY":"test"})
            self.assertEqual(result["generated"],3)
            self.assertTrue(all(x["automated_format_pass"] for x in result["channels"]))
            self.assertTrue(all(x["reader_interest_editorial_review"]=="pending"
                                for x in result["channels"]))
            self.assertTrue(all(x["agent_revisions_used"]<=2 for x in result["channels"]))
            self.assertFalse(result["publication_authorized"])

    def test_failed_repairs_do_not_claim_format_approval(self):
        def generator(backend,prompt,env):
            return "Короткий текст.",{"total_tokens":5}
        with TemporaryDirectory() as tmp:
            result=run("hybrid",Path(tmp),generator=generator,
                env={"GEMINI_API_KEY":"test","GROQ_API_KEY":"test"})
        self.assertEqual(result["generated"],3)
        self.assertTrue(all(not x["automated_format_pass"] for x in result["channels"]))
        self.assertTrue(all(x["agent_revisions_used"]==2 for x in result["channels"]))

    def test_header_and_length_metrics_are_mechanical_only(self):
        result=metrics("**Раздел**\nЗдравствуйте!\nПодписывайтесь.",0.3)
        self.assertFalse(result["length_ok"])
        self.assertFalse(result["fact_check_completed"])
        self.assertEqual(result["bold_section_headers"],1)

    def test_unknown_provider_rejected_and_no_hallucinated_text(self):
        with self.assertRaises(ValueError):
            generate("other","x",env={})

if __name__=="__main__":
    unittest.main()
