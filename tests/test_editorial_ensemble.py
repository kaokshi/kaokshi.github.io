"""Synthetic-only unit tests of experimental ensemble: NEVER production validation."""
import hashlib
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
        base={"channel":"legends","substantive_issues":["Unverified claim"],"checks_completed":True}
        self.assertEqual(route_revision(**base,attempt=0)["attempt_next"],1)
        self.assertEqual(route_revision(**base,attempt=1)["attempt_next"],2)
        self.assertEqual(route_revision(**base,attempt=2)["decision"],"owner_review")
    def test_no_score_only_rewrites(self):
        v=route_revision(channel="science",score_percent=99,
              attempt=0,substantive_issues=[])
        self.assertEqual(v["decision"],"quality_cleared_for_other_release_gates")
    def test_unvalidated_and_missing_check_fail_closed(self):
        v=route_revision(channel="science",
              attempt=0,substantive_issues=["Repeated paragraph"])
        self.assertEqual(v["decision"],"targeted_revision")
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

    def test_no_ai_threshold_needed_for_real_defects(self):
        r=route_revision(channel="legends",attempt=0,substantive_issues=["Repeated phrase"],
                         score_percent=None,threshold=None,validated=False)
        self.assertEqual(r["decision"],"targeted_revision")
        self.assertEqual(r["attempt_next"],1)
    def test_ai_score_alone_never_forces_rewrite(self):
        for candidate_index in (0, 60, 100, None):
            r=route_revision(channel="science",attempt=0,substantive_issues=[],
                             score_percent=candidate_index,validated=False)
            self.assertEqual(r["decision"],"quality_cleared_for_other_release_gates")
            self.assertFalse(r["release"])
    def test_after_two_attempts_owner_gets_unresolved_issues(self):
        r=route_revision(channel="science",attempt=2,
                         substantive_issues=["Fact not confirmed"],validated=False)
        self.assertEqual(r["decision"],"owner_review")
        self.assertEqual(r["reason"],"two_revisions_exhausted")
        self.assertEqual(r["issues"],["Fact not confirmed"])
    def test_incomplete_checks_without_threshold_still_block(self):
        r=route_revision(channel="legends",attempt=0,
                         substantive_issues=[],checks_completed=False)
        self.assertEqual(r["decision"],"owner_review")
        self.assertFalse(r["release"])

    def test_owner_threshold_30_requires_validation(self):
        high=route_revision(channel="legends",attempt=0,substantive_issues=[],
                            score_percent=75,validated=False)
        self.assertEqual(high["decision"],"quality_cleared_for_other_release_gates")
        flagged=route_revision(channel="legends",attempt=0,substantive_issues=[],
                               score_percent=30.0001,validated=True)
        self.assertEqual(flagged["decision"],"editorial_review_required")
        self.assertFalse(flagged["release"])
        clear=route_revision(channel="science",attempt=0,substantive_issues=[],
                             score_percent=30.0,validated=True)
        self.assertEqual(clear["decision"],"quality_cleared_for_other_release_gates")
    def test_two_attempts_with_validated_high_index_then_owner(self):
        kwargs={"channel":"legends","substantive_issues":["Repeated content"],
                "score_percent":77,"validated":True}
        self.assertEqual(route_revision(**kwargs,attempt=0)["attempt_next"],1)
        self.assertEqual(route_revision(**kwargs,attempt=1)["attempt_next"],2)
        self.assertEqual(route_revision(**kwargs,attempt=2)["decision"],"owner_review")
    def test_approved_threshold_cannot_be_silently_changed(self):
        with self.assertRaisesRegex(ValueError,"exactly 30"):
            route_revision(channel="legends",attempt=0,substantive_issues=[],
                           score_percent=55,threshold=25,validated=True)

    def test_paused_cinema(self):
        self.assertEqual(route_revision(channel="cinema",score_percent=99,threshold=80,
              validated=True,attempt=0,substantive_issues=["repeat"])["decision"],"paused_channel")

if __name__=="__main__":
    unittest.main()
