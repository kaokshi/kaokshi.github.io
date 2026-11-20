"""Recover an invalid Qwen writer response without silently fabricating prose."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_writer import write_article, writer_shape_problem, WriterOutputError

TITLE="Запечатанная квартира"
CTA="Подпишитесь на канал «Легенды тёмных улиц», чтобы узнать новые истории."


def response():
    return {"title":TITLE,"paragraphs":["В подъезде погас свет. "*100,
                                         "За дверью раздался звонок. "*100,
                                         "Старый сторож поднялся наверх. "*100],
            "cta":CTA}


class QwenRecoveryTests(unittest.TestCase):
    def test_specific_diagnostic_does_not_expose_private_text(self):
        a=response()
        a["cta"]="Читайте наши новые истории"
        self.assertEqual(writer_shape_problem(a,"Легенды тёмных улиц"),"cta_channel_name_missing")
        a["cta"]=None
        self.assertEqual(writer_shape_problem(a,"Легенды тёмных улиц"),"cta_missing")

    def test_one_retry_regenerates_whole_article_after_cta_failure(self):
        bad=response()
        bad["cta"]="Читайте другие истории"
        calls=[]
        def provider(task,request):
            calls.append(request)
            return bad if len(calls)==1 else response()
        article=write_article("legends","Звонок из запечатанной квартиры",provider,allow_length_repair=True)
        self.assertEqual(article["status"],"draft")
        self.assertTrue(article["release_hold"])
        self.assertEqual(len(calls),2)
        self.assertIn("cta_channel_name_missing",calls[1]["instruction"])
        self.assertNotIn("Читайте другие истории",calls[1]["instruction"])

    def test_second_bad_response_returns_reason_and_stops(self):
        bad=response()
        bad["cta"]=""
        calls=[]
        def provider(task,request):
            calls.append(1)
            return bad
        with self.assertRaisesRegex(WriterOutputError,"cta_missing"):
            write_article("legends","Дом с тайным номером",provider,allow_length_repair=True)
        self.assertEqual(len(calls),2)

    def test_normal_response_does_not_retry(self):
        calls=[]
        def provider(task,request):
            calls.append(task)
            return response()
        write_article("legends","Дом у моста на рассвете",provider,allow_length_repair=True)
        self.assertEqual(calls,["write"])


if __name__=="__main__":
    unittest.main()
