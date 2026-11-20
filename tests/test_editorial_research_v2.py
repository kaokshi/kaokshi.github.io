"""Offline checks for enlarged three-genre RESEARCH ONLY experiment."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_research_v2 import COUNTS, russian_fraction
from editorial_research_train import train_research, score_research
from editorial_ensemble import FEATURES

class ExpandedResearchTests(unittest.TestCase):
    def test_three_genres_have_separate_sample_plans(self):
        self.assertEqual(set(COUNTS), {"legends","science","cinema"})
        self.assertGreater(COUNTS["science"][0],20)
        self.assertGreater(COUNTS["legends"][1],10)
        self.assertLess(COUNTS["cinema"][1],120)
    def test_russian_filter(self):
        self.assertGreater(russian_fraction("Киноактёры смотрели фильм"),.9)
        self.assertLess(russian_fraction("This is an English review"),.1)
    def test_research_never_authorizes_after_larger_training(self):
        ntrain=4
        nhold=2
        train=[]
        holdout=[]
        for i in range(ntrain):
            for label,val in [("human",10),("ai",85)]:
                train.append({"label":label,"provenance_level":"publisher_claim_not_independently_verified",
                              "features":{f:val for f in FEATURES}})
        model=train_research(train,n_train_pairs=ntrain)
        self.assertFalse(model["publication_authorized"])
        for i in range(nhold):
            for label,val in [("human",10),("ai",85)]:
                holdout.append({"label":label,"features":{f:val for f in FEATURES}})
        report=score_research(model,holdout,n_holdout_pairs=nhold)
        self.assertTrue(report["unvalidated"])
        self.assertFalse(report["publication_authorized"])
    def test_require_pair_count(self):
        with self.assertRaises(ValueError):
            train_research([],n_train_pairs=60)

if __name__=="__main__":
    unittest.main()
