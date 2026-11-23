"""The same display-length boundaries apply to editing and release."""
import unittest

from src.editorial_length import check_html_length, check_length
from src.editorial_review import canonical, parse_article, structural_checks


class LengthTests(unittest.TestCase):
    def test_exact_bounds_with_and_without_exception(self):
        reason = "Нужно объяснить, почему сравнение двух измерений меняет вывод."
        for n, expected in ((2999, "short_text"), (3000, None), (6000, None),
                            (6001, "length_exception_reason_missing"),
                            (7000, "length_exception_reason_missing"), (7001, "long_text")):
            text = "я" * n
            with self.subTest(n=n):
                self.assertEqual(check_length(text), (n, expected))
                self.assertEqual(check_html_length(f"<p>{text}</p>"), (n, expected))
                allowed = None if 3000 <= n <= 7000 else expected
                self.assertEqual(check_html_length(f"<p>{text}</p>", reason), (n, allowed))
                self.assertEqual(check_length(text, " \u00a0"), (n, expected))

    def test_html_counts_visible_decoded_body(self):
        html = '<head><title>Hidden</title></head><p>' + ('я' * 2999) + '&amp;&nbsp;</p>'
        html += '<script>hidden</script><figure><img alt="unrelated"><figcaption>Caption</figcaption></figure>'
        self.assertEqual(check_html_length(html), (3000, None))

    def test_editor_metadata_excluded_from_hashable_text_and_checked(self):
        raw = "я" * 6001
        draft = parse_article(raw + "\n<!-- length_exception_reason: Для сравнения нужна вторая сцена. -->")
        self.assertEqual(canonical(draft), raw)
        report = structural_checks(draft, [])
        self.assertEqual(report["chars_without_spaces"], 6001)
        self.assertEqual(report["length_exception_reason"], "Для сравнения нужна вторая сцена.")
        self.assertFalse(any(issue.startswith("article_length") for issue in report["issues"]))
        self.assertTrue(any(issue.startswith("article_length") for issue in structural_checks(parse_article(raw), [])["issues"]))
