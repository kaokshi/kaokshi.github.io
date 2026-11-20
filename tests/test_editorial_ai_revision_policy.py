"""First-party-only AI revision policy: no false claims of validated thresholds."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import editorial_ai_revision_policy as module

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy=json.loads((ROOT/"config/ai_revision_policy.json").read_text())
        cls.report={
            "whole_document_index_percent":99.9,
            "checks":{x:{"status":"completed"} for x in cls.policy["only_detectors"]}
        }

    def test_current_policy_never_rewrites_solely_from_model_percent(self):
        for ch in ("legends","science"):
            result=module.decide_revision(ch,self.report,self.policy)
            self.assertEqual(result["decision"],"diagnostic_only")
            self.assertFalse(result["revision_required"])
            self.assertIsNone(result["threshold"])

    def test_paused_channel_not_excluded_from_future_policy(self):
        self.assertEqual(module.decide_revision("cinema",self.report,self.policy)["decision"],"not_active")
        self.assertIn("cinema",self.policy["paused_channels"])

    def test_incomplete_tools_do_not_get_fake_ai_rewrite_verdict(self):
        a=copy.deepcopy(self.report)
        a["checks"]["lightgbm"]["status"]="error"
        self.assertEqual(module.decide_revision("legends",a,self.policy)["decision"],"incomplete_ai_tests")

    def test_old_test_candidates_fail_validation(self):
        req=self.policy["validation_requirements"]
        for fp,h,detected,ai in [(0,60,13,60),(4,60,38,60)]:
            row={"independently_verified_provenance":True,
                 "no_overlap_with_calibration_or_model_training":True,
                 "frozen_threshold_before_holdout":True,
                 "human_holdout_n":h,"ai_holdout_n":ai,
                 "human_false_alarms":fp,"ai_detected":detected}
            self.assertFalse(module.qualify_validation(row,req))

    def test_synthetic_rehearsal_never_enables_real_policy(self):
        sandbox=copy.deepcopy(self.policy)
        sandbox["status"]="validated"
        sandbox["thresholds"]["legends"]=70
        sandbox["validation_requirements"]["minimum_ai_detection_rate"]=.6
        synthetic={"independently_verified_provenance":True,
                   "no_overlap_with_calibration_or_model_training":True,
                   "frozen_threshold_before_holdout":True,
                   "human_holdout_n":500,"ai_holdout_n":500,
                   "human_false_alarms":0,"ai_detected":450}
        self.assertTrue(module.qualify_validation(synthetic,sandbox["validation_requirements"]))
        self.assertEqual(module.decide_revision("legends",self.report,sandbox,synthetic)["decision"],
                         "targeted_editorial_revision")
        self.assertEqual(self.policy["status"],"owner_selected_unvalidated_editorial_triage")

    def test_higher_signal_not_ai_probability(self):
        self.assertIsNone(self.policy["thresholds"]["legends"])
        self.assertIsNone(self.policy["rubert_hard_limit"])
        self.assertIsNone(self.policy["thresholds"]["science"])

if __name__=="__main__":
    unittest.main()
