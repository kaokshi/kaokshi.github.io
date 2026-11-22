"""Offline contract: Groq writer, Gemini expander, Cloudflare independent editor."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from compare_groq_gemini_cloudflare_triad import run

TEXT=("Название\n\n"+"\n\n".join(
   "**Раздел "+str(i)+"**\n"+"Описание нового события и деталей. "*70
   for i in range(1,6))+"\n\nПодписывайтесь на канал.")

class TriadTests(unittest.TestCase):
    def test_no_secrets_no_calls(self):
        def forbid(*args,**kwargs):
            raise AssertionError("must not call")
        with TemporaryDirectory() as temp:
            output=run(temp,env={},writer=forbid,third=forbid,discovery=forbid)
        self.assertEqual(output["status"],"blocked_missing_secrets")
        self.assertEqual(output["calls"],{"groq":0,"gemini":0,"cloudflare":0})

    def test_exact_order_and_budget_on_three_channels(self):
        calls=[]
        def writer(provider,prompt,env):
            calls.append(provider)
            if provider=="gemini":
                self.assertIn("ЧЕРНОВИК GROQ:",prompt)
            return TEXT,{"tokens":100}
        def third(model,prompt,env):
            calls.append("cloudflare")
            self.assertIn("ОРИГИНАЛ GROQ:",prompt)
            self.assertIn("РЕДАКТУРА GEMINI:",prompt)
            return TEXT,{"tokens":50}
        with TemporaryDirectory() as temp:
            output=run(temp,env={"GROQ_API_KEY":"test","GEMINI_API_KEY":"test",
                                  "CLOUDFLARE_API_TOKEN":"test",
                                  "CLOUDFLARE_ACCOUNT_ID":"test"},
                       writer=writer,third=third)
        self.assertEqual(calls,["groq","gemini","cloudflare"]*3)
        self.assertEqual(output["calls"],{"groq":3,"gemini":3,"cloudflare":3})
        self.assertEqual(output["status"],"three_stage_test_completed")
        self.assertFalse(output["publication_authorized"])
        self.assertFalse(output["paid_calls_authorized"])
        self.assertIsNone(output["observed_reader_retention"])

    def test_failure_skips_future_stages(self):
        calls=[]
        def writer(provider,prompt,env):
            calls.append(provider)
            if provider=="gemini":
                raise ValueError("offline simulated failure")
            return TEXT,{}
        def third(model,prompt,env):
            raise AssertionError("critic must be skipped")
        with TemporaryDirectory() as temp:
            output=run(temp,env={"GROQ_API_KEY":"test","GEMINI_API_KEY":"test",
                                  "CLOUDFLARE_API_TOKEN":"test",
                                  "CLOUDFLARE_ACCOUNT_ID":"test"},
                       writer=writer,third=third)
        self.assertEqual(output["calls"]["cloudflare"],0)
        self.assertEqual(output["status"],"partial_or_failed")

if __name__=="__main__":
    unittest.main()
