"""Rules and privacy checks for the isolated visual experiment."""
import unittest

from src.editorial_image_benchmark import MANIFEST, read_manifest
from src.editorial_visual_matching import Scene, baseline_order, verified_order, verify_caption


class MatchingTests(unittest.TestCase):
    def test_fixture_covers_three_channels_and_film_frames_are_not_republished(self):
        d = read_manifest(MANIFEST)
        self.assertEqual(len(d["images"]), 30)
        self.assertTrue(all(not row["reusable"] for row in d["images"] if row.get("path")))

    def test_caption_requires_scene_details_and_fails_closed(self):
        spec = Scene("Night railway with tracks", {"subject": ["train"], "time": ["night"]}, ("bus",))
        self.assertEqual(verify_caption(spec, "A train at night")["verdict"], "supported")
        self.assertEqual(verify_caption(spec, "A train in daylight")["verdict"], "uncertain")
        self.assertEqual(verify_caption(spec, "A bus beside a train at night")["verdict"], "contradiction")
        self.assertEqual(verify_caption(spec, "No train appears at night")["verdict"], "uncertain")
        self.assertEqual(verify_caption(spec, "A train at night, without a bus")["verdict"], "supported")

    def test_experiment_ranks_all_candidates_before_selecting(self):
        spec = Scene("Night railway with tracks", {"subject": ["train"], "time": ["night"]}, ("bus",))
        rows = [{"id": "wrong", "scores": [.25, .23, .18, .12]},
                {"id": "right", "scores": [.32, .20, .12, .04]}]
        self.assertEqual(baseline_order(rows)[0]["id"], "wrong")
        order, judged = verified_order(spec, rows, {"wrong": "A bus at night", "right": "A train at night"})
        self.assertEqual([row["id"] for row in order], ["right"])
        self.assertEqual(judged[1]["caption_check"]["verdict"], "contradiction")
