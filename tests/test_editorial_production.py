"""Exercise editorial quality and recovery with deterministic fixtures."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


from src import editorial_cinema, editorial_images, editorial_review
from src.editorial_learning import CATEGORIES, evaluate_cases
from src.editorial_memory import Memory, digest, read_json
from src.editorial_http import Blocked, ProviderError
from src.editorial_review import CTA, fact_evidence, parse_article, structural_checks


def draft(channel):
    sections = []
    for number in range(1, 8):
        paragraphs = [
            f"Раздел {channel} {number}. " + " ".join(f"подробность{number}{n}" for n in range(60))
        ]
        if channel == "science":
            # Numeric claims are tested independently. Use stable letters for the prose.
            paragraphs = [p.translate(str.maketrans("0123456789", "абвгдежзик")) for p in paragraphs]
        if number == 7:
            paragraphs.append("Если вам близка эта тема, подпишитесь на канал.")
        sections.extend([f"**Смысловая часть {chr(1040 + number)}**", *paragraphs])
    value = "\n\n".join(sections)
    # Use about 4000 visible characters with distinct paragraphs.
    return value.replace("подробность", "деталь")


class ProductionTests(unittest.TestCase):
    def test_existing_truman_show_evidence_and_still_provenance_fail_closed(self):
        repo = Path(__file__).resolve().parents[1]
        existing = read_json(repo / "content/articles/cinema-01-truman-show-real-photos.json")
        ref = read_json(repo / "config/movie_reference_index.json")[existing["slug"]]
        self.assertEqual(ref["imdb_id"], "tt0120382")
        official = "https://www.paramount.com/movies/the-truman-show"
        plan = {"film_title": "Шоу Трумана", "year": 1998, "format": "retelling",
                "spoilers": True, "sources": [official]}
        with self.assertRaisesRegex(Blocked, "film_original_release_before_minimum"):
            editorial_cinema.evidence(plan, {}, resolver=lambda *_: self.fail("resolved an ineligible film"))
        film = {"qid": "Q214801", "year": ref["original_year"], "original_title": ref["film_title"]}
        class Index:
            class memory:
                time_left = staticmethod(lambda: 1000)
        with self.assertRaisesRegex(Blocked, "no_verified_official_still_for_scene"):
            editorial_cinema.stills({"subject": "Truman in Seahaven"}, "Truman Show", None,
                                    Index(), film, {})

    def test_unsupported_claim_cannot_be_covered_by_an_unrelated_fragment(self):
        text = "Наблюдение началось в 1998 году. Объект находился рядом."
        actual, issue = fact_evidence(text, {"claims": [
            {"claim": "Объект находился рядом", "source": "S1", "quote": "Достоверная цитата о положении объекта",
             "verdict": "supported"}]},
            [{"url": "https://example.org", "status": "accessible",
              "text": "Достоверная цитата о положении объекта."}])
        self.assertEqual(len(actual), 1)
        self.assertTrue(any(line.startswith("unsupported_numeric_claim") for line in issue))

    def test_actual_check_runner_reports_incomplete_on_unavailable_tools(self):
        value = draft("legends")
        checker = {"status": "error", "reason": "child process not found"}
        with patch.object(editorial_review, "measure_text", return_value={"modernbert": checker}), \
                patch.object(editorial_review, "grammar",
                             return_value={"status": "unavailable", "reason": "LanguageTool"}):
            report = editorial_review.qa(value, "legends", [], [], lambda *a: {"issues": []},
                                         lambda: 3600)
        self.assertEqual(report["detectors"]["modernbert"]["status"], "unavailable")
        self.assertEqual(report["model_a"]["status"], "incomplete")
        self.assertEqual(report["grammar"]["status"], "unavailable")
        self.assertEqual(report["text_sha256"], digest(editorial_review.canonical(parse_article(value))))

    def test_cover_selection_uses_scores_and_retains_illustrated_sections(self):
        pictures = [{"file": "first.jpg", "section": 1, "cover_score": .1, "sha256": "a"},
                    {"file": "only-for-this-section.jpg", "section": 2, "cover_score": .9, "sha256": "b"},
                    {"file": "cover.jpg", "section": 3, "cover_score": .8, "sha256": "c"},
                    {"file": "other.jpg", "section": 3, "cover_score": .3, "sha256": "d"}]
        self.assertEqual(editorial_images.select_cover(pictures), "cover.jpg")

    def test_corrupt_image_cannot_be_accepted(self):
        with TemporaryDirectory() as directory:
            memory = Memory(Path(directory), "2026-11-22", datetime.now(timezone.utc), {"daily_calls": {"fixture_a": 2, "fixture_b": 2}, "per_article_calls": {"fixture_a": 2, "fixture_b": 2}})
            index = editorial_images.ImageIndex(memory, [])
            with self.assertRaises(OSError):
                index.save(b"not an image", {}, {"subject": "Figure"}, "Theme", Path(directory) / "a.jpg")

    def test_cta_and_structure(self):
        self.assertTrue(all(CTA.search(x) for x in ("Подписывайтесь!", "Подпишитесь на канал.",
                                                    "Вы можете подписаться.", "Следите за каналом.")))
        self.assertFalse(CTA.search("Тут есть канал и подписи."))
        report = structural_checks(parse_article("Вступление\n\n**Сцена**\n\nТекст"), [])
        self.assertIn("missing_cta", report["issues"])
        self.assertIn("section_structure", report["issues"])

    def test_429_stops_only_affected_provider_and_persists(self):
        with TemporaryDirectory() as directory:
            cutoff = datetime.now(timezone.utc) + timedelta(hours=2)
            path = Path(directory)
            memory = Memory(path, "2026-11-22", cutoff, {"daily_calls": {"fixture_a": 2, "fixture_b": 2}, "per_article_calls": {"fixture_a": 2, "fixture_b": 2}})
            with self.assertRaisesRegex(Blocked, "http_429"):
                memory.task("legends-01", "image", "fixture_a", {"n": 1}, lambda task:
                    memory.call(task, "legends-01", "fixture_a", lambda: fail("http_429_quota_or_rate_limit")))
            restarted = Memory(path, "2026-11-22", cutoff, {"daily_calls": {"fixture_a": 2, "fixture_b": 2}, "per_article_calls": {"fixture_a": 2, "fixture_b": 2}}, retry=True)
            with self.assertRaisesRegex(Blocked, "provider_stopped"):
                restarted.call("t2", "legends-01", "fixture_a", lambda: self.fail("repeated an exhausted route"))
            self.assertEqual(restarted.call("t3", "science-01", "fixture_b", lambda: {"text": "ok"})["text"], "ok")
            self.assertEqual(len(restarted.data["calls"]), 2)

    def test_deadline_stops_before_provider(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            memory = Memory(path, "2026-11-22", datetime.now(timezone.utc) - timedelta(seconds=30), {"daily_calls": {"fixture_a": 2, "fixture_b": 2}, "per_article_calls": {"fixture_a": 2, "fixture_b": 2}})
            with self.assertRaisesRegex(Blocked, "production_deadline"):
                memory.task("science-01", "draft", "fixture_b", {},
                            lambda _: self.fail("late provider call"))
            self.assertEqual(memory.data["calls"], [])

    def test_verified_learning_detects_regression_and_overlap(self):
        def case(i, partition, regression=False):
            return {"channel": "legends", "issue": "missing_cta", "article_id": f"article-{i}",
                    "owner_verified": True, "provenance": "verified owner review", "partition": partition,
                    "candidate_instruction": CATEGORIES["missing_cta"], "baseline_resolved": False,
                    "candidate_resolved": True, "regression": regression,
                    **{k: digest(f"{i}.{k}") for k in ("text_sha256", "source_sha256",
                                                       "baseline_sha256", "candidate_sha256")}}
        development = [case(i, "development") for i in range(20)]
        holdout = [case(i, "holdout") for i in range(20, 25)]
        report = evaluate_cases(development + holdout + [case(2, "holdout")], 20)
        self.assertEqual(report["proposals"][0]["status"], "candidate_improved")
        self.assertEqual(report["proposals"][0]["held_out_cases"], 5)
        holdout[0]["regression"] = True
        self.assertEqual(evaluate_cases(development + holdout, 20)["proposals"][0]["status"],
                         "rejected_regression")


def fail(code):
    raise ProviderError(code, stop=True)


if __name__ == "__main__":
    unittest.main()
