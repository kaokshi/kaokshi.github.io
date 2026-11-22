"""Offline contract checks for the alternative editor sequence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research"))
from compare_groq_gemini_critics import run,CRITICS,independent_critique

class NewSequenceTests(unittest.TestCase):
    def test_missing_keys_block_without_model_calls(self):
        def forbid(*args,**kwargs):
            raise AssertionError("must not call")
        with TemporaryDirectory() as tmp:
            r=run(tmp,env={},writer=forbid)
            self.assertEqual(r["generated"],0)
            self.assertEqual(r["status"],"blocked_missing_secret")

    def test_no_critic_key_reports_blocked_not_passed(self):
        result=independent_critique("mistral","old","new","science",env={})
        self.assertEqual(result["status"],"blocked_missing_configuration")

    def test_groq_first_gemini_second_and_critics_do_not_claim_retention(self):
        calls=[]
        def writer(backend,prompt,env):
            calls.append(backend)
            if backend=="gemini":
                self.assertIn("Оригинал Groq",prompt)
            return ("Заголовок\\n\\n**Первый раздел**\\nПроверочный текст. "
                    "**Второй раздел**\\nЕщё текст. Подписывайтесь."),{"tokens":10}
        def critic(provider,source,improved,channel,env):
            calls.append(provider)
            self.assertIn("Заголовок",source)
            self.assertIn("Заголовок",improved)
            return {"status":"blocked_missing_configuration"}
        with TemporaryDirectory() as tmp:
            r=run(tmp,env={"GROQ_API_KEY":"fake","GEMINI_API_KEY":"fake"},
                  writer=writer,critic=critic)
            self.assertEqual(r["generated"],3)
            self.assertEqual(calls,
                ["groq","gemini","mistral","sambanova","cloudflare"]*3)
            self.assertIsNone(r["real_reader_retention"])
            self.assertFalse(r["publication_authorized"])
            self.assertTrue(all(e["human_interest_review"]=="pending"
                                for e in r["channels"]))

if __name__=="__main__":
    unittest.main()
