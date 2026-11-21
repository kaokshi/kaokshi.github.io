"""Regression tests for the dedicated legends visual identity."""
import base64
from io import BytesIO
import json
import sys
from pathlib import Path
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_visual_style import visual_profile, scene_prompt, final_edit_prompt, expected_size
from editorial_cloudflare import CloudflarePending
from editorial_cloudflare_refine import refine_image


class FakeResponse:
    status_code = 200
    def __init__(self, data):
        self.content = json.dumps({"success": True, "result": {
            "image": base64.b64encode(data).decode()}}).encode()
    def json(self):
        return json.loads(self.content)


def jpg(size=(1024, 1024)):
    buf = BytesIO()
    Image.new("RGB", size, "#203549").save(buf, format="JPEG")
    return buf.getvalue()


ENV = {"ZEN_IMAGE_ALLOW_PAID": "false", "ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED": "true",
       "CLOUDFLARE_ACCOUNT_ID": "a" * 32, "CLOUDFLARE_API_TOKEN": "fake"}


class VisualStyleTests(unittest.TestCase):
    def test_shared_style_in_every_prompt(self):
        text = scene_prompt("A woman enters the old hallway.")
        self.assertIn("live-action", text)
        self.assertIn("cinematic", text)
        self.assertIn("hallway", text)
        self.assertIn("No extra characters", text)
        self.assertLessEqual(len(text), 2048)

    def test_refuse_silent_scene_truncation(self):
        with self.assertRaises(ValueError):
            scene_prompt("x" * 2000)

    def test_exact_article_landscape(self):
        self.assertEqual(expected_size("inline"), (1280,720))
        self.assertEqual(expected_size("cover"), (1280,720))

    def test_edit_requires_actual_reference_and_exact_dimensions(self):
        def post(url,headers,files,data,timeout):
            self.assertEqual(data["width"], "1280")
            self.assertEqual(data["height"], "720")
            self.assertIn("input_image_0", files)
            self.assertIn("Bearer fake", headers["Authorization"])
            with Image.open(BytesIO(files["input_image_0"][1])) as image:
                self.assertLess(max(image.size), 512)
            return FakeResponse(jpg((1280,720)))
        data,metadata=refine_image(jpg(),final_edit_prompt("Woman walks through a door"),env=ENV,post=post)
        self.assertEqual(Image.open(BytesIO(data)).size,(1280,720))
        self.assertEqual(metadata["final_width"],1280)

    def test_wrong_dimensions_fail_closed(self):
        def post(*args,**kwargs):
            return FakeResponse(jpg((1024,1024)))
        with self.assertRaisesRegex(CloudflarePending, "wrong image dimensions"):
            refine_image(jpg(),"Some editing instructions",env=ENV,post=post)

    def test_paid_flag_blocks_edit(self):
        with self.assertRaisesRegex(CloudflarePending,"Paid mode"):
            refine_image(jpg(),"Some editing instructions",env={**ENV,"ZEN_IMAGE_ALLOW_PAID":"true"},
                         post=lambda *a,**k: (_ for _ in ()).throw(AssertionError("called")))

if __name__ == "__main__":
    unittest.main()
