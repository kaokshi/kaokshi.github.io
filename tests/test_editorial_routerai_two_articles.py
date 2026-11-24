"""Offline checks; never access RouterAI or write real private content."""
import unittest
from unittest.mock import patch
from src import editorial_routerai_two_articles as two


class TwoArticleTest(unittest.TestCase):
    def test_models_are_stable_and_production_is_not_changed(self):
        self.assertEqual(two.MODELS, {
            "brain": "qwen/qwen3.6-plus",
            "draft": "anthropic/claude-sonnet-5.5",
            "edit": "deepseek/deepseek-v4-pro-0813",
        })
        self.assertEqual(len(two.TESTS), 2)
        self.assertEqual({x["channel"] for x in two.TESTS}, {"legends", "science"})
        self.assertLessEqual(two.BUDGET, 45)

    def test_metrics_require_length_headings_and_channel_cta(self):
        txt = ("**Первый блок**\n" + "Длинный текст " * 150 +
               "\n**Второй блок**\n" + "Длинный текст " * 150 +
               "\n**Финальный блок**\n" + "Длинный текст " * 80 +
               "\nПодписывайтесь на «Легенды тёмных улиц»")
        row = two.metrics(txt, "legends")
        self.assertTrue(row["minimum_length"])
        self.assertTrue(row["has_bold_sections"])
        self.assertTrue(row["channel_cta"])

    def test_balance_failure_never_starts_model_calls(self):
        with patch.object(two, "github_private", return_value="private-preflight"), \
             patch.object(two, "credits", return_value=two.Decimal("15")), \
             patch.object(two, "one_model") as model:
            with self.assertRaisesRegex(two.UnsafeToTest, "insufficient_balance_reserve"):
                two.evaluate()
            model.assert_not_called()

    def test_private_storage_failure_prevents_spend(self):
        with patch.object(two, "github_private",
                          side_effect=two.UnsafeToTest("private_storage_unavailable")), \
             patch.object(two, "credits") as balance:
            with self.assertRaisesRegex(two.UnsafeToTest, "private_storage_unavailable"):
                two.evaluate()
            balance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
