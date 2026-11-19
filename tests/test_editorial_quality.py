"""No-network, no-model tests for all three channel-specific quality inspections."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import editorial_quality as q


class QualityTests(unittest.TestCase):
    def test_repeated_words_and_syntax(self):
        report = q.text_style("Дом стоял за лесом. " + "Дорога " * 10)
        self.assertIn("дорога", [x["word"] for x in report["repeated_words"]])

    def test_science_needs_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "science.json"
            path.write_text(json.dumps({"slug": "science-xx", "channel": "science",
                                        "body_html": "<p>Наука.</p>", "image_files": []}), encoding="utf-8")
            with patch.object(q, "ROOT", Path(tmp)):
                report = q.inspect_article(path)
            self.assertIn("science_sources_not_recorded", report["warnings"])

    def test_cinema_source_traceability_is_mandatory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cinema.json"
            path.write_text(json.dumps({"slug": "cinema-xx", "channel": "cinema",
                                        "body_html": "<p>Фильм.</p>", "image_files": ["frame.jpg"],
                                        "image_sources": [], "rights_reviewed": False}), encoding="utf-8")
            with patch.object(q, "ROOT", Path(tmp)):
                report = q.inspect_article(path)
            self.assertIn("film_still_source_traceability_incomplete", report["warnings"])
            self.assertIn("film_still_rights_unverified", report["warnings"])

    def test_fiction_reality_claim_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legends.json"
            path.write_text(json.dumps({"slug": "story-xx", "channel": "legends",
                                        "body_html": "<p>Основано на реальных событиях.</p>"}), encoding="utf-8")
            report = q.inspect_article(path)
            self.assertIn("fiction_claim_of_reality_review", report["warnings"])

    def test_internal_duplicate_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name in ("a", "b"):
                path = Path(tmp) / (name + ".json")
                path.write_text(json.dumps({"slug": name, "title": "Одинаковое название", "body_html": "<p>Слова и буквы.</p>"}, ensure_ascii=False), encoding="utf-8")
                paths.append(path)
            self.assertTrue(q.similarity_report(paths))

    def test_real_publication_flag_never_modified(self):
        self.assertFalse(q.inspect_article(ROOT / "content/articles/cinema-01-truman-show-real-photos.json")["publication_authorized"])


if __name__ == "__main__":
    unittest.main()
