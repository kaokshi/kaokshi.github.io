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
            choose=lambda t,p: {"topics":["Ночной маяк на отмели", "Куда пропал сторож вокзала"]}
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
