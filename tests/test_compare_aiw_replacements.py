import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from compare_aiw_replacements import merge_results, score_from_tool

def result(score):
    return {"status":"completed","data":{"score_percent":score}}

def sample(modern=20, light=40, bert=60, frida=90, accent=.09):
    return {"modernbert":result(modern),"lightgbm":result(light),
        "transformer":result(bert),"mini-frida":result(frida),
        "ai_accent_detector":{"status":"completed","data":{"ai_accent_score":accent}}}

class CompareTests(unittest.TestCase):
    def test_separate_replacements_and_one_index_each(self):
        x=merge_results(sample())
        self.assertEqual(x["variants"]["transformer"]["score_index_0_100"],40)
        self.assertEqual(x["variants"]["mini-frida"]["score_index_0_100"],50)
        self.assertEqual(x["ai_accent_style_index_0_100"],9)
        self.assertIsNone(x["variants"]["transformer"]["calibrated_ai_probability"])
        self.assertFalse(x["publication_authorized"])

    def test_failed_model_does_not_produce_fake_index(self):
        raw=sample()
        raw["mini-frida"]={"status":"error","reason":"Download failed"}
        x=merge_results(raw)
        self.assertIsNone(x["variants"]["mini-frida"]["score_index_0_100"])
        self.assertEqual(x["variants"]["transformer"]["score_index_0_100"],40)

    def test_unavailable_base_checks_fail_both(self):
        raw=sample()
        raw["modernbert"]["data"]["score_percent"]=float("nan")
        x=merge_results(raw)
        for x0 in x["variants"].values():
            self.assertIsNone(x0["score_index_0_100"])

    def test_zero_and_hundred(self):
        x=merge_results(sample(0,0,0,100))
        self.assertEqual(x["variants"]["transformer"]["score_index_0_100"],0)
        self.assertEqual(x["variants"]["mini-frida"]["score_index_0_100"],round(100/3,2))

    def test_boolean_invalid(self):
        self.assertIsNone(score_from_tool(result(True)))

if __name__=="__main__":
    unittest.main()
