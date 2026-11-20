"""Model-ensemble dispute analysis never turns scores into probability or proof."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_model_agreement import audit

def report(rubert="human",modern=.02,light=.03,accent=.7):
    return {"checks":{
        "rubert_v4":{"status":"completed","data":{"prediction_native":rubert}},
        "modernbert":{"status":"completed","data":{"probability":modern}},
        "lightgbm":{"status":"completed","data":{"probability":light}},
        "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":accent}}
    }}

class AgreementTests(unittest.TestCase):
    def test_agreed_human_is_not_disputed_even_with_style_signal(self):
        d=audit(report())
        self.assertEqual(d["status"],"completed")
        self.assertFalse(d["disputed"])
        self.assertEqual(d["family_decisions"]["ai_accent_index_0_100"],70)

    def test_agreed_ai_is_not_disputed(self):
        self.assertFalse(audit(report("ai",.96,.83))["disputed"])

    def test_aiw_internal_conflict_is_disputed(self):
        d=audit(report("human",.91,.1))
        self.assertTrue(d["disputed"])
        self.assertIn("aiw_family_internal_disagreement",d["reason_codes"])

    def test_rubert_against_consistent_aiw_disputed(self):
        d=audit(report("human",.95,.91))
        self.assertTrue(d["disputed"])
        self.assertIn("rubert_vs_aiw_family_disagreement",d["reason_codes"])

    def test_missing_data_is_unknown_not_disagreement(self):
        d=report()
        del d["checks"]["lightgbm"]["data"]["probability"]
        r=audit(d)
        self.assertEqual(r["status"],"incomplete")
        self.assertFalse(r["disputed"])

    def test_accent_is_style_not_independent_vote(self):
        self.assertFalse(audit(report("human",.04,.01,.99))["disputed"])

if __name__=="__main__":
    unittest.main()
