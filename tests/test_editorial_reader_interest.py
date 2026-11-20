"""Reader-interest safeguards and optional real ruTS integration."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import editorial_reader_interest as reader


class ReaderInterestTests(unittest.TestCase):
    def test_quoted_advisories_and_no_publication_gate(self):
        a={"slug":"legends-test","channel":"legends",
           "body_html":"<p>В современном мире перед читателем возникает вопрос.</p>"
                       "<p>В рамках данного проекта произошло событие.</p>"}
        r=reader.analyze(a)
        self.assertEqual(r["slug"],a["slug"])
        self.assertFalse(r["is_publication_gate"])
        self.assertFalse(r["is_authorship_detector"])
        names={x["rule"] for x in r["editorial_guidance"]["findings"]}
        self.assertIn("empty_introduction",names)
        self.assertIn("bureaucratic_phrase",names)
        self.assertTrue(all(x.get("quote") for x in r["editorial_guidance"]["findings"]))

    def test_concrete_natural_prose_does_not_get_fabricated_defects(self):
        a={"slug":"science-test","channel":"science",
           "body_html":"<p>После грозы фотограф заметил красную вспышку над облаком.</p>"}
        r=reader.analyze(a)
        self.assertEqual(r["editorial_guidance"]["findings"],[])
        self.assertIn(r["ruts"]["status"],{"completed","not_installed","error"})

    def test_sha_changes_on_edit(self):
        a={"slug":"legends-test","channel":"legends","body_html":"<p>Вокзал замолчал.</p>"}
        first=reader.analyze(a)["text_sha256"]
        a["body_html"]="<p>Вокзал внезапно замолчал.</p>"
        self.assertNotEqual(first,reader.analyze(a)["text_sha256"])

    def test_no_fake_metric_when_dependency_missing(self):
        with patch.object(reader,"ruts_metrics",return_value={"status":"not_installed"}):
            result=reader.analyze({"slug":"x","channel":"legends","body_html":"<p>Текст.</p>"})
        self.assertEqual(result["ruts"]["status"],"not_installed")
        self.assertNotIn("lexical_ttr",result["ruts"])

if __name__=="__main__":
    unittest.main()
