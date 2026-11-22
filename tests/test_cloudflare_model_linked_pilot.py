"""No-network contract for bounded Cloudflare + Groq + Gemini comparison."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from cloudflare_model_linked_pilot import MODELS,LIMITS,run

def article():
    # Artificial, not considered good quality: contract tests check call discipline.
    return ("Название\\n\\n"+"\\n\\n".join(
        "**Заголовок "+str(i)+"**\\n"+"Текст наблюдения меняется. "*80
        for i in range(5))+"\\n\\nПодписывайтесь на канал.")

class PilotTests(unittest.TestCase):
    def test_absent_cloudflare_never_calls_providers(self):
        def no(*a,**k):raise AssertionError("Unexpected API")
        with TemporaryDirectory() as tmp:
            obj=run(tmp,env={},cf=no,external=no)
            self.assertEqual(obj["status"],"blocked_cloudflare_secret_absent")
            self.assertEqual(obj["calls"]["cloudflare"],0)
    def test_provider_sequences_never_exceed_budget(self):
        calls=[]
        def cf(model,prompt,env):
            calls.append(("cloudflare",model))
            return article(),{}
        def external(provider,prompt,env):
            calls.append((provider,""))
            return article(),{}
        with TemporaryDirectory() as tmp:
            obj=run(tmp,env={"CLOUDFLARE_API_TOKEN":"fake",
                             "CLOUDFLARE_ACCOUNT_ID":"fake",
                             "GROQ_API_KEY":"fake","GEMINI_API_KEY":"fake"},
                    cf=cf,external=external)
            self.assertEqual(len(MODELS),3)
            self.assertEqual(obj["calls"],{"cloudflare":12,"groq":6,"gemini":3})
            self.assertEqual(len([x for x in calls if x[0]=="cloudflare"]),12)
            self.assertFalse(obj["publication_authorized"])
            self.assertFalse(obj["paid_calls_authorized"])
            self.assertTrue(all(x.get("reader_retention_observed") is None
                        for x in obj["tests"] if x["status"]=="generated"))
    def test_quality_check_never_claims_human_review(self):
        with TemporaryDirectory() as tmp:
            obj=run(tmp,env={"CLOUDFLARE_API_TOKEN":"fake","CLOUDFLARE_ACCOUNT_ID":"fake"},
                    cf=lambda model,prompt,env:(article(),{}))
            self.assertEqual(obj["calls"]["cloudflare"],9)
            self.assertTrue(all(x["human_readability_review"]=="not_completed"
                     for x in obj["tests"] if x["status"]=="generated"))

if __name__=="__main__":
    unittest.main()
