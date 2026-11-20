"""No hallucinated four-detector performance or false independence."""
import unittest
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"experiments"))
import four_detector_benchmark as f

class FourDetectorResearchTests(unittest.TestCase):
    def test_model_score_validation(self):
        for value in (-.1,1.01,True,None):
            with self.assertRaises(ValueError):
                f.finite01(value)
        self.assertEqual(f.finite01(.5),.5)

    def test_word_overlap_coverage_in_fully_measured_document(self):
        values=[.2,.8]
        self.assertEqual(f.whole_index(values,150),50.0)
        self.assertEqual(f.whole_index([.6],85),60.0)
        with self.assertRaises(ValueError):
            f.whole_index([.3],150)

    def test_detector_votes_not_misrepresented_as_four_independent(self):
        rows=[
            {"label":"human","models":{"two_family_strict":False}},
            {"label":"human","models":{"two_family_strict":True}},
            {"label":"ai","models":{"two_family_strict":True}},
            {"label":"ai","models":{"two_family_strict":False}},
        ]
        cm=f.confusion(rows,"two_family_strict")
        self.assertEqual((cm["human_false_alarms"],cm["ai_detected"]),(1,1))
        self.assertEqual(cm["false_alarm_rate"],.5)

    def test_dataset_is_fixed_revision_and_pairs_disjoint(self):
        self.assertEqual(len(f.COMMIT),40)
        self.assertEqual(f.PAIRS_PER_GENRE,20)
        self.assertEqual(set(f.FILES),{"essay","scientific"})

if __name__=="__main__":
    unittest.main()
