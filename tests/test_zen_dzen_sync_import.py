import unittest

from src.zen_dzen_sync_import import compact_report
from test_zen_dzen_xlsx import fixture


class NativeStudioImportTest(unittest.TestCase):
    def test_compact_reports_from_three_bound_channels(self):
        for channel in ("science", "legends", "cinema"):
            with self.subTest(channel=channel):
                raw = fixture(channel)
                report = compact_report(raw, channel, "a" * 24)
                self.assertEqual(report, compact_report(raw, channel, "a" * 24))
                self.assertEqual(report["channel"], channel)
                self.assertEqual(set(report), {"channel", "channelId", "digest", "start", "end", "dzen", "all"})
                self.assertNotIn("publications", report)
                with self.assertRaises(ValueError):
                    compact_report(raw, channel, "b" * 24)
