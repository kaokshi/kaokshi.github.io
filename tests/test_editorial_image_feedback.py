"""No network/API regression for optional Klein visual feedback mode."""
import os
import unittest
from unittest.mock import patch

from src import editorial_images as images
from src import editorial_image_feedback as feedback
from src.editorial_providers import Blocked


SCENE = {"section": 2, "kind": "illustration",
         "subject": "an abandoned metro platform with an old train",
         "brief": "Old train stopped at an abandoned metro platform, doors open, one amber lamp.",
         "must_include": ["old train", "open doors", "metro platform"],
         "must_avoid": ["empty tunnel without train", "crowds"]}


class Memory:
    def __init__(self): self.calls=0
    def time_left(self): return 2000
    def call(self, task, article, provider, fn):
        self.calls += 1
        return fn()


class Index:
    def __init__(self): self.memory=Memory(); self.saved=[]
    def save(self, raw, metadata, scene, subject, path):
        self.saved.append((raw,metadata))
        return metadata


class FeedbackTests(unittest.TestCase):
    def test_repair_prompt_is_new_generation_not_fake_edit(self):
        prompt=feedback.repair_prompt(SCENE, {"florence_caption":"A dim empty tunnel."},
                                     style="subdued night")
        for expected in ("GENERATE A NEW IMAGE FROM SCRATCH", "old train",
                         "open doors", "empty tunnel without train", "subdued night"):
            self.assertIn(expected,prompt)
        self.assertNotIn("preserve exact pixels",prompt)

    def test_scene_spec_rejects_malformed_constraints(self):
        with self.assertRaises(ValueError):
            feedback.scene_spec({**SCENE,"must_include":"train"})

    def test_reviewer_rejects_low_relative_margin(self):
        row=feedback.review_decision(SCENE,{"siglip_relative_margin":-0.7,
                                             "florence_caption":"Empty tunnel"})
        self.assertFalse(row["passed"])

    def test_first_failed_then_regenerated_and_saved_once(self):
        index=Index()
        observed=iter([
            {"siglip_model":"sig","florence_model":"flo","siglip_relative_margin":-1.5,
             "florence_caption":"An empty train tunnel with no carriage."},
            {"siglip_model":"sig","florence_model":"flo","siglip_relative_margin":3.0,
             "florence_caption":"An old train at a deserted station."},
        ])
        prompts=[]
        def fake_klein(prompt):
            prompts.append(prompt)
            return b"jpeg bytes", {"width":1024,"height":768}
        with patch.dict(os.environ,{"EDITORIAL_IMAGE_FEEDBACK_EXPERIMENT":"true"}), \
             patch.object(images,"warm_visual_matcher"), \
             patch.object(images,"klein",side_effect=fake_klein), \
             patch.object(feedback,"inspect_visual",side_effect=lambda *_:next(observed)):
            row=images.generated(SCENE,"metro article","unused.jpg",index,"job","legends-01","legends")
        self.assertEqual(index.memory.calls,2)
        self.assertEqual(len(index.saved),1)
        self.assertEqual(row["visual_feedback"]["generation_attempt"],2)
        self.assertIn("GENERATE A NEW IMAGE FROM SCRATCH",prompts[1])

    def test_unavailable_reviewer_fails_closed(self):
        index=Index()
        with patch.dict(os.environ,{"EDITORIAL_IMAGE_FEEDBACK_EXPERIMENT":"true"}), \
             patch.object(images,"warm_visual_matcher"), \
             patch.object(images,"klein",return_value=(b"test",{})), \
             patch.object(feedback,"inspect_visual",side_effect=Blocked("visual_feedback_unavailable")):
            with self.assertRaisesRegex(Blocked,"visual_feedback_unavailable"):
                images.generated(SCENE,"metro","unused.jpg",index,"job","legends-01","legends")
        self.assertEqual(len(index.saved),0)

    def test_disabled_uses_original_path(self):
        index=Index()
        with patch.dict(os.environ,{"EDITORIAL_IMAGE_FEEDBACK_EXPERIMENT":"false"}), \
             patch.object(images,"warm_visual_matcher"), \
             patch.object(images,"klein",return_value=(b"test",{})), \
             patch.object(feedback,"inspect_visual",side_effect=AssertionError("must not call")):
            row=images.generated(SCENE,"metro","unused.jpg",index,"job","legends-01","legends")
        self.assertNotIn("visual_feedback",row)
        self.assertEqual(index.memory.calls,1)


if __name__=="__main__": unittest.main()
