"""Detector-to-Klein integration invariants without live inference or billed API."""
import unittest
from unittest.mock import patch
from src import editorial_object_detector as detect
from src import editorial_image_feedback as feedback
from src.editorial_visual_constraints import evaluate

SCENE={
 "subject":"four wall clocks in old metro control room",
 "brief":"An abandoned metro control room with four separate round wall clocks",
 "count_requirements":[{"object":"wall clock","mode":"exact","value":4}],
}
class Tests(unittest.TestCase):
    def test_iou_and_no_duplicate(self):
        self.assertEqual(detect._iou([0,0,10,10],[20,20,30,30]),0)
        self.assertGreater(detect._iou([0,0,10,10],[1,1,11,11]),.65)
    def test_detector_mismatch_explains_wrong_count(self):
        evidence={"detections":{"wall clock":{"count":2,"boxes":[],"scores":[]}}}
        notes=detect.discrepancy_instructions(SCENE,evidence)
        self.assertEqual(len(notes),1)
        self.assertIn("identified 2 instances",notes[0])
        prompt=feedback.repair_prompt(SCENE,{"florence_caption":"A room with two clocks",
                                             "object_detector":evidence},style="dark metro")
        self.assertIn("EXACTLY 4",prompt)
        self.assertIn("identified 2 instances",prompt)
    def test_detector_evidence_does_not_silently_become_approval(self):
        row={"siglip_relative_margin":10,"florence_caption":"Four clocks on wall",
             "object_detector":{"detections":{"wall clock":{"count":4}},
                                "independent_of_captioner":True,"verified_independently":False}}
        verdict=evaluate(SCENE,row)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["status"],"owner_review_required")
    def test_scene_calls_detector_for_exact_count(self):
        with patch.object(detect,"objects",return_value={"detections":{"wall clock":{"count":4}}}) as mocked:
            got=detect.evidence_for_scene(b"unused",SCENE)
        self.assertEqual(got["detections"]["wall clock"]["count"],4)
        self.assertEqual(mocked.call_args.args[1],["wall clock"])
    def test_unspecified_count_yields_no_fake_data(self):
        self.assertEqual(detect.evidence_for_scene(b"",{"subject":"street","brief":"a street"})["detections"],{})
if __name__=="__main__":
    unittest.main()
