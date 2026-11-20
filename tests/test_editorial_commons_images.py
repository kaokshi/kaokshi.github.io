"""Zero-network tests: retrieve any license category without granting rights."""
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_commons_images import candidate, search


def sample(license_name="CC BY 4.0", url="https://upload.wikimedia.org/wikipedia/commons/a/ab/test.jpg"):
    return {"ns":6,"title":"File:Test.jpg","imageinfo":[{"url":url,
        "descriptionurl":"https://commons.wikimedia.org/wiki/File:Test.jpg",
        "width":2000,"height":1200,"extmetadata":{
            "LicenseShortName":{"value":license_name},
            "Artist":{"value":"<span>Anna Artist</span>"},
            "AttributionRequired":{"value":"true"},
            "LicenseUrl":{"value":"https://creativecommons.org/licenses/by/4.0/"},
        }}]}


class DummyResponse(BytesIO):
    pass


def opener(req, timeout):
    assert "commons.wikimedia.org/w/api.php" in req.full_url
    assert "gsrnamespace=6" in req.full_url
    return DummyResponse(json.dumps({"query":{"pages":{"1":sample(),"2":sample("All rights reserved")}}}).encode())


class CommonsTests(unittest.TestCase):
    def test_real_api_response_structure_and_no_false_rights(self):
        result=search("red sprites lightning",opener=opener)
        self.assertEqual(result["count"],2)
        item=result["candidates"][0]
        self.assertEqual(item["license_claimed"],"CC-BY-4.0")
        self.assertEqual(item["artist"],"Anna Artist")
        self.assertFalse(item["license_verified"])
        self.assertFalse(item["semantic_match_verified"])
        self.assertFalse(item["editor_approved"])
        self.assertFalse(result["publication_authorized"])

    def test_rejects_proprietary_and_untrusted_media(self):
        restricted = candidate(sample("All rights reserved"))
        self.assertIsNotNone(restricted)
        self.assertIsNone(restricted["license_claimed"])
        self.assertEqual(restricted["license_status"], "unknown_or_restricted")
        self.assertFalse(restricted["license_verified"])
        self.assertIsNone(candidate(sample(url="https://evil.example/test.jpg")))
        bad=sample()
        bad["title"]="File:Test.svg"
        self.assertIsNone(candidate(bad))

    def test_input_limits(self):
        with self.assertRaises(ValueError):
            search("",opener=opener)
        with self.assertRaises(ValueError):
            search("legends",limit=500,opener=opener)

if __name__=="__main__":
    unittest.main()
