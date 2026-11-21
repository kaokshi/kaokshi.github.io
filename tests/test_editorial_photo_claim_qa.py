"""Unit tests for non-publishing photo-claim evaluator."""
import io
import json
import sys
import unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_photo_claim_qa import review_photo_claim,VisionPending
from editorial_new_photo_benchmark import PHOTOS

ENV={"ZEN_IMAGE_VISION_PILOT":"true","ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED":"true",
     "ZEN_IMAGE_ALLOW_PAID":"false","CLOUDFLARE_ACCOUNT_ID":"a"*32,
     "CLOUDFLARE_API_TOKEN":"fake"}

def picture():
    b=io.BytesIO();Image.new("RGB",(256,256),"blue").save(b,format="JPEG")
    return b.getvalue()

class Mock:
    status_code=200
    def __init__(self,response):
        self.content=json.dumps({"success":True,"result":{"response":json.dumps(response)}}).encode()
    def json(self):return json.loads(self.content)

class Tests(unittest.TestCase):
    def test_six_independent_photo_file_pages(self):
        self.assertEqual(len(PHOTOS),6)
        self.assertEqual(len({p["title"] for p in PHOTOS}),6)

    def test_model_success_never_auto_approves(self):
        def post(url,headers,json,timeout):
            self.assertIn("image_url",str(json))
            return Mock({"claim_matches_visible_subject":True,"uncertain":False,
                         "observable_evidence":"Round blue planet against black space"})
        result=review_photo_claim(picture(),{"claim":"A blue planet visible from space"},env=ENV,post=post)
        self.assertEqual(result["status"],"needs_independent_validation")
        self.assertIs(result["semantic_verified"],False)

    def test_uncertain_cannot_pass(self):
        result=review_photo_claim(picture(),{"claim":"The visible object is a planet"},
            env=ENV,post=lambda *a,**k:Mock({"claim_matches_visible_subject":True,
                "uncertain":True,"observable_evidence":"unclear"}))
        self.assertEqual(result["status"],"reject_candidate")

    def test_no_paid_fallback(self):
        with self.assertRaisesRegex(VisionPending,"Paid"):
            review_photo_claim(picture(),{"claim":"The object looks like a planet"},
                env={**ENV,"ZEN_IMAGE_ALLOW_PAID":"true"},
                post=lambda *a,**k:self.fail("Should not call API"))
if __name__=="__main__":unittest.main()
