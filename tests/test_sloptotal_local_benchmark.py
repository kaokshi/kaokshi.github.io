import io
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sloptotal_local_benchmark as m

class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

class TestSlopTotal(unittest.TestCase):
    def test_extraction(self):
        text=m.plain_text('<figure><figcaption>подпись</figcaption></figure><h2>Глава</h2><p>Текст <b>дальше</b>.</p>')
        self.assertIn('Глава',text)
        self.assertIn('Текст дальше.',text)
        self.assertNotIn('<',text)

    def test_local_address_only(self):
        for u in ('https://sloptotal.com', 'http://evil.com:8000',
                  'http://localhost:9000', 'http://127.0.0.1:8000/?url=https://evil.com'):
            with self.assertRaises(ValueError): m.check_local_url(u)
        self.assertEqual(m.check_local_url('http://localhost:8000'),'http://127.0.0.1:8000')

    def test_request_contains_full_text(self):
        def transport(req,timeout):
            self.assertEqual(req.full_url,'http://127.0.0.1:8000/api/analyze')
            self.assertEqual(json.loads(req.data)['text'], 'Текст статьи')
            return R(b'{"overall_score":43.5,"overall_verdict":"uncertain"}')
        result=m.analyze('Текст статьи','http://localhost:8000',transport=transport)
        self.assertEqual(m.compact_result(result)['overall_score_0_100'],43.5)

    def test_invalid_response_fails_closed(self):
        for data in ({},{'overall_score': True,'overall_verdict':'ai'},
                     {'overall_score':101,'overall_verdict':'ai'},
                     {'overall_score':25}):
            with self.assertRaises(ValueError): m.compact_result(data)

    def test_no_fake_probability_or_release(self):
        x=m.compact_result({'overall_score':12,'overall_verdict':'human','text':'private'})
        self.assertNotIn('text',x)
        self.assertNotIn('publication_authorized',x)

if __name__=='__main__': unittest.main()
