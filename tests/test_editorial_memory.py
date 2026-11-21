import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_memory import choose_topic, experience, recalled_provider, remember
from editorial_agent_v1 import article_digest
from editorial_writer import recover_numbered_prose
from editorial_zen_import import analyze


class PersistentExperience(unittest.TestCase):
    def test_genre_scope_and_daily_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article = dict(slug="m",channel="legends",title="Пример",body_html="<p>start:1</p><p>Абзац.</p>")
            case = {"versions":[{"stage":"original","article":article,"text_sha256":article_digest(article)}]}
            remember(root,case)
            remember(root,case)
            self.assertEqual(experience(root,"science"), [])
            self.assertEqual(experience(root,"legends")[0]["articles"],1)
            captured=[]
            provider=recalled_provider(root,"legends",lambda t,p: captured.append(p))
            provider("write",{})
            self.assertIn("genre_experience",captured[0])
            choose=lambda t,p: {"topics":["Проверяемый научный вопрос о движении теней", "Ночной маяк на отмели", "Куда пропал сторож вокзала"]}
            self.assertEqual(choose_topic(root,"legends","2026-11-21",choose,["Ночной маяк на отмели"]),"Куда пропал сторож вокзала")
            self.assertEqual(choose_topic(root,"legends","2026-11-21",None,[]),"Куда пропал сторож вокзала")

    def test_lossless_recovery_on_independent_controls(self):
        for delimiter in ("start ", "start:"):
            text="\n".join(f"{delimiter}{n}\nheading: Заголовок\nРеплика {n}." for n in range(1,4))
            self.assertEqual(recover_numbered_prose([text,"cta"]),["Реплика 1.","Реплика 2.","Реплика 3."])
        self.assertEqual(recover_numbered_prose(["start 2\nheading: Это диалог"] ),["start 2\nheading: Это диалог"])

    def test_no_collection_or_import_before_launch(self):
        self.assertEqual(analyze([{}],own=True)["status"],"disabled_before_launch")
        self.assertEqual(analyze()["status"],"authorized_export_not_connected")
        with self.assertRaises(ValueError):
            analyze([{"source":"export","permission":"authorized_metadata_export","body":"private"}])

    def test_reviewed_field_leak_is_measured_on_the_next_raw_answer(self):
        from editorial_memory import writer_experience_report
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            article = dict(slug="story", channel="legends", title="Станция", body_html="<p>start:1</p>")
            case = {"versions": [{"stage": "original", "article": article, "text_sha256": article_digest(article)}]}
            remember(root, case)
            def writer(task, payload):
                self.assertEqual(payload["genre_experience"][0]["observed"], "writer_field_in_prose")
                return {"paragraphs": ["На станции зажгли свет.", "heading: Поезд"]}
            provider = recalled_provider(root, "legends", writer)
            reply = provider("write", {})
            report = writer_experience_report(root, "legends", provider.last_reminders, reply)
            self.assertEqual(report["outcome"], "defect_recurred")
            self.assertEqual(report["prior_observations"][0]["text_sha256"], article_digest(article))
            self.assertEqual(report["field_lines"], 1)
            self.assertFalse(report["prompt_deployed"])

    def test_prose_gate_rejects_realistic_field_streams_and_allows_dialogue(self):
        from editorial_writer import validate_prose_reply
        base = {"title": "Станция", "paragraphs": ["Вера вошла.", "На столе лежал ключ.", "Она ждала."], "cta": "Подпишитесь."}
        validate_prose_reply(base)
        for field in ("start 1", "heading: Поезд", "cta", "История.\nheading: Поезд"):
            with self.assertRaises(ValueError):
                validate_prose_reply(dict(base, paragraphs=[*base["paragraphs"], field]))
        validate_prose_reply(dict(base, paragraphs=[*base["paragraphs"], "На кнопке написано start."]))

    def test_writer_budget_is_durable_after_invalid_json_structure(self):
        from editorial_pipeline import new_case
        from editorial_writer import Pending
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_dir = root / "legends-test"
            case_dir.mkdir()
            calls = []
            def invalid(task, request):
                calls.append(task)
                return {"title": "title", "paragraphs": ["start 1", "Пора.", "Тихо."], "cta": "cta"}
            for _ in range(2):
                with self.assertRaises(Pending):
                    new_case(root, case_dir, None, "legends", "Задание", invalid)
            self.assertEqual(calls, ["write", "write"])
