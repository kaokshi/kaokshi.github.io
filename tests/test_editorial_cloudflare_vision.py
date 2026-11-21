"""Qwen vision pilot tests; never equate an unvalidated model answer with approval."""
import io
import json
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_cloudflare_vision import parse_review, review_pair, VisionPending
from editorial_scene_contract import make_contract

def img():
    out=io.BytesIO()
    Image.new("RGB",(128,128),"navy").save(out,format="JPEG")
    return out.getvalue()

CONTRACT=make_contract("Woman stands in an apartment corridor beside a doorway to a forest.",
   subject="A woman in green coat",location="Apartment corridor leading to winter forest",
   fix="Remove the bench blocking the passage")

class Fake:
    status_code=200
    def __init__(self,payload):
        self.content=json.dumps(payload).encode()
    def json(self):return json.loads(self.content)

ENV={"ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED":"true","ZEN_IMAGE_ALLOW_PAID":"false",
     "ZEN_IMAGE_VISION_PILOT":"true","CLOUDFLARE_ACCOUNT_ID":"a"*32,"CLOUDFLARE_API_TOKEN":"fake"}

class Tests(unittest.TestCase):
    def test_review_is_not_automatic_approval(self):
        keys=("same_story","same_character","same_location","same_visual_style",
              "targeted_fix_successful","no_new_defects")
        result=parse_review(json.dumps({"checks":{k:True for k in keys},"defects":[]}),img(),img())
        self.assertEqual(result["status"],"needs_independent_validation")
        self.assertIs(result["semantic_verified"],False)
        self.assertEqual(result["reviewer_kind"],"unvalidated_vision")

    def test_missing_field_is_not_success(self):
        with self.assertRaises(VisionPending):
            parse_review('{"checks":{"same_story":true},"defects":[]}',img(),img())

    def test_image_bytes_are_real_in_request(self):
        def fake(url,headers,json,timeout):
            self.assertIn("@cf/qwen/qwen3.8-27b",url)
            content=json["messages"][1]["content"]
            self.assertEqual(len([x for x in content if x["type"]=="image_url"]),2)
            keys=("same_story","same_character","same_location","same_visual_style",
                  "targeted_fix_successful","no_new_defects")
            response='{"checks":'+__import__("json").dumps({k:True for k in keys})+',"defects":[]}'
            return Fake({"success":True,"result":{"response":response}})
        result=review_pair(img(),img(),CONTRACT,env=ENV,post=fake)
        self.assertEqual(result["status"],"needs_independent_validation")

    def test_pilot_disabled_no_api_call(self):
        with self.assertRaisesRegex(VisionPending,"disabled"):
            review_pair(img(),img(),CONTRACT,env={**ENV,"ZEN_IMAGE_VISION_PILOT":"false"},
               post=lambda *a,**k: self.fail("API called"))

if __name__=="__main__":unittest.main()
