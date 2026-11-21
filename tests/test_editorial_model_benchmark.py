"""Offline checks for privacy, comparability and missing-key handling."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from compare_editorial_models import TASKS, MODEL_IDS, metrics, run, generate

class BenchmarkTests(unittest.TestCase):
    def test_all_models_receive_same_three_channel_briefs(self):
        self.assertEqual(set(TASKS),{"legends","science","cinema"})
        self.assertEqual(set(MODEL_IDS),{"gemini","groq","qwen"})
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
