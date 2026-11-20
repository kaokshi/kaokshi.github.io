"""Six-article dry-run to the RSS serialization boundary. Never writes an RSS file."""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from build import CHANNELS, config, rss, read_posts
SLUGS={
 "legends": ["legends-pilot-midnight-bus-20261120","legends-pilot-photo-studio-20261120"],
 "science": ["science-pilot-noctilucent-clouds-20261120","science-pilot-hair-ice-20261120"],
 "cinema": ["cinema-pilot-inception-20261120","cinema-pilot-shutter-island-20261120"],
}
class SixArticlePreflight(unittest.TestCase):
 def test_release_stays_frozen(self):
  cfg,_=config()
  self.assertIs(cfg["publication_enabled"],False)
  self.assertEqual(read_posts(datetime.fromisoformat("2099-02-01T00:00:00+00:00")),[])
  for channel,items in SLUGS.items():
   for slug in items:
    with self.subTest(slug=slug):
     a=json.loads((ROOT/"content"/"articles"/(slug+".json")).read_text("utf-8"))
     self.assertEqual(a["status"],"draft")
     self.assertTrue(a["release_hold"])
     self.assertFalse(a["publication_authorized"])
     self.assertFalse(a["auto_release_candidate"])
     self.assertLess(len(a.get("image_files",[])),7)
 def test_rss_preview_for_all_six_only_in_memory(self):
  cfg,site=config()
  for channel,items in SLUGS.items():
   draft=[]
   for slug in items:
    a=json.loads((ROOT/"content"/"articles"/(slug+".json")).read_text("utf-8"))
    a["date"]=datetime.fromisoformat(a["publish_at"])
    a["path"]=f"/{channel}/{slug}/"
    draft.append(a)
   xml=rss(channel,CHANNELS[channel],draft,site)
   parsed=ET.fromstring(xml)
   self.assertEqual(parsed.tag,"rss")
   entries=parsed.findall("./channel/item")
   self.assertEqual(len(entries),2)
   for item in entries:
    self.assertTrue(item.findtext("title"))
    self.assertTrue(item.findtext("link").startswith(site+"/"))
    self.assertIsNotNone(item.find("{http://purl.org/rss/1.0/modules/content/}encoded"))
    self.assertIsNotNone(item.findtext("guid"))
if __name__=="__main__":
 unittest.main()
