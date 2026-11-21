"""Offline regression tests for experimental editorial guards."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"experiments"))
import clip_caption_review as clip
import reviewdog_editorial as dog

class EditorialToolsTests(unittest.TestCase):
    def test_clip_flags_wrong_caption_but_does_not_verify_scene(self):
        result=clip.interpret([[.1,.5,.01],[.2,.6,.01]],["a.jpg","b.jpg"],
                              ["caption one","caption two"],["unrelated"])
        self.assertEqual(result[0]["status"],"editor_review_candidate")
        self.assertEqual(result[1]["status"],"caption_ranked_first_not_verified")
        self.assertFalse(result[0]["visual_scene_verified"])
        self.assertFalse(result[1]["film_identity_verified"])
        self.assertFalse(result[0]["image_rights_verified"])

    def test_reviewdog_genuine_diagnostics_only(self):
        short=dog.diagnostics(["Утром герой вышел из дома."],"legends")
        self.assertEqual(short,[])
        long=dog.diagnostics([" ".join(["длинное"]*24)+"."],"legends")
        self.assertEqual(len(long),1)
        self.assertEqual(long[0]["line"],1)
        self.assertIn("подсказка",long[0]["message"].lower())

if __name__=="__main__":
    unittest.main()
