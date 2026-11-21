"""Proof that edits are specific, optional reference is transmitted and semantics are not trusted."""
import base64
import io
import json
import sys
import unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))

from editorial_scene_contract import (make_contract, validate_contract, controlled_edit_prompt,
                                      structural_verdict, fingerprint, select_reviewed_variant)
from editorial_cloudflare_refine import refine_image
from editorial_cloudflare import CloudflarePending


def jpeg(size):
    b=io.BytesIO()
    img=Image.new("RGB",size)
    for y in range(size[1]):
        for x in range(size[0]):
            img.putpixel((x,y), (x%256,y%256,(x+y)%256))
    img.save(b,format="JPEG")
    return b.getvalue()


class Fake:
    status_code=200
    def __init__(self,data):
        self.content=json.dumps({"success":True,"result":{"image":base64.b64encode(data).decode()}}).encode()
    def json(self):
        return json.loads(self.content)

ENV={"CLOUDFLARE_ACCOUNT_ID":"a"*32,"CLOUDFLARE_API_TOKEN":"test",
     "ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED":"true","ZEN_IMAGE_ALLOW_PAID":"false"}

class SceneContractTests(unittest.TestCase):
    def test_editor_preserves_scene_and_changes_only_requested_defect(self):
        c=make_contract("Young woman crosses apartment corridor toward a snowy forest",
                        subject="Same dark-haired woman in green coat",
                        location="Apartment hallway, door leads to forest",
                        action="Woman walks toward the door",
                        must_keep=["door shows forest from the corridor"],
                        fix="Remove the bench blocking the passage",
                        forbid=["Do not move the door to the forest"])
        p=controlled_edit_prompt(c)
        self.assertIn("Remove the bench",p)
        self.assertIn("door shows forest",p)
        self.assertIn("NOT a new scene",p)
        self.assertIn("Do not move the door",p)
        self.assertEqual(len(fingerprint(c)),64)

    def test_invalid_contract_never_silently_changes_style(self):
        c=make_contract("A woman stands before a door")
        c["style"]="cartoon"
        with self.assertRaisesRegex(ValueError,"style"):
            validate_contract(c)

    def test_structure_cannot_pretend_to_verify_semantics(self):
        verdict=structural_verdict(jpeg((100,100)),jpeg((1280,720)))
        self.assertEqual(verdict["status"],"needs_visual_review")
        self.assertIs(verdict["semantic_verified"],False)
        self.assertEqual(structural_verdict(jpeg((100,100)),jpeg((1024,1024)))["status"],"reject")

    def test_visual_review_must_match_exact_candidate(self):
        from hashlib import sha256
        source, edited = b"original pixels", b"edited pixels"
        evidence = {"reviewer_kind": "human",
                    "original_sha256": sha256(source).hexdigest(),
                    "edited_sha256": sha256(edited).hexdigest(),
                    "decision": "approve",
                    "same_story": True, "same_character": True,
                    "same_location": True, "same_visual_style": True,
                    "targeted_fix_successful": True, "no_new_defects": True}
        chosen, record = select_reviewed_variant(source, edited, evidence)
        self.assertEqual(chosen, edited)
        rejected = dict(evidence, same_story=False)
        chosen, record = select_reviewed_variant(source, edited, rejected)
        self.assertEqual(chosen, source)
        with self.assertRaisesRegex(ValueError, "stale"):
            select_reviewed_variant(source, b"unexpected output", evidence)

    def test_extra_reference_separate_and_small(self):
        def post(url,headers,files,data,timeout):
            self.assertIn("input_image_0",files)
            self.assertIn("input_image_1",files)
            self.assertEqual(data["width"],"1280")
            with Image.open(io.BytesIO(files["input_image_1"][1])) as im:
                self.assertLess(max(im.size),512)
            return Fake(jpeg((1280,720)))
        _,meta=refine_image(jpeg((128,128)),"Make only the requested change",env=ENV,
                            style_reference=jpeg((100,100)),post=post)
        self.assertTrue(meta["style_reference_used"])

if __name__=="__main__":
    unittest.main()
