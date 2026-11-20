"""Publisher-labelled research experiments may never enable release.
These unit tests use synthetic fixtures only, never assert empirical accuracy.
"""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_research_train import extract_pairs, train_research, score_research, N_TRAIN_PAIRS, N_HOLDOUT_PAIRS
from editorial_ensemble import FEATURES

class ResearchOnlyTests(unittest.TestCase):
 def test_pairing_same_source_id(self):
  h=[{"id":12,"source":"human","text":"Лес за городом " * 80}]
  a=[{"id":12,"source":"ai","text":"Загадочный лес " * 80}]
  out=extract_pairs(h,a)
  self.assertEqual(len(out),1)
  self.assertEqual(out[0][0],"12")
 def test_no_mislabeled_pairs(self):
  h=[{"id":12,"source":"ai","text":"Лес за городом " * 80}]
  a=[{"id":12,"source":"human","text":"Загадочный лес " * 80}]
  self.assertEqual(extract_pairs(h,a),[])
 def test_research_status_always_denies_publication(self):
  rows=[]
  for i in range(N_TRAIN_PAIRS):
   for label, val in (("human",20),("ai",80)):
    rows.append({"label":label,"provenance_level":"publisher_claim_not_independently_verified",
                 "features":{f:val for f in FEATURES}})
  m=train_research(rows)
  self.assertEqual(m["status"],"research_only_publisher_labels_unverified")
  self.assertIs(m["publication_authorized"],False)
  holdout=[]
  for i in range(N_HOLDOUT_PAIRS):
   for label, val in (("human",20),("ai",80)):
    holdout.append({"label":label,"features":{f:val for f in FEATURES}})
  stats=score_research(m,holdout)
  self.assertIs(stats["publication_authorized"],False)
  self.assertIs(stats["unvalidated"],True)
 def test_no_provenance_upgrade(self):
  rows=[{"label":"ai","provenance_level":"independently_verified",
         "features":{f:90 for f in FEATURES}} for i in range(2*N_TRAIN_PAIRS)]
  with self.assertRaises(ValueError):
   train_research(rows)

if __name__=="__main__":
 unittest.main()
