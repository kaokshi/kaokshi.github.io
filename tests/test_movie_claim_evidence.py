"""Bounded offline movie-claim and image-alignment regression tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from movie_claim_evidence import audit_claims
from movie_image_alignment import match_images
from editorial_semantic import paragraphs

TEXT = "В ночном городе сыщик заметил пустой вагон и решил открыть дверь."
ARTICLE = {"channel": "cinema", "slug": "cinema-test", "title": "Пример",
           "body_html": "<p>" + TEXT + "</p><p>Второй герой спрятал красную папку в подвале.</p>"}
DOCS = [{"url": "https://ru.wikipedia.org/wiki/Test_Film",
         "title": "Сюжет фильма Пример",
         "kind": "secondary", "text": TEXT + " Затем мужчина ушёл по тоннелю."}]


def provider_ok(task, request):
    if task == "extract_claims":
        return {"claims": [{"id": "c1", "paragraph": 1, "quote": TEXT}],
                "search_query": ""}
    if task == "compare_claims":
        return {"verdicts": [{"id": "c1", "verdict": "confirmed", "reason": "retrieved plot",
                              "evidence": [{"url": DOCS[0]["url"], "quote": TEXT}]}]}
    raise AssertionError("Unexpected model task")


class CinemaClaimAuditTests(unittest.TestCase):
    def test_exact_citation_is_not_called_film_verification(self):
        result = audit_claims(ARTICLE, DOCS, provider_ok)
        self.assertEqual(result["status"], "source_comparison_completed_not_film_verified")
        self.assertEqual(result["claims"][0]["status"], "secondary_text_support_not_verified")
        self.assertFalse(result["film_scene_verified"])
        self.assertFalse(result["publication_authorized"])

    def test_fake_quote_in_secondary_source_rejected(self):
        def fabricated(task, request):
            reply = provider_ok(task, request)
            if task == "compare_claims":
                reply["verdicts"][0]["evidence"][0]["quote"] = "Сыщик встретил пришельцев и улетел."
            return reply
        result = audit_claims(ARTICLE, DOCS, fabricated)
        self.assertEqual(result["status"], "fact_check_pending")
        self.assertFalse(result["publication_authorized"])

    def test_unquoted_movie_claim_rejected(self):
        def invented(task, request):
            if task == "extract_claims":
                return {"claims": [{"id": "c1", "paragraph": 1,
                                    "quote": "Фальшивый вымышленный поворот сюжета"}]}
            return provider_ok(task, request)
        self.assertEqual(audit_claims(ARTICLE, DOCS, invented)["status"], "fact_check_pending")

    def test_missing_plot_source_remains_pending(self):
        self.assertEqual(audit_claims(ARTICLE, [], provider_ok)["status"], "fact_check_pending")

    def test_disagreement_needs_review_not_auto_correction(self):
        def contrary(task, request):
            reply = provider_ok(task, request)
            if task == "compare_claims":
                reply["verdicts"][0]["verdict"] = "disputed"
            return reply
        result = audit_claims(ARTICLE, DOCS, contrary)
        self.assertEqual(result["claims"][0]["status"], "source_disagreement_needs_review")

    def test_only_one_verdict_per_id(self):
        def repeated(task, request):
            reply = provider_ok(task, request)
            if task == "compare_claims":
                reply["verdicts"].append(dict(reply["verdicts"][0]))
            return reply
        self.assertEqual(audit_claims(ARTICLE, DOCS, repeated)["status"], "fact_check_pending")


class CinemaImagesTests(unittest.TestCase):
    def test_plausible_caption_matches_paragraph_but_is_not_approved(self):
        bundle = {"candidates": [
            {"title": "File:Ночной город пустой вагон.jpg",
             "description": "Ночной город и пустой вагон",
             "source_page": "https://commons.wikimedia.org/wiki/File:night.jpg",
             "candidate_only": True, "license_verified": False},
            {"title": "File:Собака в парке.jpg", "description": "Собака в парке",
             "source_page": "https://commons.wikimedia.org/wiki/File:dog.jpg",
             "candidate_only": True},
        ]}
        result = match_images(ARTICLE, bundle)
        self.assertEqual(result["matches_proposed"], 1)
        self.assertEqual(result["proposals"][0]["suggested_paragraphs"][0]["paragraph"], 1)
        self.assertEqual(result["proposals"][1]["status"], "no_meaningful_text_overlap")
        self.assertFalse(result["all_scene_matches_verified"])
        self.assertFalse(result["proposals"][0]["rights_verified"])

    def test_untrusted_candidate_cannot_become_approved(self):
        bundle = {"candidates": [{
            "title": "File:Ночной город пустой вагон.jpg",
            "description": "Ночной город пустой вагон",
            "source_page": "https://commons.wikimedia.org/wiki/File:test.jpg",
            "candidate_only": False, "editor_approved": True,
            "semantic_match_verified": True, "license_verified": True,
        }]}
        result = match_images(ARTICLE, bundle)
        self.assertEqual(result["candidates_examined"], 0)
        self.assertFalse(result["publication_authorized"])

    def test_duplicate_source_page_cannot_fill_multiple_slots(self):
        row = {"title": "File:Ночной город пустой вагон.jpg",
               "description": "Ночной город пустой вагон",
               "source_page": "https://commons.wikimedia.org/wiki/File:test.jpg",
               "candidate_only": True}
        result = match_images(ARTICLE, {"candidates": [row, row]})
        self.assertEqual(result["candidates_examined"], 1)


if __name__ == "__main__":
    unittest.main()
