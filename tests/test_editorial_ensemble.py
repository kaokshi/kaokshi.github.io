"""Synthetic-only unit tests of experimental ensemble: NEVER production validation."""
import hashlib
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_ensemble import fit,score,evaluate,route_revision,ACTIVE,SUPPORTED_GENRES

def example(n, label, channel="legends", split="train", source=None):
    signal = 80 if label == "ai" else 20
    return {"channel":channel,"split":split,"label":label,
            "provenance_verified":True,"four_detectors_completed":True,
            "threshold_frozen_before_holdout":True,"disjoint_from_model_training":True,
            "source_id":source or f"{split}/{channel}/{n}",
            "text_sha256":hashlib.sha256(f"{split}/{channel}/{n}".encode()).hexdigest(),
            "features":{"rubert_v4":signal,"modernbert":signal,
                        "lightgbm":signal,"ai_accent_detector":signal}}

class EnsembleTests(unittest.TestCase):
    def setUp(self):
        self.train=[example(i,"human") for i in range(30)]+[example(i+30,"ai") for i in range(30)]
    def test_synthetic_training_only(self):
        m=fit(self.train)
        self.assertEqual(m["status"],"experimental_unvalidated")
        self.assertGreater(score(m,example(500,"ai")),score(m,example(500,"human")))
        self.assertFalse(m.get("publication_authorized",False))
    def test_insufficient_data_fails(self):
        with self.assertRaises(ValueError):
            fit(self.train[:3])
    def test_incomplete_four_features_fails(self):
        self.train[0]["features"].pop("lightgbm")
        with self.assertRaises(ValueError):
            fit(self.train)
    def test_holdout_leakage_fails(self):
        m=fit(self.train)
        holdout=[example(100+i,"human",split="holdout") for i in range(120)]
        holdout += [example(300+i,"ai",split="holdout") for i in range(120)]
        holdout[0]["source_id"]=self.train[0]["source_id"]
        with self.assertRaisesRegex(ValueError,"leakage"):
            evaluate(m,holdout,90,self.train)
    def test_holdout_synthetic_report_never_authorizes(self):
        m=fit(self.train)
        holdout=[example(100+i,"human",split="holdout") for i in range(120)]
        holdout += [example(300+i,"ai",split="holdout") for i in range(120)]
        r=evaluate(m,holdout,90,self.train)
        self.assertFalse(r["eligible_for_release"])
        self.assertEqual(r["human_holdout_n"],120)
        self.assertEqual(r["ai_holdout_n"],120)
    def test_two_revisions_then_owner(self):
        base={"channel":"legends","score_percent":91,"threshold":85,
              "validated":True,"substantive_issues":["Unverified claim"],"checks_completed":True}
        self.assertEqual(route_revision(**base,attempt=0)["attempt_next"],1)
        self.assertEqual(route_revision(**base,attempt=1)["attempt_next"],2)
        self.assertEqual(route_revision(**base,attempt=2)["decision"],"owner_review")
    def test_no_score_only_rewrites(self):
        v=route_revision(channel="science",score_percent=99,threshold=80,validated=True,
              attempt=0,substantive_issues=[])
        self.assertEqual(v["reason"],"high_score_without_proven_defect")
    def test_unvalidated_and_missing_check_fail_closed(self):
        v=route_revision(channel="science",score_percent=99,threshold=80,validated=False,
              attempt=0,substantive_issues=["Repeated paragraph"])
        self.assertEqual(v["decision"],"owner_review")
        self.assertFalse(v["release"])
        self.assertEqual(route_revision(channel="science",score_percent=99,threshold=80,validated=True,
              attempt=0,substantive_issues=["Repeated paragraph"],checks_completed=False)["decision"],"owner_review")
    def test_cinema_separate_model_training_and_holdout(self):
        self.assertIn("cinema", SUPPORTED_GENRES)
        self.assertNotIn("cinema", ACTIVE)
        train = [example(i,"human",channel="cinema") for i in range(30)]
        train += [example(30+i,"ai",channel="cinema") for i in range(30)]
        model = fit(train)
        self.assertEqual(model["channel"],"cinema")
        self.assertEqual(model["status"],"experimental_unvalidated")
        self.assertGreater(score(model,example(900,"ai",channel="cinema")),
                           score(model,example(901,"human",channel="cinema")))
        holdout=[example(100+i,"human",channel="cinema",split="holdout") for i in range(120)]
        holdout += [example(300+i,"ai",channel="cinema",split="holdout") for i in range(120)]
        result=evaluate(model,holdout,90,train)
        self.assertEqual(result["human_holdout_n"],120)
        self.assertEqual(result["ai_holdout_n"],120)
        self.assertFalse(result["eligible_for_release"])
        self.assertEqual(route_revision(channel="cinema",score_percent=99,threshold=80,
              validated=True,attempt=0,substantive_issues=["repeat"])["decision"],"paused_channel")
    def test_cinema_model_is_genre_specific(self):
        train = [example(i,"human",channel="cinema") for i in range(30)]
        train += [example(30+i,"ai",channel="cinema") for i in range(30)]
        model=fit(train)
        with self.assertRaisesRegex(ValueError,"Wrong genre"):
            score(model,example(500,"human",channel="science"))

    def test_paused_cinema(self):
        self.assertEqual(route_revision(channel="cinema",score_percent=99,threshold=80,
              validated=True,attempt=0,substantive_issues=["repeat"])["decision"],"paused_channel")

if __name__=="__main__":
    unittest.main()
