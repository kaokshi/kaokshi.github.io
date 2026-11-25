"""Synthetic offline checks for the private editorial evaluation."""
import base64
from decimal import Decimal
import unittest
from unittest.mock import patch

from src import editorial_routerai_two_articles as two


class TwoArticleTest(unittest.TestCase):
    def test_selected_models_and_caps(self):
        self.assertEqual(two.MODELS, {
            "brain": "qwen/qwen3.6-plus",
            "draft": "anthropic/claude-sonnet-5.5",
            "edit": "deepseek/deepseek-v4-pro-0813",
            "image": "bytedance-seed/seedream-5-0-flash",
        })
        self.assertEqual(set(two.PILOT_CHANNELS), {"legends", "science"})
        self.assertEqual((two.BUDGET, two.DAILY, two.MONTHLY), (70, 300, 9000))

    def test_private_storage_failure_prevents_spend(self):
        with patch.object(two, "Spend") as spend:
            with self.assertRaisesRegex(two.UnsafeToTest, "private_storage_secret_missing"):
                two.evaluate("routerai-validation/123", "", "synthetic", {})
            spend.assert_not_called()

    def test_private_repo_visibility_checked_before_writing(self):
        with patch.object(two, "urlopen") as transport, patch.object(
                two.json, "load", return_value={"full_name": two.PRIVATE_REPO, "visibility": "public"}):
            with self.assertRaisesRegex(two.UnsafeToTest, "visibility_unverified"):
                two.github_private("routerai-validation/123", "synthetic", "test.json", b"fixture")
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(transport.call_args.args[0].get_method(), "GET")

    def test_budget_blocks_payment_when_reserve_would_be_breached(self):
        report = {"calls": []}
        spend = two.Spend.__new__(two.Spend)
        spend.token, spend.report = "synthetic", report
        spend.initial, spend.total = Decimal(100), Decimal(60)
        with patch.object(two, "credits", return_value=Decimal(40)), patch.object(two, "call") as api:
            with self.assertRaisesRegex(two.UnsafeToTest, "next_stage_forecast"):
                spend.request("science", "image", "image", "/images", {}, Decimal(3), reserve=Decimal(8))
            api.assert_not_called()
            self.assertEqual(report["calls"], [])

    def test_usage_budget_stops_when_balance_has_not_updated(self):
        report = {"calls": []}
        spend = two.Spend.__new__(two.Spend)
        spend.token, spend.report = "synthetic", report
        spend.initial, spend.total = Decimal(100), Decimal(68)
        with patch.object(two, "credits", return_value=Decimal(100)), patch.object(
                two, "call", return_value={"usage": {"cost": 3}, "data": [{}]}):
            with self.assertRaisesRegex(two.UnsafeToTest, "actual_spend_outside_estimate"):
                spend.request("science", "image", "image", "/images", {}, Decimal(2))
        self.assertEqual(report["calls"][0]["cost_rub"], "3")
        self.assertEqual(report["calls"][0]["images"], 1)

    def test_paid_bytes_survive_a_post_request_budget_stop(self):
        spend = two.Spend.__new__(two.Spend)
        spend.token, spend.report = "synthetic", {"calls": []}
        spend.initial, spend.total = Decimal(100), Decimal(69)
        save = []
        answer = {"usage": {"cost": "1.05"},
                  "data": [{"b64_json": base64.b64encode(b"paid bytes").decode()}]}
        with patch.object(two, "credits", side_effect=[Decimal(31), Decimal("29.95")]), patch.object(
                two, "call", return_value=answer):
            with self.assertRaisesRegex(two.UnsafeToTest, "actual_spend_outside_estimate"):
                spend.request("science", "image", "image", "/images", {}, Decimal(1),
                              retain_image=save.append)
        self.assertEqual(save, [b"paid bytes"])
        self.assertTrue(spend.report["calls"][0]["image_retained"])
        self.assertEqual(spend.total, Decimal("70.05"))

    def test_exact_tariff_accepts_balance_rounding_noise(self):
        spend = two.Spend.__new__(two.Spend)
        spend.token, spend.report = "synthetic", {"calls": []}
        spend.initial, spend.total = Decimal(100), Decimal(0)
        with patch.object(two, "credits", side_effect=[Decimal(100), Decimal("98.07771807999999")]), patch.object(
                two, "call", return_value={"usage": {"cost": "1.92228192"}, "data": [{}]}):
            spend.request("science", "image", "image", "/images", {}, Decimal("1.92228192"))
        self.assertEqual(spend.total, Decimal("1.92228192"))

    def test_scene_anchor_must_be_from_the_assigned_section(self):
        rows = [{"subject": "Subject", "brief": "Cover scene", "anchor": "Short intro"},
                {"subject": "Subject", "brief": "Section scene", "anchor": "Other section"}]
        with self.assertRaisesRegex(two.UnsafeToTest, "image_plan_anchor_outside_section"):
            two.plan_images({"style": "Style", "images": rows}, [{"paragraphs": ["Actual section"]}])

    def test_private_preview_keeps_missing_slots_visible(self):
        article = {"intro": ["Вступление."],
                   "sections": [{"heading": "Раздел", "paragraphs": ["Текст."]}]}
        preview = two.render(article, "Заголовок", None, {}, [], private_preview=True)
        self.assertEqual(preview.count("Иллюстрация ожидает"), 2)
        self.assertIn("выпуск не согласован", preview)

    def test_visual_approval_requires_evidence_and_preserves_hard_failures(self):
        approved = {"passed": True, "issues": [], "evidence": "A visible detail"}
        self.assertTrue(two.visual_passes(approved, [], has_pixels=True))
        self.assertFalse(two.visual_passes(approved, ["image_duplicate"], has_pixels=True))
        self.assertFalse(two.visual_passes(approved, [], has_pixels=False))
        self.assertFalse(two.visual_passes({**approved, "evidence": ""}, [], has_pixels=True))

    def test_layout_places_illustrations_after_headings(self):
        sections = [{"heading": f"Блок {i}", "paragraphs": [f"Строка {i}."]} for i in range(6)]
        article = {"intro": ["Вступление."], "sections": sections}
        rows = [{"section": max(1, i), "file": f"image-{i}.jpg", "sha256": str(i),
                 "cover_score": int(i == 0)} for i in range(7)]
        cover, pictures = two.arrange(article, rows)
        output = two.render(article, "Заголовок", cover, pictures, [])
        self.assertLess(output.index("image-0.jpg"), output.index("Вступление."))
        for i in range(6):
            self.assertIn(f"**Блок {i}**\n![ИИ-иллюстрация](images/image-{i + 1}.jpg)", output)


if __name__ == "__main__":
    unittest.main()
