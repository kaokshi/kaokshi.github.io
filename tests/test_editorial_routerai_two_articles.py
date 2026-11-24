"""Synthetic offline checks for the private editorial evaluation."""
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
        self.assertEqual(set(two.CHANNELS), {"legends", "science"})
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
