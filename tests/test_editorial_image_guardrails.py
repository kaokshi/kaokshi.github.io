"""Deterministic crop false-accept regression."""
import io
from pathlib import Path
import sys
import unittest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_image_guardrails import detect_severe_crop


def image():
    im=Image.new("RGB",(256,256))
    for y in range(256):
        for x in range(256):
            im.putpixel((x,y),((x*y)//255,(x+2*y)%256,(5*x+y)%256))
    b=io.BytesIO()
    im.save(b,format="JPEG",quality=95)
    return b.getvalue()


class CropChecks(unittest.TestCase):
    def test_identity_is_not_blocked(self):
        original=image()
        self.assertEqual(detect_severe_crop(original,original)["status"],"pass")

    def test_aggressive_centre_crop_rejected(self):
        original=image()
        im=Image.open(io.BytesIO(original))
        im=im.crop((72,72,184,184)).resize((256,256))
        b=io.BytesIO()
        im.save(b,format="JPEG",quality=92)
        result=detect_severe_crop(original,b.getvalue())
        self.assertEqual(result["status"],"reject",result)

    def test_different_aspect_ratio_abstains(self):
        b=io.BytesIO()
        Image.new("RGB",(1280,720),"black").save(b,format="JPEG")
        self.assertEqual(detect_severe_crop(image(),b.getvalue())["status"],"abstain")

if __name__=="__main__":
    unittest.main()
