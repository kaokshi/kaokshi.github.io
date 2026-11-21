"""Fail-closed tests for Cloudflare Workers AI illustration adapter."""
import base64
import io
import json
import sys
from pathlib import Path
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from editorial_cloudflare import CloudflarePending, generate_scene

ACCOUNT = "a" * 32
VALID = {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": "not-a-real-token",
         "ZEN_CLOUDFLARE_FREE_PLAN_CONFIRMED": "true", "ZEN_IMAGE_ALLOW_PAID": "false"}


class FakeResponse:
    def __init__(self, data):
        self.data = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, n=-1):
        return self.data.read(n)


class CloudflareTests(unittest.TestCase):
    def test_requires_confirmed_free_plan(self):
        with self.assertRaisesRegex(CloudflarePending, "Verify"):
            generate_scene("dark corridor", env={})

    def test_rejects_paid_mode(self):
        with self.assertRaisesRegex(CloudflarePending, "forbidden"):
            generate_scene("dark corridor", env={**VALID, "ZEN_IMAGE_ALLOW_PAID": "true"})

    def test_requires_keys(self):
        with self.assertRaisesRegex(CloudflarePending, "Account ID"):
            generate_scene("dark corridor", env={**VALID, "CLOUDFLARE_API_TOKEN": ""})

    def test_success_uses_expected_model_and_does_not_expose_key(self):
        data = b"\xff\xd8" + b"real-image-bytes" * 20
        def open_request(request, timeout):
            self.assertEqual(timeout, 120)
            self.assertIn("/ai/run/@cf/black-forest-labs/flux-1-schnell", request.full_url)
            self.assertEqual(json.loads(request.data)["steps"], 4)
            self.assertIn("Bearer not-a-real-token", request.get_header("Authorization"))
            return FakeResponse(json.dumps({"success": True, "result": {
                "image": base64.b64encode(data).decode("ascii")}}).encode())
        output, metadata = generate_scene("foggy courtyard", env=VALID, opener=open_request)
        self.assertEqual(output, data)
        self.assertEqual(metadata["model"], "@cf/black-forest-labs/flux-1-schnell")
        self.assertNotIn(VALID["CLOUDFLARE_API_TOKEN"], str(metadata))

    def test_failed_http_does_not_leak_body(self):
        def fail(*args, **kwargs):
            raise HTTPError("https://example.org", 429, "token-secret", None, None)
        with self.assertRaisesRegex(CloudflarePending, "HTTP 429") as caught:
            generate_scene("foggy courtyard", env=VALID, opener=fail)
        self.assertNotIn("token-secret", str(caught.exception))

    def test_api_error_fails_closed(self):
        def reject(*args, **kwargs):
            return FakeResponse(b'{"success":false,"errors":[{"message":"secret"}]}')
        with self.assertRaisesRegex(CloudflarePending, "rejected") as caught:
            generate_scene("foggy courtyard", env=VALID, opener=reject)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
