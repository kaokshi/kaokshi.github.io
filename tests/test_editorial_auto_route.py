"""Hard-gate and 20-point release routing regression tests; no live publication."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import editorial_auto_route as route


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        images = []
        sources = []
        for i in range(7):
            p = f"content/assets/test{i}.jpg"
            file = self.root / p
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"test image bytes " + str(i).encode())
            images.append(p)
            sources.append({"file": p, "license": "CC0-1.0",
                            "license_verified": True, "source_page": "https://example.org/license/" + str(i)})
        self.article = {
            "slug": "legends-test", "channel": "legends", "status": "draft",
            "title": "Тестовая оригинальная история",
            "body_html": "<p>" + "Ветер " * 900 + "</p>",
            "image_files": images,
            "image_sources": sources,
            "rights_basis": "СС0 со ссылками на авторов и лицензии",
            "auto_release_candidate": True,
            "release_hold": False,
        }
        self.patch = patch.object(route, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.evidence = self.make_evidence(self.article, 5)

    def make_evidence(self, article, rubert):
        dg = route.digest_article(article)
        stamp = {"status": "completed", "article_sha256": dg,
                 "report_ref": "https://github.com/kaokshi/kaokshi.github.io/actions/runs/123"}
        return {
            "ai": {**stamp, "checks": {p: {"status": "completed",
                     "score_percent": rubert if p == "rubert_v4" else 81}
                     for p in route.REQUIRED_AI}},
            "quality": {**stamp, "checks": {p: {"status": "completed"}
                         for p in route.REQUIRED_QUALITY}},
            "factual": {**stamp, "original_fiction_confirmed": True,
                         "fiction_not_presented_as_fact": True},
            "style_warning_count": 0,
            "image_warning_count": 0,
            "source_warning_count": 0,
        }

    def test_passes_all_evidence_as_candidate_without_authorizing(self):
        result = route.decide(self.article, self.evidence)
        self.assertEqual(result["disposition"], "send_for_owner_review")
        self.assertIn("ai_auto_release_threshold_not_validated", result["blockers"])
        self.assertLess(result["risk_score"], 20)
        self.assertFalse(result["publication_authorized"])

    def test_above_score_routes_to_human(self):
        self.evidence["style_warning_count"] = 7
        result = route.decide(self.article, self.evidence)
        self.assertIn("aggregate_risk_above_20", result["blockers"])
        self.assertEqual(result["disposition"], "send_for_owner_review")

    def test_high_rubert_does_not_trigger_unvalidated_ai_rewrite(self):
        self.evidence["ai"]["checks"]["rubert_v4"]["score_percent"] = 99
        result = route.decide(self.article, self.evidence)
        self.assertNotIn("rubert_above_25", result["blockers"])
        self.assertFalse(result["ai_rewrite_required_by_detector"])
        self.assertIsNone(result["ai_rewrite_threshold"])
        self.assertIn("ai_auto_release_threshold_not_validated", result["blockers"])

    def test_missing_evidence_never_passes(self):
        result = route.decide(self.article, {})
        self.assertEqual(result["disposition"], "send_for_owner_review")
        self.assertIn("factual_report_missing_or_stale", result["blockers"])

    def test_modified_text_invalidates_all_reports(self):
        self.article["body_html"] += "<p>Новое сообщение.</p>"
        result = route.decide(self.article, self.evidence)
        self.assertIn("ai_report_missing_or_stale", result["blockers"])

    def test_unverified_rights_are_hard_blocked(self):
        self.article["image_sources"][0]["license_verified"] = False
        self.evidence = self.make_evidence(self.article, 0)
        result = route.decide(self.article, self.evidence)
        self.assertIn("image_license_unverified", result["blockers"])

    def test_cinema_is_paused_even_with_perfect_evidence(self):
        self.article["channel"] = "cinema"
        self.evidence = self.make_evidence(self.article, 0)
        result = route.decide(self.article, self.evidence)
        self.assertIn("channel_paused_or_unknown", result["blockers"])

    def test_science_claim_coverage_required(self):
        self.article["channel"] = "science"
        self.article["claims"] = [{"id": "c1", "source_urls": ["https://doi.org/10.1000/test"]}]
        self.evidence = self.make_evidence(self.article, 0)
        self.evidence["factual"] = {
            "status": "completed",
            "article_sha256": route.digest_article(self.article),
            "report_ref": "https://example.org/report",
            "claim_sources_checked": True,
            "no_unresolved_factual_contradictions": True,
            "verified_claim_ids": ["c1"],
        }
        self.assertIn("ai_auto_release_threshold_not_validated",
                      route.decide(self.article, self.evidence)["blockers"])
        self.evidence["factual"]["verified_claim_ids"] = []
        self.assertIn("science_claim_coverage_missing",
                      route.decide(self.article, self.evidence)["blockers"])

    def test_comparison_models_do_not_get_unscientific_threshold(self):
        self.evidence["ai"]["checks"]["modernbert"]["score_percent"] = 99
        self.evidence["ai"]["checks"]["lightgbm"]["score_percent"] = 99
        self.assertEqual(route.decide(self.article, self.evidence)["disposition"],
                         "send_for_owner_review")
        self.evidence["ai"]["checks"]["lightgbm"]["status"] = "error"
        self.assertIn("ai_check_incomplete", route.decide(self.article, self.evidence)["blockers"])


if __name__ == "__main__":
    unittest.main()
