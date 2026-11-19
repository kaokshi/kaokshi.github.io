"""Ensure approved/prepared article images are distinct and present."""
import hashlib
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class ImageUniquenessTests(unittest.TestCase):
    def test_article_images_are_unique_and_exist(self):
        for article in sorted((ROOT/"content"/"articles").glob("*.json")):
            data=json.loads(article.read_text(encoding="utf-8"))
            imgs=data.get("image_files", [])
            self.assertEqual(len(imgs),len(set(imgs)),f"Duplicate image references: {article.name}")
            digests=[]
            for relative in imgs:
                file=ROOT/relative
                self.assertTrue(file.is_file(),f"Missing image {relative} in {article.name}")
                digests.append(hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertEqual(len(digests),len(set(digests)),f"Duplicated image binary: {article.name}")
    def test_release_is_globally_frozen(self):
        config=json.loads((ROOT/"config"/"site.json").read_text(encoding="utf8"))
        self.assertIs(config.get("publication_enabled"),False,"Do not enable publication without explicit user authorization.")
if __name__=="__main__":
    unittest.main()
