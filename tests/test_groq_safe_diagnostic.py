"""Synthetic unit tests for privacy-safe Groq diagnostics."""
import io
import json
import sys
from pathlib import Path
import unittest
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from diagnose_groq_api import diagnostic_error,run

def fail(status,payload):
    raw=json.dumps(payload).encode()
    return HTTPError("https://api.groq.com/openai/v1/models",status,
                     "Forbidden",{},io.BytesIO(raw))

class GroqDiagnosticTests(unittest.TestCase):
    def test_classifies_known_model_permissions_without_leaking_tokens(self):
        e=fail(403,{"error":{"message":"model_permission_blocked_org gsk_fake_private_key",
                             "type":"permission_error","code":"model_permission_blocked_org"}})
        info=diagnostic_error(e)
        self.assertEqual(info["classification"],"organization_permission")
        self.assertEqual(info["error_code"],"model_permission_blocked_org")
        self.assertNotIn("gsk_fake_private_key",str(info))

    def test_freeform_private_details_not_reflected(self):
        e=fail(403,{"error":{"message":"unknown user@example.com gsk_fake_private",
                             "code":"gsk_fake_private","type":"unknown-private-type"}})
        info=diagnostic_error(e)
        self.assertEqual(info["error_code"],"unclassified")
        self.assertEqual(info["error_type"],"unknown")
        self.assertNotIn("gsk_fake_private",str(info))

    def test_html_forbidden_response_is_only_non_json(self):
        e=HTTPError("https://api.groq.com/openai/v1/models",403,"Forbidden",{},
                    io.BytesIO(b"<html>Cloud proxy raw error</html>"))
        info=diagnostic_error(e)
        self.assertEqual(info["classification"],"non_json_response")

    def test_missing_key_never_sends_request(self):
        def disallow(*args,**kwargs):
            raise AssertionError("network must not be called")
        info=run({},request_fn=disallow)
        self.assertEqual(info["status"],"missing_secret")
        self.assertFalse(info["key_present"])

    def test_request_error_exposes_only_code_and_safe_category(self):
        def mock_request(request,timeout):
            self.assertEqual(request.full_url,
                             "https://api.groq.com/openai/v1/models")
            raise fail(403,{"error":{"message":"Service not available in your region",
                                     "type":"permission_error","code":"blocked"}})
        info=run({"GROQ_API_KEY":"gsk_fake_key"},request_fn=mock_request)
        self.assertEqual(info["models_api"]["classification"],
                         "geographic_restriction")
        self.assertNotIn("gsk_fake_key",str(info))

if __name__=="__main__":
    unittest.main()
