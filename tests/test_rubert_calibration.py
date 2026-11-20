"""Calibration protocol tests; no model downloads or threshold changes."""
import unittest
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import rubert_calibration as cal

class CalibrationTests(unittest.TestCase):
    def test_real_ninth_floor_pattern_is_not_60_percent_article_probability(self):
        vals=[.6,.481481,.303797,.112676,.303797,.481481,.481481,
              .112676,.112676,.112676,.112676,.303797,.112676,
              .303797,.112676,.090909,.303797]
        s=cal.summarize_windows(vals)
        self.assertEqual(s["max"],.6)
        self.assertEqual(s["p75"],.303797)
        self.assertEqual(s["p90"],.481481)
        self.assertAlmostEqual(s["mean"],.261357,places=5)
        self.assertEqual(s["n_windows"],17)

    def test_invalid_values_are_rejected(self):
        for invalid in ([float("nan")],[float("inf")],[True],[-.1],[1.2],[]):
            with self.assertRaises(ValueError):
                cal.summarize_windows(invalid)

    def test_insufficient_truth_data_does_not_propose_a_threshold(self):
        samples=[{"id":"h1","genre":"legends","label":"human",
                  "split":"calibration","source_group":"author-one",
                  "provenance_verified":True,"window_probabilities":[.2,.3]}]
        result=cal.suggest_thresholds(samples)
        self.assertEqual(result["proposals"]["legends"]["status"],"insufficient_labeled_evidence")
        self.assertIsNone(result["proposals"]["legends"]["candidate_threshold"])
        self.assertFalse(result["release_gate_changed"])

    def test_leakage_is_rejected(self):
        samples=[{"id":"a","genre":"legends","label":"human",
                  "split":"calibration","source_group":"same-origin",
                  "provenance_verified":True,"window_probabilities":[.2]},
                 {"id":"b","genre":"legends","label":"human",
                  "split":"holdout","source_group":"same-origin",
                  "provenance_verified":True,"window_probabilities":[.3]}]
        with self.assertRaisesRegex(ValueError,"leaked"):
            cal.suggest_thresholds(samples)

    def test_valid_fingerprint_source_and_identity_required(self):
        samples=[{"id":"a","genre":"legends","label":"human",
                  "split":"calibration","source_group":"a",
                  "provenance_verified":False,"window_probabilities":[.2]}]
        with self.assertRaises(ValueError):
            cal.suggest_thresholds(samples)

    def test_controlled_research_threshold_is_never_automatically_deployed(self):
        rows=[]
        for split,nh,na in (("calibration",50,50),("holdout",30,30)):
            for label,n in (("human",nh),("ai",na)):
                for i in range(n):
                    rows.append({"id":f"{split}-{label}-{i}","genre":"legends",
                          "split":split,"source_group":f"{split}-{label}-{i}",
                          "provenance_verified":True,
                          "label":label,
                          "window_probabilities":[.1] if label=="human" else [.9]})
        result=cal.suggest_thresholds(rows)
        r=result["proposals"]["legends"]
        self.assertEqual(r["status"],"holdout_evaluated")
        self.assertEqual(r["candidate_threshold"],.1)
        self.assertEqual(r["holdout_rates"]["false_positive_rate"],0)
        self.assertEqual(r["holdout_rates"]["true_positive_rate"],1)
        self.assertFalse(r["eligible_to_change_release_gate"])
        self.assertFalse(result["release_gate_changed"])

if __name__=="__main__":
    unittest.main()
