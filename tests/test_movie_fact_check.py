"""Offline smoke tests: movie evidence does not hallucinate a verified frame match."""
import json
from pathlib import Path
import sys
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

    def test_semantic_split_and_not_a_truth_verdict(self):
        import movie_semantic as semantic
        passages = semantic.split_passages("Труман выходит из дома. " * 80, max_words=25)
        self.assertGreater(len(passages), 2)
        self.assertTrue(all(len(x.split()) <= 25 for x in passages))
        self.assertEqual(semantic.semantic_candidates(
            [{"section": "Начало", "text": "Труман идёт по городу."}],
            {"status": "unavailable"})["status"], "source_unavailable")

    def test_semantic_ranks_rephrased_evidence_without_claiming_truth(self):
        try:
            __import__("numpy")
        except ImportError:
            self.skipTest("numpy is installed only in the isolated movie workflow")
        import movie_semantic as semantic
        class Dummy:
            def encode(self, texts, **kwargs):
                import numpy as np
                vectors = []
                for text in texts:
                    if "море" in text or "океан" in text:
                        vectors.append([1.0, 0.0])
                    else:
                        vectors.append([0.0, 1.0])
                return np.array(vectors)
        source = {"status": "completed",
                  "extract": "== Сюжет ==\nТруман выходит к океану и отправляется в плавание, пытаясь найти выход из искусственного города. Он управляет лодкой, находясь под наблюдением телезрителей.",
                  "url": "https://ru.wikipedia.org/wiki/Шоу_Трумана"}
        sections = [{"section": "Финал", "text": "Труман выбирается в море, несмотря на трудности. Вскоре он понимает, что все это было снято в искусственном мире."}]
        result = semantic.semantic_candidates(sections, source, model=Dummy())
        self.assertEqual(result["status"], "completed", str(result))
        self.assertEqual(len(result["matches"]), 1)
        self.assertFalse(result["matches"][0]["fact_verified"])
        self.assertTrue(result["matches"][0]["candidate_matches"])

    def test_revision_queue_does_not_rewrite_without_verified_contradiction(self):
        import movie_revision_queue as revisions
        report = {
            "article_slug": "cinema-01-truman-show-real-photos",
            "source_file": "content/articles/cinema-01-truman-show-real-photos.json",
            "article_sections": [{"section": "Финал", "status": "requires_editor_review",
                                  "source_url": "https://ru.wikipedia.org/wiki/Шоу_Трумана"}],
            "semantic_plot_search": {"matches": [{"section": "Финал",
                "candidate_matches": [{"cosine_similarity": 0.99, "source_excerpt": "Труман уходит", "source_url": "https://ru.wikipedia.org/wiki/Шоу_Трумана"}]}]},
            "stills": [{"file": "example.jpg", "article_section": "Финал",
                        "source_url": "https://example.com", "warnings": ["source_caption_differs_from_article"],
                        "visual_scene_match": "not_verified"}],
        }
        result = revisions.build_queue({"title": "Тест"}, report)
        self.assertEqual(result["counts"]["automatically_rewritten"], 0)
        self.assertEqual(result["counts"]["verified_contradictions"], 0)
        self.assertFalse(result["publication_authorized"])
        self.assertIsNone(result["items"][0]["suggested_rewrite"])
        self.assertGreaterEqual(result["counts"]["review_required"], 2)

    def test_source_extractor_reports_evidence_without_fact_verdict(self):
        import editorial_source_extract as src
        rows = src.inspect(["https://example.org/test"],
            fetch=lambda url: ("<html><body><main>Город Трумана " + "сцена " * 45 + "</main></body></html>", url),
            extract=lambda html: "Труман живёт в городе. " * 12)
        self.assertEqual(rows[0]["status"], "extracted")
        self.assertEqual(rows[0]["is_authoritative_for_claim"], "not_evaluated")
        self.assertLessEqual(len(rows[0]["snippet"]), 150)

    def test_source_extractor_rejects_http(self):
        import editorial_source_extract as src
        rows = src.inspect(["http://example.org"], fetch=lambda url: ("", url), extract=lambda html: None)
        self.assertEqual(rows[0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
