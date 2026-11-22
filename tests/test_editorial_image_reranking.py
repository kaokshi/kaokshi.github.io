"""Deterministic regression tests for selecting the most relevant photo, not first hit."""
import io
import unittest
from unittest.mock import patch

from PIL import Image

from src import editorial_images as images


def jpeg(value):
    img = Image.new("RGB", (600, 600), (value, value, value))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class Time:
    def time_left(self):
        return 600


class Index:
    memory = Time()
    def __init__(self):
        self.written = []
    def save(self, raw, metadata, scene, subject, path):
        self.written.append((raw, metadata))
        return metadata


class RelevanceRanking(unittest.TestCase):
    def test_rank_selects_later_better_photo(self):
        poor = jpeg(40)
        better = jpeg(220)
        def score(image, captions):
            v = image.getpixel((10, 10))[0] / 255
            return "mock-scene-score", [round(.2 + .5 * v, 3), .3, .01], [v]
        candidates = [
            {"bytes": poor, "metadata": {"asset_url": "https://upload.wikimedia.org/a.jpg"}},
            {"bytes": better, "metadata": {"asset_url": "https://upload.wikimedia.org/b.jpg"}}]
        ranked = images.rank_photo_candidates(candidates, {"subject": "bright scene"}, "topic", scorer=score)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["bytes"], better)

    def test_unrelated_images_rejected(self):
        candidates = [{"bytes": jpeg(40), "metadata": {}}]
        ranked = images.rank_photo_candidates(candidates, {"subject": "specific scene"}, "topic",
                                              scorer=lambda *_: ("mock", [.18, .99, .5], [0]))
        self.assertEqual(ranked, [])

    def test_selection_keeps_rights_metadata_and_qa(self):
        weak, strong = jpeg(60), jpeg(200)
        def find(_):
            return iter([
                {"asset_url": "https://upload.wikimedia.org/weak.jpg",
                 "source_page": "https://commons.wikimedia.org/wiki/File:Weak",
                 "rights_status": "rights_review_pending", "domains": ("upload.wikimedia.org",)},
                {"asset_url": "https://upload.wikimedia.org/strong.jpg",
                 "source_page": "https://commons.wikimedia.org/wiki/File:Strong",
                 "rights_status": "rights_review_pending", "domains": ("upload.wikimedia.org",)}])
        def score(image, captions):
            v = image.getpixel((10, 10))[0] / 255
            return "mock", [.2 + .5 * v, .2, .02], [v]
        index = Index()
        with patch.object(images, "commons_photos", find), \
             patch.object(images, "nasa_photos", lambda _: iter(())), \
             patch.object(images, "warm_visual_matcher", lambda: None), \
             patch.object(images, "similarity", score), \
             patch.object(images, "request", side_effect=lambda url, *_args, **kwargs: strong if "strong" in url else weak), \
             patch.object(images, "rank_photo_candidates", side_effect=lambda candidates, scene, topic: real_rank(candidates, scene, topic, scorer=score)):
            result = images.photo({"subject": "specific scene", "query": "scene", "section": 1},
                                  "overall topic", "unused.jpg", index)
        self.assertEqual(result["source_page"], "https://commons.wikimedia.org/wiki/File:Strong")
        self.assertEqual(result["rights_status"], "rights_review_pending")
        self.assertEqual(result["candidate_pool_size"], 2)
        self.assertEqual(result["candidate_rank"], 1)

if __name__ == "__main__":
    unittest.main()
