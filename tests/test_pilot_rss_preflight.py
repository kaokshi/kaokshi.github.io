"""Safe RSS preflight for editorial pilot articles: no writing and no deployment."""
import json
from pathlib import Path
import unittest
import sys
from datetime import datetime
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build import CHANNELS, config, read_posts, rss

PILOTS = {
    "legends": "legends-pilot-counting-stairs-20261120",
    "science": "science-pilot-lake-nyos-20261120",
}

class PilotRssPreflight(unittest.TestCase):
    def test_global_freeze_and_unapproved_pilots(self):
        settings, _ = config()
        self.assertIs(settings["publication_enabled"], False)
        self.assertEqual(read_posts(datetime.fromisoformat("2026-12-02T12:00:00+00:00")), [])
        for channel, slug in PILOTS.items():
            article = json.loads((ROOT / "content" / "articles" / (slug + ".json")).read_text("utf-8"))
            self.assertEqual(article["channel"], channel)
            self.assertEqual(article["status"], "draft")
            self.assertIs(article.get("release_hold"), True)
            self.assertIs(article.get("publication_authorized"), False)
            self.assertIs(article.get("auto_release_candidate"), False)

    def test_two_independent_in_memory_rss_previews(self):
        """Create XML only in memory; NEVER write site/feeds or authorize publication."""
        settings, site_url = config()
        self.assertIs(settings["publication_enabled"], False)
        for channel, slug in PILOTS.items():
            with self.subTest(channel=channel):
                article = json.loads((ROOT / "content" / "articles" / (slug + ".json")).read_text("utf-8"))
                self.assertGreaterEqual(len("".join(article["body_html"].split())), 3000)
                draft = dict(article)
                draft["date"] = datetime.fromisoformat(article["publish_at"])
                draft["path"] = f"/{channel}/{slug}/"
                xml = rss(channel, CHANNELS[channel], [draft], site_url)
                root = ET.fromstring(xml)
                self.assertEqual(root.tag, "rss")
                entries = root.findall("./channel/item")
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].findtext("title"), article["title"])
                self.assertEqual(entries[0].findtext("link"), site_url + draft["path"])
                self.assertIsNotNone(entries[0].find("{http://purl.org/rss/1.0/modules/content/}encoded"))
                # Preview format passes, but release readiness does not:
                # image licenses, images and editorial evidence remain incomplete.
                self.assertLess(len(article["image_files"]), 7)

if __name__ == "__main__":
    unittest.main()
