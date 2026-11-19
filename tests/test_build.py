import sys
import unittest
from pathlib import Path
from datetime import datetime,timezone
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import build

class WebsiteTest(unittest.TestCase):
    def test_three_channels(self):
        self.assertEqual(set(build.CHANNELS),{"cinema","legends","science"})
    def test_date_requires_timezone(self):
        with self.assertRaises(ValueError):
            build.parse_date("2026-12-01T20:00:00")
    def test_build_produces_three_valid_rss(self):
        build.build(datetime(2026,11,19,tzinfo=timezone.utc))
        for key in build.CHANNELS:
            rss=build.OUT/"feeds"/(key+".xml")
            root=ET.fromstring(rss.read_text("utf-8"))
            self.assertEqual(root.tag,"rss")
            self.assertEqual(root.attrib["version"],"2.0")
    def test_draft_not_published(self):
        self.assertEqual(build.read_posts(datetime(2026,11,19,tzinfo=timezone.utc)),[])
if __name__=="__main__":
    unittest.main()
