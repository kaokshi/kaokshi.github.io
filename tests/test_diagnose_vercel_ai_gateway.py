"""No-network tests for Vercel AI Gateway read-only connectivity."""
import io
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from diagnose_vercel_ai_gateway import diagnose

class FakeHTTP:
    def __init__(self,data):
        self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,*args):return json.dumps(self.data).encode()

class VercelDiagnosticTests(unittest.TestCase):
    def test_missing_secret_never_calls_api(self):
        def fail(*args,**kwargs):
            raise AssertionError("Unexpected request")
        self.assertEqual(diagnose({},opener=fail)["status"],"blocked_missing_secret")
    def test_read_only_catalog_does_not_generate_or_claim_free(self):
        observed=[]
        def fake(request,timeout):
            observed.append((request.get_method(),request.full_url))
            return FakeHTTP({"data":[{"id":"example/test"}]})
        out=diagnose({"AI_GATEWAY_API_KEY":"dummy"},opener=fake)
        self.assertEqual(out["status"],"authenticated_model_catalog_access")
        self.assertEqual(observed,[("GET","https://ai-gateway.vercel.sh/v1/models")])
        self.assertFalse(out["generation_checked"])
        self.assertFalse(out["free_credit_eligibility_checked"])
        self.assertEqual(out["chargeable_calls"],0)

if __name__=="__main__":
    unittest.main()
