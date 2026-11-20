import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_ai_auto_triage import triage

def report(scores=(60, 3, 18, 8), *, length=3500, completed=True):
    keys=("rubert_v4","modernbert","lightgbm","ai_accent_detector")
    return {"all_checks_completed": completed, "characters_without_spaces":length,
       "editorial_threshold":{"checks":{k:{"status":"completed","index_0_100":v}
                                  for k,v in zip(keys,scores)}}}

class TestTriage(unittest.TestCase):
    def test_large_spread_requires_attention_not_rewrite(self):
        result=triage(report())
        self.assertEqual(result["routing"],"editorial_attention")
        self.assertEqual(result["classifier_spread_points"],57)
        self.assertFalse(result["needs_text_rewrite"])
        self.assertFalse(result["publication_authorized"])

    def test_consistent_scores_remain_diagnostics(self):
        result=triage(report((48,51,53,96)))
        self.assertEqual(result["routing"],"routine_quality_checks")
        self.assertNotIn("large_cross_model_disagreement",result["reasons"])

    def test_missing_or_failed_never_passes(self):
        sample=report()
        sample["all_checks_completed"]=False
        sample["editorial_threshold"]["checks"]["lightgbm"]["status"]="unavailable"
        result=triage(sample)
        self.assertEqual(result["status"],"incomplete")
        self.assertEqual(result["routing"],"human_editorial_review")
        self.assertFalse(result["publication_authorized"])

    def test_short_article_requires_review(self):
        result=triage(report((10,12,11,3),length=2500))
        self.assertIn("text_length_needs_editorial_check",result["reasons"])

    def test_invalid_numeric_is_not_made_up(self):
        sample=report()
        sample["editorial_threshold"]["checks"]["modernbert"]["index_0_100"]=float("nan")
        result=triage(sample)
        self.assertEqual(result["status"],"incomplete")
        self.assertIsNone(result["model_scores"]["modernbert"])

if __name__=="__main__":
    unittest.main()
