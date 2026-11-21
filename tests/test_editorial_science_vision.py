"""Tests for scientific fixed-source label comparison and hard-case suite."""
import io
import json
import sys
from pathlib import Path
import unittest
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_science_vision import CONTRACTS, score_transcription, VisionPending
from editorial_vision_hard_benchmark import scientific_diagram,science_cases


class Tests(unittest.TestCase):
    def test_known_science_sources_are_explicit(self):
        self.assertIn("nasa",CONTRACTS["solar_inner_planets"]["source"])
        self.assertIn("genome.gov",CONTRACTS["dna_complementary_base_pairs"]["source"])

    def test_correct_transcription_not_automatic_publication(self):
        image=scientific_diagram("solar_inner_planets",
                ["Mercury","Venus","Earth","Mars"])
        result=score_transcription(json.dumps({
            "observed_labels":["Mercury","Venus","Earth","Mars"],
            "all_labels_legible":True}),image,"solar_inner_planets")
        self.assertEqual(result["status"],"needs_independent_validation")
        self.assertIs(result["semantic_verified"],False)

    def test_sun_context_label_is_not_a_planet(self):
        sample=scientific_diagram("solar_inner_planets",
                ["Mercury","Venus","Earth","Mars"])
        record=score_transcription(json.dumps({
            "observed_labels":["SUN","Mercury","Venus","Earth","Mars"],
            "all_labels_legible":True}),sample,"solar_inner_planets")
        self.assertEqual(record["status"],"needs_independent_validation")
        self.assertEqual(record["observed_labels"][0],"SUN")
        self.assertEqual(record["compared_labels"],["Mercury","Venus","Earth","Mars"])

    def test_false_label_is_rejected_even_if_visually_readable(self):
        image=scientific_diagram("solar_inner_planets",
                ["Mercury","Earth","Venus","Mars"])
        result=score_transcription(json.dumps({
            "observed_labels":["Mercury","Earth","Venus","Mars"],
            "all_labels_legible":True}),image,"solar_inner_planets")
        self.assertEqual(result["status"],"reject_candidate")

    def test_unclear_image_fails_closed(self):
        with self.assertRaises(VisionPending):
            score_transcription('{"observed_labels":["Mercury"],"all_labels_legible":"yes"}',
                b"fake","solar_inner_planets")

    def test_science_suite_has_correct_and_wrong_labels(self):
        cases=list(science_cases())
        self.assertEqual(len(cases),8)
        self.assertEqual(sum(x[1] for x in cases),4)
        for name,label,bytes_,category in cases:
            with Image.open(io.BytesIO(bytes_)) as img:
                self.assertEqual(img.size,(960,540))

if __name__=="__main__":
    unittest.main()
