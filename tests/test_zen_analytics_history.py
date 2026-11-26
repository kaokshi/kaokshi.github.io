import tempfile
import unittest
from pathlib import Path
from src.zen_analytics_history import metrika_snapshot, append_snapshot

def fixture():
    return {"schema":"zen-metrika/v1","collected_at":"2026-11-26T10:00:00Z",
            "channels":{c:{"channel":c,"source":"yandex_metrika","status":"binding_unverified",
                          "periods":{}} for c in ("science","cinema","legends")}}

class HistoryTest(unittest.TestCase):
    def test_three_channels_and_no_false_confirmation(self):
        rows=metrika_snapshot(fixture())
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r["confirmation"]=="unverified" and not r["usable_for_editorial_advice"] for r in rows))
    def test_replay_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            r=metrika_snapshot(fixture())[0]
            self.assertTrue(append_snapshot(d,r)["created"])
            self.assertFalse(append_snapshot(d,r)["created"])
            self.assertTrue(append_snapshot(d,{**r,"collected_at":"2026-11-26T11:00:00+00:00"})["created"])
            self.assertEqual(len(list(Path(d).rglob("*.json"))),2)
    def test_channel_mismatch(self):
        f=fixture()
        f["channels"]["science"]["channel"]="cinema"
        with self.assertRaises(ValueError): metrika_snapshot(f)
    def test_unverified_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as d:
            r=metrika_snapshot(fixture())[0]
            with self.assertRaises(ValueError): append_snapshot(d,{**r,"confirmation":"verified"})
    def test_incomplete_report_and_naive_timestamp(self):
        f=fixture()
        del f["channels"]["science"]
        with self.assertRaises(ValueError): metrika_snapshot(f)
        f=fixture()
        f["collected_at"]="2026-11-26T10:00:00"
        with self.assertRaises(ValueError): metrika_snapshot(f)
