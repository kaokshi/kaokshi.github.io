"""Simulate Dzen RSS output without enabling actual public publication."""
import json
import re
import unittest
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import build
CONTENT_NS="{http://purl.org/rss/1.0/modules/content/}"
class TestRSSPreview(unittest.TestCase):
    def test_truman_full_text_and_images_in_rss_preview(self):
        p=ROOT/"content"/"articles"/"cinema-01-truman-show-real-photos.json"
        d=json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["status"],"approved")
        self.assertFalse(d["publication_authorized"])
        self.assertTrue(d["release_hold"])
        self.assertEqual(d["ai_score"],0)
        self.assertEqual(len(d["image_files"]),8)
        self.assertEqual(len(set(d["image_files"])),8)
        # Test-only mock: no article metadata is modified and site settings stay frozen.
        hypothetical={**d,"publication_authorized":True,"release_hold":False,"publish_at":"2026-11-19T09:00:00+00:00"}
        date=datetime(2026,11,19,9,tzinfo=timezone.utc)
        hypothetical["date"]=date
        hypothetical["path"]="/cinema/"+hypothetical["slug"]+"/"
        xml=build.rss("cinema",build.CHANNELS["cinema"],[hypothetical],"https://kaokshi.github.io")
        root=ET.fromstring(xml)
        items=root.findall("./channel/item")
        self.assertEqual(len(items),1)
        item=items[0]
        self.assertEqual(item.findtext("title"),d["title"])
        self.assertEqual(item.findtext("guid"),"https://kaokshi.github.io"+hypothetical["path"])
        parsedate_to_datetime(item.findtext("pubDate"))
        whole=item.findtext(CONTENT_NS+"encoded")
        self.assertIsNotNone(whole)
        self.assertGreater(len(re.sub(r"\s","",re.sub("<[^>]+>","",whole))),3000)
        self.assertEqual(whole.count("<img"),8)
        self.assertNotIn('src="/',whole)
        self.assertEqual(item.find("enclosure").attrib["type"],"image/jpeg")
        self.assertLess(len(xml.encode("utf-8")),10_000_000)
    def test_freeze_stays_active(self):
        config,_=build.config()
        self.assertIs(config.get("auto_release_enabled"),True)
        if config.get("publication_enabled"):
            self.assertTrue(config.get("auto_release_active_channels")==["legends","science"])
        else:
            self.assertIs(config.get("publication_enabled"), False)
if __name__=="__main__":
    unittest.main()
