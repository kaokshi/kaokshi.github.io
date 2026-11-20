"""Offline smoke tests: movie evidence does not hallucinate a verified frame match."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import movie_fact_check as movie


class MovieFactCheckTests(unittest.TestCase):
    def test_film_article_sections_and_captions(self):
        path = ROOT / "content" / "articles" / "cinema-01-truman-show-real-photos.json"
        article = json.loads(path.read_text(encoding="utf-8"))
        sections, images = movie.sections(article["body_html"])
        self.assertGreaterEqual(len(sections), 4)
        self.assertEqual(len(images), 8)
        self.assertTrue(images[0].get("caption"))

    def test_all_truman_stills_remain_unverified_visually(self):
        path = ROOT / "content" / "articles" / "cinema-01-truman-show-real-photos.json"
        article = json.loads(path.read_text(encoding="utf-8"))
        _, images = movie.sections(article["body_html"])
        reference = movie.load_reference("cinema-01-truman-show-real-photos")
        result = movie.stills(article, images, reference)
        self.assertEqual(len(result), 8)
        self.assertTrue(all(x["visual_scene_match"] == "not_verified" for x in result))
        self.assertTrue(all(x["image_rights"] == "not_verified_by_this_tool" for x in result))

    def test_mismatched_imdb_still_is_flagged(self):
        article = {"image_files": ["some.jpg"], "image_sources": [
            {"file": "some.jpg", "source_page": "https://www.imdb.com/title/tt9999999/mediaviewer/123"}]}
        result = movie.stills(article, [{"src": "/assets/some.jpg", "caption": "Frame"}], {"imdb_id": "tt0120382"})
        self.assertIn("imdb_title_id_conflict", result[0]["warnings"])

    def test_source_unavailable_does_not_validate_plot(self):
        result = movie.comparison([{"section": "Начало", "text": "Труман"}],
                                  {"status": "unavailable", "url": "https://example.org"})
        self.assertEqual(result[0]["status"], "source_unavailable")

    def test_report_does_not_modify_article_or_release_status(self):
        path = ROOT / "content" / "articles" / "cinema-01-truman-show-real-photos.json"
        before = path.read_bytes()
        with patch.object(movie, "fetch_wikipedia", return_value={
             "status": "completed", "extract": "Труман Бёрбанк снимается в шоу и не знает об этом. " * 8,
             "url": "https://ru.wikipedia.org/wiki/Шоу_Трумана"}):
            report = movie.analyze(path, use_imdb=False)
        self.assertFalse(report["summary"]["film_plot_verified"])
        self.assertFalse(report["summary"]["frame_scene_match_verified"])
        self.assertFalse(report["summary"]["article_authorized_for_publication"])
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
