"""Offline regressions for science's real-photo + explanatory Klein illustration mix."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.editorial_images import photo, validate_plan
from src.editorial_providers import Blocked


class ScienceMixedImagesTests(unittest.TestCase):
    def plan(self, kinds):
        sections = [1, 1, 2, 3, 4, 5, 6]
        return {
            "subject": "Real science process with illustrated explanation",
            "images": [
                {
                    "section": section,
                    "kind": kind,
                    "subject": f"Distinct science scene {i}",
                    "brief": f"Unique well grounded scene description {i}",
                    "query": f"scientific photograph {i}",
                }
                for i, (section, kind) in enumerate(zip(sections, kinds))
            ],
        }

    def test_science_requires_both_real_photos_and_illustrations(self):
        mixed = self.plan(["photo", "illustration", "photo", "photo",
                           "photo", "illustration", "photo"])
        validate_plan(mixed, 6, "science", {"minimum_images": 7, "maximum_images": 8})
        photos_only = self.plan(["photo"] * 7)
        with self.assertRaisesRegex(Blocked, "image_plan_missing_section_or_medium"):
            validate_plan(photos_only, 6, "science", {"minimum_images": 7, "maximum_images": 8})
        illustrations_only = self.plan(["illustration"] * 7)
        with self.assertRaisesRegex(Blocked, "image_plan_missing_section_or_medium"):
            validate_plan(illustrations_only, 6, "science", {"minimum_images": 7, "maximum_images": 8})

    def test_science_photo_search_falls_back_to_nasa_without_pretending_rights_verified(self):
        scene = {"section": 3, "kind": "photo", "query": "real observatory photo",
                 "subject": "Observatory science"}
        candidate = {"asset_url": "https://images-assets.nasa.gov/example.jpg",
                     "domains": ("images-assets.nasa.gov",),
                     "source": "NASA", "rights_status": "rights_review_pending"}
        index = SimpleNamespace(memory=SimpleNamespace(time_left=lambda: 300))
        def save(raw, metadata, selected_scene, subject, path):
            self.assertEqual(metadata["rights_status"], "rights_review_pending")
            self.assertEqual(raw, b"synthetic image fixture")
            self.assertEqual(selected_scene, scene)
            return {"source": "NASA", "rights_status": metadata["rights_status"]}
        index.save = save
        with patch("src.editorial_images.warm_visual_matcher"), \
             patch("src.editorial_images.commons_photos", return_value=iter(())), \
             patch("src.editorial_images.nasa_photos", return_value=iter([candidate])) as nasa, \
             patch("src.editorial_images.request", return_value=b"synthetic image fixture"):
            result = photo(scene, "science", "irrelevant.jpg", index, verified_only=False)
        self.assertEqual(result["source"], "NASA")
        nasa.assert_called_once_with(scene["query"])

    def test_strict_commons_route_must_not_use_nasa(self):
        scene = {"section": 2, "kind": "photo", "query": "research microscope photo",
                 "subject": "Microscope photo"}
        index = SimpleNamespace(memory=SimpleNamespace(time_left=lambda: 300))
        with patch("src.editorial_images.warm_visual_matcher"), \
             patch("src.editorial_images.commons_photos", return_value=iter(())), \
             patch("src.editorial_images.nasa_photos") as nasa:
            with self.assertRaisesRegex(Blocked, "no_traceable_photo_found"):
                photo(scene, "science", "irrelevant.jpg", index, verified_only=True)
        nasa.assert_not_called()


if __name__ == "__main__":
    unittest.main()
